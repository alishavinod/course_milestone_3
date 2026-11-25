"""Section driver: walks the agent through the task's own sections, one at a time.

The text of every step is cut from the task description the agent was given; nothing is added to it. A step is
finished with `section-done`, which is refused until the agent has worked on the app and sent it enough requests.
A submit is refused until every step is done, or time or money is nearly used up. Standard library only.
"""

import json
import os
import re
import time
from pathlib import Path

APP = Path("/app")
COMMAND_PATH = Path("/usr/local/bin/section-done")
MARKER = "__SECTION_DONE_REQUESTED__"
SOURCE_SUFFIXES = {".py", ".sh", ".html", ".js", ".css", ".txt"}
DONE_REFUSALS_PER_STEP = 3  # then the step is let through, so a step can never trap the agent
SUBMIT_REFUSALS = 15
MIN_REQUESTS, MAX_REQUESTS = 3, 12
REVIEW, REVIEW_CHARS = "Final review", 8500

# Which task headings make up a step. Headings the task adds later become steps of their own.
STEPS = (
    ("Architecture and resilience", ("Overview", "REST conventions", "Health", "Resilience", "Dependencies", "Prohibited approaches")),
    ("Accounts", ("Auth", "Profiles")),
    ("Workspaces", ("Workspaces",)),
    ("Channels", ("Channels",)),
    ("Messages, threads, reactions", ("Messages, threads, reactions",)),
    ("DMs and files", ("DMs", "Files")),
    ("Search, slash commands, mentions, read state", ("Search, slash commands, mentions, read state",)),
    ("Groups, invitations, roles", ("User groups, invitations, roles",)),
    ("WebSocket and performance", ("WebSocket", "Performance")),
    ("IRC gateway", ("IRC gateway",)),
    ("Web UI", ("UI overview", "Frontend hooks")),
)  # fmt: skip
IGNORED = {
    "Caution and instructions to the LLM agent", "Caveats of mini-swe-agent (the harness you run in)",
    "Specs and existing code", "Process management — never kill by name", "Finish and verify before submitting",
    "API contract",
}  # fmt: skip


def split_task(task):
    """Returns {heading: text} in task order. The Overview's UI paragraph becomes 'UI overview'."""
    sections = {}
    for part in re.split(r"(?m)^(?=#{2,3} )", task):
        match = re.match(r"#{2,3} (.+)\n", part)
        if match:
            sections[match[1].strip()] = part[match.end() :].strip()
    paragraphs = sections.get("Overview", "").split("\n\n")
    ui = [p for p in paragraphs if p.startswith("The UI is graded")]
    if ui:
        sections["Overview"] = "\n\n".join(p for p in paragraphs if p not in ui)
        sections["UI overview"] = "\n\n".join(ui)
    return {k: v for k, v in sections.items() if v}


def build_steps(task):
    sections = split_task(task)
    steps, used = [], set()
    for title, headings in STEPS:
        found = [h for h in headings if h in sections]
        used.update(headings)
        if found:
            steps.append((title, "\n\n".join(f"### {h}\n\n{sections[h]}" for h in found)))
    steps += [(h, f"### {h}\n\n{t}") for h, t in sections.items() if h not in used | IGNORED]
    if not steps:
        return []
    # the review repeats every section, in parts that fit the harness's 10,000 character output limit
    parts = [[]]
    for _, text in steps:
        if parts[-1] and sum(map(len, parts[-1])) + len(text) > REVIEW_CHARS:
            parts.append([])
        parts[-1].append(text)
    return steps + [(f"{REVIEW} {i} of {len(parts)}", "\n\n".join(p)) for i, p in enumerate(parts, 1)]


def count_rules(text):
    return len(re.findall(r"(?m)^\s*- ", text))


def passive_opens():
    """TCP connections accepted in this container so far (one per HTTP request, WebSocket or IRC connection)."""
    try:
        rows = [r.split() for r in Path("/proc/net/snmp").read_text().splitlines() if r.startswith("Tcp:")]
        return int(rows[1][rows[0].index("PassiveOpens")])
    except (OSError, ValueError, IndexError):
        return 0


def app_state():
    """Changes when a source file under /app changes."""
    state = {}
    for root, dirs, files in os.walk(APP):
        dirs[:] = [d for d in dirs if d not in {"__pycache__", "node_modules", "venv", ".venv", ".git", "data"}]
        for name in files:
            path = Path(root, name)
            if path.suffix in SOURCE_SUFFIXES:
                try:
                    stat = path.stat()
                    state[str(path)] = (stat.st_mtime_ns, stat.st_size)
                except OSError:
                    pass
    return state


