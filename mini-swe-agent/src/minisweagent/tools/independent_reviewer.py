"""Independent reviewer: when the agent submits, the app is reviewed by the same model in new, empty conversations.

One reviewer per section of the task. It is given the task text and nothing else: not the code, not the agent's
conversation, not the agent's tests. It writes test functions, one per numbered rule. The harness runs them (through
review_kit.py, which records the real requests and answers), sends the reviewer back when a rule has no real evidence,
repeats every failure, and has a second empty conversation judge whether the task text supports it. The failures that
remain go to the agent and the submit is refused. Holds no app code and no hand-written tests of the app.
"""

import ast
import json
import os
import re
import shutil
import subprocess
import tempfile
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from minisweagent.tools.section_driver import IGNORED, app_state, split_task
from minisweagent.tools.clients import PORTS, healthy, wait_until

KIT = Path(__file__).parent / "review_kit.py"
PYTHON = "/opt/venv/bin/python3" if Path("/opt/venv/bin/python3").exists() else "python3"
FINDINGS_PATH = Path("/tmp/review_findings.txt")
ROUND_CHARS = 8500  # the harness shows at most 10,000 characters of a command's output
MAX_REVIEWS = 2  # complete sets of reviewers, the second one only after the first is satisfied
MAX_ROUNDS = 25
FULL_EVERY = 5  # rounds between complete runs; the others repeat only the rules with open findings
STALLED_ROUNDS = 3
IDLE_SUBMITS = 6  # submits without a change before the agent is let go
WRITE_ATTEMPTS = 2
TESTS_LEFT_OUT = re.compile(r"/app\b|\bsubprocess\b|\bopen\(|\bos\.|\bimport os\b|__import__|/proc\b")

KIT_MISUSE = re.compile(r"'(Thing|Ws|IrcClient|Answer|Page)' object|unexpected keyword|positional argument")  # the test's own mistake

# Sections reviewed together. Headings the task adds later get a reviewer of their own.
GROUPS = (
    ("REST conventions", ("REST conventions", "Health")),
    ("Accounts and profiles", ("Auth", "Profiles")),
    ("Workspaces and roles", ("Workspaces",)),
    ("Channels", ("Channels",)),
    ("Messages, threads, reactions", ("Messages, threads, reactions",)),
    ("DMs and files", ("DMs", "Files")),
    ("Search, slash commands, mentions, read state", ("Search, slash commands, mentions, read state",)),
    ("Groups and invitations", ("User groups, invitations, roles",)),
    ("WebSocket and performance", ("WebSocket", "Performance")),
    ("IRC gateway", ("IRC gateway",)),
    ("Web UI behaviour", ("UI overview",)),
    ("Web UI hooks", ("Frontend hooks",)),
    ("Resilience and dependencies", ("Resilience", "Dependencies")),
)
SLOW = {"Web UI behaviour": 110, "Web UI hooks": 90, "Resilience and dependencies": 200, "WebSocket and performance": 100}
ALTERNATE = {*SLOW, "IRC gateway"}  # sections repeated only every second round
RESILIENCE = "Resilience and dependencies"
NOT_REVIEWED = IGNORED | {"Overview", "Prohibited approaches"}