class SectionDriver:
    def __init__(self, open_after_seconds, open_after_cost, log_path):
        self.open_after, self.open_cost, self.log_path = open_after_seconds, open_after_cost, Path(log_path)
        self.steps, self.index, self.cost = [], 0, lambda: 0.0
        self.started, self.submit_refusals = time.time(), 0

    def begin(self, task, cost):
        """Called once, with the task text and a function that returns the money spent so far."""
        self.steps, self.cost, self.started, self.index = build_steps(task), cost, time.time(), 0
        self._new_step()
        self._log("begin", steps=[t for t, _ in self.steps])
        try:
            COMMAND_PATH.write_text(f"#!/bin/sh\necho {MARKER}\n")
            COMMAND_PATH.chmod(0o755)
        except OSError:
            pass

    @property
    def finished(self):
        return self.index >= len(self.steps)

    def _new_step(self):
        self.requests, self.done_refusals, self.state = 0, 0, app_state()

    def intro(self):
        return "" if self.finished else self._show()

    def _needed(self):
        text = self.steps[self.index][1]
        return min(MAX_REQUESTS, max(MIN_REQUESTS, count_rules(text)))

    def _left(self):
        steps = max(1, len(self.steps) - self.index)
        minutes = max(0.0, (self.open_after - (time.time() - self.started)) / 60)
        money = max(0.0, self.open_cost - self.cost())
        return f"about {minutes / steps:.0f} minutes and ${money / steps:.2f} for this step"

    def _show(self):
        title, text = self.steps[self.index]
        n = len(self.steps)
        if title.startswith(REVIEW):
            job = (
                "The sections below are repeated from earlier steps. Re-test each rule against the running app with real requests, "
                "including the error cases, and fix what is wrong or missing."
            )
        else:
            job = (
                "Build every rule of this section, then test each one with real requests, including its error cases "
                "and from different nodes. Keep what already works."
            )
        return (
            f"STEP {self.index + 1} of {n}: {title}\n{job}\n"
            f"When it works, run `section-done` (it asks for at least {self._needed()} requests to the app in this step). "
            f"You have {self._left()}.\n\n{text}"
        )

    def observe(self, opened):
        """Called after every command with the number of connections it made."""
        self.requests += max(0, opened)

    def rewrite(self, output):
        """Replaces the marker that `section-done` prints with the answer."""
        if MARKER not in output.get("output", ""):
            return
        output["output"] = output["output"].replace(MARKER, self.done(), 1).replace(MARKER, "")

    def done(self):
        if not self.steps or self.finished:
            return "All steps are done. Submit when you are ready."
        title = self.steps[self.index][0]
        changed, needed = app_state() != self.state, self._needed()
        required = needed if changed else 2 * needed
        if self.requests < required and self.done_refusals < DONE_REFUSALS_PER_STEP and not self._out_of_budget():
            self.done_refusals += 1
            self._log("done refused", step=title, requests=self.requests, required=required, changed=changed)
            why = "" if changed else " You also have not changed any file in /app during this step, so test more."
            return (
                f"section-done REFUSED for step {self.index + 1} ({title}): the app has received {self.requests} "
                f"request(s) in this step, at least {required} are needed.{why} Test every rule of the section, fix "
                "what fails, then run section-done again."
            )
        self._log("done", step=title, requests=self.requests, required=required, changed=changed, refusals=self.done_refusals)
        self.index += 1
        self._new_step()
        if self.finished:
            return "section-done accepted. All steps are done. Check that the cluster starts cleanly, then submit."
        return "section-done accepted.\n\n" + self._show()

    def _out_of_budget(self):
        return time.time() - self.started >= self.open_after or self.cost() >= self.open_cost

    def check_submit(self):
        """Returns None to allow the submit, or the text to show the agent."""
        if not self.steps or self.finished:
            reason = "all steps done"
        elif self._out_of_budget():
            reason = "time or money"
        elif self.submit_refusals >= SUBMIT_REFUSALS:
            reason = "refusal limit"
        else:
            self.submit_refusals += 1
            self._log("submit refused", step=self.steps[self.index][0])
            left = [t for t, _ in self.steps[self.index :]]
            return (
                f"SUBMIT REFUSED: {len(left)} of {len(self.steps)} steps are not done ({', '.join(left)}). "
                f"Nothing was submitted. Go on with this step.\n\n{self._show()}"
            )
        self._log("submit allowed", reason=reason)
        return None

    def _log(self, event, **more):
        if self.log_path.parent.is_dir():
            row = {"event": event, "elapsed": round(time.time() - self.started), "cost": round(self.cost(), 4)}
            try:
                with self.log_path.open("a") as f:
                    f.write(json.dumps(row | more) + "\n")
            except OSError:
                pass