KIT_GUIDE = """The test functions use these ready-made helpers. They are already defined; do not import or redefine them.
call(method, path, body=None, user=None, node=0, files=None, headers=None, raw=None) sends one HTTP request to node 0, 1 or 2 and returns an answer r with r.status, r.json, r.text, r.raw (bytes), r.headers (lower-case names), r.get("a.b.0.c") (None when missing, never raises) and r.list("messages") (always a list). `path` starts with /api and must have its query values URL-encoded. files={"file": ("a.txt", b"data", "text/plain")} sends multipart.
user() registers a new unique user and returns it with .id .name .token .password. workspace(owner) returns .id .slug .general (the id of #general) .owner. channel(owner, w, name=..., is_private=...) returns .id .name. member(w, u, role=None) adds u to the workspace w (made by workspace()). join(u, channel_id) joins a channel and returns the answer. message(u, channel_id, "text", parent_id=...) returns .id. These raise if the app does not give what the task promises.
ws(u, node=0) opens a WebSocket: .subscribe(channel_id), .send(dict), .wait(lambda f: ..., seconds=4) returns the frame or None, .frames, .close(). Where a frame holds the message is open, so look for a text with `text in json.dumps(f)`. It raises if the connection is refused (wrap it in try when a refusal is what you test).
irc() opens an IRC connection: .login(u, nick=None), .send(line), .wait("433") or .wait(lambda line: ...) returns the line or None, .lines, .close().
page(u=None) returns a Playwright sync page on the app at 1280x900, logged in when a user is given. tid("send-btn") is the selector of a data-testid. Give every wait a timeout of at most 4000 ms.
kill(port) SIGKILLs the process on a port. healthy(node) says whether a node answers. wait_until(lambda: ..., seconds). fresh_install() installs the app's requirements in a clean environment and returns (exit code, output).
time, json, re, secrets and threading are available.
check(what, got, want) records one result: `got` is an answer (its status is compared) or a value, `want` is a value, any_of(a, b) or a function of got. Always pass the observed value as got, never a constant."""


def rules_of(text):
    """The bullet points of a section, or its paragraphs when it has none."""
    rules = []
    for block in re.split(r"(?m)^(?=- )|\n\n", text):
        block = re.sub(r"\s+", " ", block.strip()).removeprefix("- ")
        if not block or block.startswith("###"):
            continue
        long = len(block) > 700 and block.count("; ") >= 4 and "- " not in text[:4]
        rules += [x.strip() for x in block.split("; ")] if long and "###" in text and text.count("\n- ") == 0 else [block]
    return rules


def min_checks(rule):
    """How many real checks a rule needs: one per sentence, between 1 and 6."""
    return max(1, min(6, len(re.split(r"(?<=[.;])\s+(?=[A-Z`*])", rule))))


def build_sections(task):
    sections, used, out = split_task(task), set(), []
    for title, headings in GROUPS:
        used.update(headings)
        text = "\n\n".join(f"### {h}\n\n{sections[h]}" for h in headings if h in sections)
        if text:
            out.append((title, text, rules_of(text)))
    return out + [(h, t, rules_of(t)) for h, t in sections.items() if h not in used | NOT_REVIEWED]


class Review:
    """One reviewer's tests for one section."""

    def __init__(self, title, text, rules):
        self.title, self.text, self.rules = title, text, rules
        self.source, self.functions, self.weak, self.tests = "", {}, set(), 0
        self.incomplete = True  # not yet run to its end
        self.open, self.passed = {}, {}  # (rule, what) -> confirmed failure; rule -> checks that passed
        self.verdicts = {}  # (rule, what) -> True if the task text supports the failure


class IndependentReviewer:
    def __init__(self, open_after_seconds, open_after_cost, log_path):
        self.open_after, self.open_cost, self.log_path = open_after_seconds, open_after_cost, Path(log_path)
        self.started, self.task, self.sections = time.time(), "", []
        self.cost, self.ask = lambda: 0.0, None
        self.reviews, self.sets, self.rounds, self.idle = [], 0, 0, 0
        self.passed = self.early = 0
        self.state, self.last_text, self.best, self.stalled, self.shown = None, "", None, 0, {}

    def begin(self, task, cost, ask):
        """`ask(messages)` sends a conversation of its own to the model and returns the text of the answer."""
        self.task, self.cost, self.ask, self.started = task, cost, ask, time.time()
        self.sections = build_sections(task)
        self._log("begin", sections={t: len(r) for t, _, r in self.sections})

    # ---------- the decision ----------
    def _over(self, margin=0.0):
        return time.time() - self.started >= self.open_after - 300 * margin or self.cost() >= self.open_cost - margin

    def check_submit(self):
        """Returns None to allow the submit, or the text to show the agent. A fault in here never blocks a submit."""
        try:
            return self._check()
        except Exception as e:
            self._log("submit allowed", reason=f"reviewer error {type(e).__name__}: {e}")
            return None

    def _allow(self, reason):
        self._log("submit allowed", reason=reason, reviews=self.sets)

    def _check(self):
        if not self.sections or not self.ask:
            return self._allow("no sections")
        if self._over():
            return self._allow("time or money")
        if self.rounds >= MAX_ROUNDS:
            return self._allow("round limit")
        if self.last_text and app_state() == self.state:
            self.idle += 1
            if self.idle >= IDLE_SUBMITS:
                return self._allow("no change in several submits")
            return "SUBMIT REFUSED: no file in /app changed since the last review. Fix these findings first.\n\n" + self.last_text
        self.idle, self.rounds = 0, self.rounds + 1
        self._start_if_down()
        if not any(healthy(p) for p in PORTS):
            self.state, self.last_text = app_state(), (
                "SUBMIT REFUSED: /app/start.sh does not bring up a node that answers GET /api/health within 25 seconds. "
                "Make the cluster start, then submit again."
            )
            return self.last_text
        findings = self._run_all(full=self.rounds % FULL_EVERY == 0) if self.reviews else []
        if self.reviews and not findings and not self._over():  # every open finding is fixed: is anything broken by the fixes?
            findings = self._run_all(full=True)
        if findings:
            if self.rounds % 2 == 0:
                pass  # the slow sections were not repeated
            elif self.best and len(findings) >= self.best[0] and self.passed <= self.best[1]:
                self.stalled += 1
            else:  # fewer findings, or more checks pass (a fix can let a test reach its later checks)
                self.stalled, self.best = 0, (len(findings), self.passed)
            if self.stalled >= STALLED_ROUNDS and not any(r.incomplete for r in self.reviews):
                return self._allow("findings no longer go down")
            return self._refuse(findings)
        while self.sets < MAX_REVIEWS and not self._over(0.15):
            self.sets += 1
            self._write_reviews()
            findings = self._run_all(full=True)
            if findings:
                self.best, self.stalled = (len(findings), self.passed), 0
                return self._refuse(findings)
        return self._allow("no findings left")

    # ---------- writing the tests ----------
    def _contract(self):
        sections = split_task(self.task)
        return "\n\n".join(f"### {h}\n\n{t}" for h, t in sections.items() if h not in IGNORED)

    def _write_reviews(self):
        covered = {}
        for r in self.reviews:
            covered.setdefault(r.title, []).extend(sorted({what for _, what in r.verdicts}))
        new = [Review(*s) for s in self.sections]
        with ThreadPoolExecutor(16) as pool:
            list(pool.map(lambda r: self._guarded(self._write, r, covered.get(r.title, [])), new))
        kept = [r for r in new if r.functions]
        self.reviews = self.reviews + kept
        self._log("reviews written", set=self.sets, sections={r.title: r.tests for r in kept},
                  weak_rules={r.title: sorted(r.weak) for r in kept if r.weak})  # fmt: skip

    def _guarded(self, fn, review, *args):
        try:
            fn(review, *args)
        except Exception as e:
            self._log("review fault", section=review.title, step=fn.__name__, error=f"{type(e).__name__}: {e}"[:300])

    def _write(self, review, covered):
        numbered = "\n".join(f"RULE {i} (at least {min_checks(r)} checks): {r}" for i, r in enumerate(review.rules, 1))
        again = ""
        if self.sets > 1:
            again = (
                "\n\nAn earlier reviewer already tested this section. Look for what they would miss: combinations of rules, boundary "
                "values, other roles, the state after the request, and requests sent to a different node."
            )
            if covered:
                again += " They already reported: " + "; ".join(covered)[:1500]
        prompt = (
            "You are an independent reviewer of a chat server. Another programmer built it. You cannot see their code; judge it only by "
            "the task text. Find where the running app breaks the rules below.\n\n"
            f"<task_text>\n{self._contract()}\n</task_text>\n\n"
            f"Review this section: {review.title}\n{numbered}{again}\n\n{KIT_GUIDE}\n\n"
            "Write one Python script with one function per rule, decorated with its number:\n\n"
            "@rule(1)\ndef short_name():\n    u = user()\n    r = call(\"GET\", \"/api/auth/me\", user=u)\n"
            "    check(\"GET /api/auth/me returns the caller's id\", r.get(\"user.id\"), u.id)\n\n"
            "Each function stands alone and makes its own users and workspaces. Test every clause of the rule: the normal case, every error "
            "case and status code it names, who may and may not do it, and that the effect is really there afterwards (also read from another "
            "node). Test only what the task text states; where it leaves something open, accept every reading. Ids may be numbers or strings, so "
            "compare them with str(). Read answers with r.get and r.list. `what` must say in words what was done and what should happen, "
            "so that a person who cannot see the test understands it; do not put random names or ids in it. Use many small checks. Do not read files or start programs. Keep each "
            "function under 15 seconds. Answer with one ```python block and nothing else."
        )
        messages = [{"role": "user", "content": prompt}]
        best = None
        for attempt in range(WRITE_ATTEMPTS):
            reply = self.ask(messages)
            problems, weak, functions, source, tests = self._inspect(review, reply)
            if functions and (best is None or len(weak) < len(best[0])):
                best = (weak, functions, source, tests)
            if not problems or attempt == WRITE_ATTEMPTS - 1 or self._over(0.1):
                break
            messages += [
                {"role": "assistant", "content": reply},
                {"role": "user", "content": "The harness rejects this review:\n" + "\n".join(problems[:20])
                 + "\n\nAnswer with the complete corrected script in one ```python block."},
            ]  # fmt: skip
        if best:
            review.weak, review.functions, review.source, review.tests = best
        self._log("review", section=review.title, attempts=attempt + 1, tests=review.tests, weak=sorted(review.weak), script=review.source)

    def _inspect(self, review, reply):
        """Returns (problems, rules without enough evidence, {rule: source}, script, number of tests)."""
        everything = set(range(1, len(review.rules) + 1))
        match = re.search(r"```(?:python)?\s*\n(.*?)\n```", reply, re.S)
        source = match[1] if match else reply
        try:
            tree = ast.parse(source)
        except SyntaxError as e:
            return [f"It does not parse: {e}"], everything, {}, "", 0
        functions = {}
        for node in tree.body:
            for d in getattr(node, "decorator_list", []):
                if isinstance(d, ast.Call) and getattr(d.func, "id", "") == "rule" and d.args and isinstance(d.args[0], ast.Constant):
                    functions[d.args[0].value] = functions.get(d.args[0].value, "") + ast.get_source_segment(source, node) + "\n"
        banned = TESTS_LEFT_OUT.search(source)
        if banned:
            return [f"It contains `{banned[0]}`. Use only the helpers; do not read files or start programs."], everything, {}, "", 0
        # a check that passes with no app at all, whatever it answers, is no evidence
        runs = [self._run(source, mode, 25) for mode in ("stub200", "stub404")]
        problems = [f"It does not run: {row['load_error']}" for rows in runs for row in rows if "load_error" in row][:1]
        if problems:
            return problems, everything, {}, "", 0
        for rows in runs:
            for row in rows:
                if row.get("error") == "NameError" or ("error" in row and KIT_MISUSE.search(str(row["got"]))):
                    problems.append(f"Rule {row['rule']}: {row['error']}: {row['got']}")
        passed = [{(r["rule"], r["what"]) for r in rows if r.get("ok")} for rows in runs]
        empty = passed[0] & passed[1]
        called, weak = self._calls(tree), set()
        for n in sorted(everything):
            found = called.get(n, 0) - len([1 for k in empty if k[0] == n])
            if n not in functions:
                problems.append(f"Rule {n} has no test function.")
                weak.add(n)
            elif found < min_checks(review.rules[n - 1]):
                bad = [what for k, what in empty if k == n][:3]
                problems.append(
                    f"Rule {n} has {found} check(s) with evidence, {min_checks(review.rules[n - 1])} are needed."
                    + (f" These pass even without an app: {bad}." if bad else "")
                )
                weak.add(n)
        return problems, weak, functions, source, sum(called.values())

    @staticmethod
    def _calls(tree):
        """{rule: how many check() calls its functions contain}. A check in a loop over a literal counts once per item."""
        counts = {}

        def walk(node, times):
            total = times if isinstance(node, ast.Call) and getattr(node.func, "id", "") == "check" else 0
            if isinstance(node, ast.For) and isinstance(node.iter, ast.Tuple | ast.List):
                times *= max(1, len(node.iter.elts))
            return total + sum(walk(c, times) for c in ast.iter_child_nodes(node))

        for node in tree.body:
            for d in getattr(node, "decorator_list", []):
                if isinstance(d, ast.Call) and getattr(d.func, "id", "") == "rule" and d.args and isinstance(d.args[0], ast.Constant):
                    counts[d.args[0].value] = counts.get(d.args[0].value, 0) + walk(node, 1)
        return counts

    # ---------- running them ----------
    def _run(self, source, mode, seconds, rules=None):
        tmp = tempfile.mkdtemp(prefix="rv_")
        try:
            script, out = Path(tmp, "probe.py"), Path(tmp, "out.jsonl")
            script.write_text(source, encoding="utf-8")
            keep = ("PATH", "HOME", "LANG", "PLAYWRIGHT_BROWSERS_PATH", "TMPDIR")  # no API key reaches a test
            env = {k: os.environ[k] for k in keep if k in os.environ} | {"PYTHONDONTWRITEBYTECODE": "1", "PYTHONIOENCODING": "utf-8"}
            process = subprocess.Popen(
                [PYTHON, str(KIT), str(script), str(out), mode, *([",".join(map(str, sorted(rules)))] if rules else [])], cwd=tmp, env=env, stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, start_new_session=True,
            )  # fmt: skip
            try:
                process.wait(seconds)
            except subprocess.TimeoutExpired:
                os.killpg(process.pid, 9)
                process.wait()
            rows = []
            for line in out.read_text(encoding="utf-8").splitlines() if out.exists() else []:
                try:
                    rows.append(json.loads(line))
                except ValueError:
                    pass
            return rows
        finally:
            shutil.rmtree(tmp, ignore_errors=True)

    def _failures(self, review, rules=None):
        rows = self._run(review.source, "real", SLOW.get(review.title, 60), rules)
        rows = [r for r in rows if "what" in r and r.get("error") not in ("NameError", "Stalled") and not KIT_MISUSE.search(str(r["got"]) if "error" in r else "")]
        for n in rules or {r["rule"] for r in rows} | set(review.passed):
            review.passed[n] = sum(1 for r in rows if r["ok"] and r["rule"] == n)
        return {(r["rule"], r["what"]): r for r in rows if not r["ok"]}

    def _run_all(self, full):
        """Runs the reviews against the app. Returns [(review, rule, row)] for the confirmed failures.

        Not full: only the rules with open findings, and the slow sections only every second round."""
        quick = any(r.incomplete or r.open for r in self.reviews if r.title not in ALTERNATE)
        began, ran, timing, stalled = time.time(), [], {}, 0
        for review in self.reviews:
            rules = None if full or review.incomplete else {k[0] for k in review.open if isinstance(k[0], int)}
            if not full and not review.incomplete and (not rules or (review.title in ALTERNATE and self.rounds % 2 == 0 and quick)):
                continue
            if self._over():
                break
            at = time.time()
            first = self._failures(review, rules)
            stalls = {k: v for k, v in first.items() if v.get("stall")}
            review.incomplete = bool(stalls)
            if stalls:  # measured, and each one takes a minute: not repeated
                first, stalled = stalls, stalled + 1
            elif first and review.title not in SLOW:  # must fail twice
                second = self._failures(review, {k[0] for k in first})
                first = {k: v for k, v in second.items() if k in first}
            ran.append((review, first))
            wait_until(lambda: all(healthy(p) for p in PORTS), 20 if review.title == RESILIENCE else 3)
            self._start_if_down()
            timing[review.title] = round(time.time() - at)
            if stalled >= 2 and self.early < 2:  # nearly every later test would only show the same stall
                self.early += 1
                break
        with ThreadPoolExecutor(16) as pool:
            list(pool.map(lambda x: self._guarded(self._judge, x[0], x[1]), ran))
        for review, rows in ran:
            hidden = review.open if any(v.get("stall") for v in rows.values()) else {}
            hidden = {k: v for k, v in hidden.items() if not v.get("stall")}
            review.open = hidden | {k: v for k, v in rows.items() if review.verdicts.get(k)}
        findings, stalls = [], set()
        for review in self.reviews:
            for k, row in review.open.items():
                if not (row.get("stall") and row["what"] in stalls):
                    findings.append((review, k[0], row))
                if row.get("stall"):
                    stalls.add(row["what"])
        self.passed = sum(sum(r.passed.values()) for r in self.reviews)
        self._log(
            "round", round=self.rounds, set=self.sets, full=full, ran=[r.title for r, _ in ran], timing=timing, passed=self.passed,
            failed=sum(len(x) for _, x in ran), confirmed=len(findings), seconds=round(time.time() - began),
            findings=[[r.title, n, row["what"], row["got"], row["want"]] for r, n, row in findings],
        )  # fmt: skip
        return findings

    def _judge(self, review, failures):
        for k, v in failures.items():
            if v.get("stall"):  # measured by the harness
                review.verdicts[k] = True
        new = [(k, v) for k, v in failures.items() if k not in review.verdicts][:40]
        if not new:
            return
        listed = "\n\n".join(
            f"FAILURE {i}\nrule {k[0]}: {review.rules[k[0] - 1] if isinstance(k[0], int) and 0 < k[0] <= len(review.rules) else ''}\n"
            f"check: {k[1]}\ngot: {v['got']}   wanted: {v['want']}\nlast requests:\n" + "\n".join(f"  {e}" for e in v["evidence"])
            for i, (k, v) in enumerate(new, 1)
        )
        code = "\n".join(review.functions.get(n, "") for n in sorted({k[0] for k, _ in new}, key=str))
        prompt = (
            f"Tests of a chat server failed. The server was built from this task text.\n\n<task_text>\n{self._contract()}\n</task_text>\n\n"
            f"<tests>\n{code}\n</tests>\n\n{listed}\n\nFor each failure decide: APP if the recorded requests show that the app breaks a statement "
            "of the task text, or TEST if the test is wrong: it has a mistake, demands something the text does not state, or sends a request the "
            "text does not describe. A missing route or feature the text asks for is APP, also when it stopped the test early. Be strict: "
            "if the text allows what the app did, it is TEST. Answer with one line per failure, like `1 APP` or `2 TEST`, and nothing else."
        )
        verdicts = dict(re.findall(r"(?m)^\W*(?:FAILURE )?(\d+)\W+(APP|TEST)\b", self.ask([{"role": "user", "content": prompt}])))
        for i, (k, _) in enumerate(new, 1):
            if str(i) in verdicts:
                review.verdicts[k] = verdicts[str(i)] == "APP"
        self._log("judged", section=review.title, verdicts=[[k[0], k[1], review.verdicts.get(k)] for k, _ in new])

    # ---------- telling the agent ----------
    def _refuse(self, findings):
        groups = {}
        for review, n, row in findings:
            groups.setdefault((review.title, n), (review, []))[1].append(row)
        stall = {(r.title, n) for r, n, row in findings if row.get("stall")}
        order = {t: i for i, (t, _, _) in enumerate(self.sections)}
        keys = sorted(groups, key=lambda k: (k not in stall, self.shown.get(k, 0) >= 2, k[0] != RESILIENCE, order.get(k[0], 99), str(k[1]).zfill(3)))
        blocks = []
        for key in keys:
            review, rows = groups[key]
            rows = sorted(rows, key=lambda row: not row.get("stall"))
            text = review.rules[key[1] - 1] if isinstance(key[1], int) and 0 < key[1] <= len(review.rules) else ""
            block = f"[{key[0]}] {text[:200]}{'...' if len(text) > 200 else ''}\n"
            seen = set()
            for row in rows:
                if row["what"] not in seen and len(seen) < 6:
                    seen.add(row["what"])
                    block += f"  - {row['what']}: got {row['got']}, expected {row['want']}\n"
            evidence = rows[0]["evidence"][-1:]
            if rows[0].get("stall"):
                block = f"[APP STALLS] seen in: {key[0]}\n" + "".join(f"  - {x['what']}\n" for x in rows if x.get("stall"))
                block += "".join(f"    request: {e[:220]}\n" for e in rows[0]["evidence"])
            else:
                block += f"    last request: {evidence[0][:300]}\n" if evidence else ""
            blocks.append((key, block))
        head = (
            f"SUBMIT REFUSED. An independent reviewer, who only has the task text, tested the running app: {len(findings)} check(s) failed "
            f"under {len(groups)} rule(s). Nothing was submitted.\n"
        )
        tail = (
            f"\nAll of it is in {FINDINGS_PATH}. Fix every finding in the code of the app, restart the cluster, test the fix yourself, and submit "
            "again: the app is then reviewed again. Fix many findings per command. If a finding contradicts the task description, leave it. "
            f"About {max(0, int((self.open_after - time.time() + self.started) // 60))} minutes and ${max(0, self.open_cost - self.cost()):.2f} are left."
        )
        body, left = "", 0
        for key, block in blocks:
            if len(head) + len(body) + len(block) + len(tail) < ROUND_CHARS:
                body += block
                self.shown[key] = self.shown.get(key, 0) + 1
            else:
                left += 1
        if left:
            body += f"... and {left} more rule(s), see the file.\n"
        try:
            FINDINGS_PATH.write_text("".join(b for _, b in blocks), encoding="utf-8")
        except OSError:
            pass
        self.state, self.last_text = app_state(), head + body + tail
        self._log("submit refused", findings=len(findings), rules=len(groups))
        return self.last_text

    def _start_if_down(self):
        if any(healthy(p) for p in PORTS) or not Path("/app/start.sh").is_file():
            return
        with open("/tmp/review_start.log", "wb") as log:
            try:
                subprocess.Popen(["/app/start.sh"], cwd="/app", stdin=subprocess.DEVNULL, stdout=log, stderr=log, start_new_session=True)
            except OSError:
                return
        wait_until(lambda: all(healthy(p) for p in PORTS), 25)

    _lock = threading.Lock()

    def _log(self, event, **more):
        if self.log_path.parent.is_dir():
            row = {"event": event, "elapsed": round(time.time() - self.started), "cost": round(self.cost(), 4)}
            try:
                with self._lock, self.log_path.open("a", encoding="utf-8") as f:
                    f.write(json.dumps(row | more, ensure_ascii=False) + "\n")
            except OSError:
                pass
