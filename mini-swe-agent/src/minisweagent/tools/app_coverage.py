"""Coverage report: which routes and UI hooks named in the task description the running app serves.

It checks existence only, never correctness, and it never blocks anything. It observes the app over HTTP
and contains no app code. Standard library only.
"""

import json
import re
import secrets
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

BASE = "http://127.0.0.1:8000"
DEADLINE_SECONDS = 12
NOT_SERVED = (404, 405, 501)
COMMAND_PATH = Path("/usr/local/bin/app-coverage")

# Every data-testid that the task says is in the initial HTML (data-dependent ones are left out).
TEST_IDS = (
    "auth-form auth-submit auth-toggle workspace-create-form empty-state-create-workspace join-workspace-form "
    "join-workspace-submit channel-list dms-list new-channel-btn current-user logout-btn workspace-header "
    "workspace-settings-btn workspace-settings-close workspace-name-input workspace-join-mode "
    "workspace-general-submit channel-title channel-topic channel-settings-btn channel-settings-title "
    "channel-settings-close channel-members-list channel-add-member-form channel-add-member-input "
    "channel-add-member-submit channel-topic-input channel-topic-submit archive-channel-btn unarchive-channel-btn "
    "create-channel-form create-channel-private create-channel-submit create-channel-cancel message-list "
    "message-input send-btn thread-panel thread-input thread-send close-thread settings-tab-members "
    "settings-tab-invitations settings-tab-general settings-pane-members settings-pane-invitations "
    "settings-pane-general create-invitation-btn create-invitation-form create-invitation-submit invitations-list"
).split()
DOM_IDS = "auth-modal create-channel-modal channel-settings-modal workspace-settings-modal create-invitation-form".split()


class Probe:
    def __init__(self):
        self.end = time.time() + DEADLINE_SECONDS
        self.served, self.missing, self.errors, self.unknown = [], [], [], []

    def call(self, method, path, body=None, token=None, raw=None, content_type="application/json"):
        """Returns (status, json-or-None). Status 0 means no answer."""
        left = self.end - time.time()
        if left <= 0:
            return 0, None
        data = raw if raw is not None else (None if body is None else json.dumps(body).encode())
        req = urllib.request.Request(BASE + path, method=method, data=data)
        if data is not None:
            req.add_header("Content-Type", content_type)
        if token:
            req.add_header("Authorization", f"Bearer {token}")
        try:
            with urllib.request.urlopen(req, timeout=min(2, left)) as r:
                status, text = r.status, r.read()
        except urllib.error.HTTPError as e:
            status, text = e.code, e.read()
        except Exception:
            return 0, None
        try:
            return status, json.loads(text)
        except ValueError:
            return status, None

    def route(self, method, template, path, body=None, token=None, **kw):
        """Sends one request and records whether the route exists. `path` None means a route it needs is missing."""
        name = f"{method} {template}"
        if path is None:
            self.unknown.append(name)
            return None
        status, data = self.call(method, path, body, token, **kw)
        if status in NOT_SERVED:
            self.missing.append(name)
        elif status == 0 or status >= 500:
            self.errors.append(f"{name} ({'no answer' if status == 0 else status})")
        else:
            self.served.append(name)
        return data

    @staticmethod
    def get(data, *keys):
        for key in keys:
            data = data.get(key) if isinstance(data, dict) else None
        return data

    def routes(self):
        get = self.get
        r, rnd = self.route, secrets.token_hex(4)

        def at(template, value, **more):
            """Fills the ids into a route template, or None if an id is not known."""
            values = {"id": value, **more}
            if any(v is None for v in values.values()):
                return None
            return re.sub(r"\{(\w+)\}", lambda m: str(values.get(m.group(1), value)), template)

        # accounts
        r("GET", "/api/health", "/api/health")
        users = []
        for i in range(2):
            name, pw = f"cov{rnd}{i}", "cov-password-1"
            if i == 0:
                data = r("POST", "/api/auth/register", "/api/auth/register", {"username": name, "password": pw})
            else:
                data = self.call("POST", "/api/auth/register", {"username": name, "password": pw})[1]
            users.append((name, pw, get(data, "token"), get(data, "user", "id")))
        (name, pw, tok, uid), (name2, _, tok2, uid2) = users
        r("POST", "/api/auth/login", "/api/auth/login", {"username": name, "password": pw})
        if not tok:
            return
        r("GET", "/api/auth/me", "/api/auth/me", token=tok)
        t = "/api/users/{id}"
        r("GET", t, at(t, uid), token=tok)
        r("PATCH", "/api/users/me", "/api/users/me", {"status_text": "covered"}, tok)

        # workspaces
        slug = f"cov-{rnd}"
        data = r("POST", "/api/workspaces", "/api/workspaces", {"slug": slug, "name": "Coverage"}, tok)
        gen = get(data, "general_channel", "id")
        slug = slug if gen is not None else None
        r("GET", "/api/workspaces", "/api/workspaces", token=tok)
        w = "/api/workspaces/{slug}"
        r("GET", w, at(w, slug), token=tok)
        r("GET", w + "/members", at(w + "/members", slug), token=tok)
        r("PATCH", w, at(w, slug), {"name": "Covered"}, tok)
        c = "/api/channels/{id}"
        r("POST", c + "/join", at(c + "/join", gen), {}, tok2)
        m = w + "/members/{user_id}"
        r("PATCH", m, at(m, slug, user_id=uid2), {"role": "admin"}, tok)

        # channels and messages
        data = r("POST", w + "/channels", at(w + "/channels", slug), {"name": f"cov-{rnd}"}, tok)
        cid = get(data, "channel", "id")
        cid = gen if cid is None else cid
        r("PATCH", c, at(c, cid), {"topic": "covered"}, tok)
        r("GET", c + "/members", at(c + "/members", cid), token=tok)
        data = r("POST", c + "/messages", at(c + "/messages", cid), {"body": "coverage"}, tok)
        mid = get(data, "message", "id")
        r("GET", c + "/messages", at(c + "/messages", cid), token=tok)
        g = "/api/messages/{id}"
        r("PATCH", g, at(g, mid), {"body": "coverage edited"}, tok)
        r("GET", g + "/replies", at(g + "/replies", mid), token=tok)
        r("POST", g + "/reactions", at(g + "/reactions", mid), {"emoji": "x"}, tok)
        r("DELETE", g + "/reactions/{emoji}", at(g + "/reactions/x", mid), token=tok)
        r("POST", g + "/pin", at(g + "/pin", mid), {}, tok)
        r("GET", c + "/pins", at(c + "/pins", cid), token=tok)
        r("DELETE", g + "/pin", at(g + "/pin", mid), token=tok)
        r("GET", c + "/read", at(c + "/read", cid), token=tok)
        r("POST", c + "/read", at(c + "/read", cid), {"last_read_event_id": 1}, tok)
        r("DELETE", g, at(g, mid), token=tok)
        r("GET", "/api/search", "/api/search?q=coverage", token=tok)
        r("POST", "/api/dms", "/api/dms" if uid2 is not None else None, {"user_id": uid2}, tok)

        # files
        b = "cov" + rnd
        part = (
            f'--{b}\r\nContent-Disposition: form-data; name="file"; filename="c.txt"\r\n'
            f"Content-Type: text/plain\r\n\r\ncoverage\r\n--{b}--\r\n"
        ).encode()
        data = r("POST", "/api/files", "/api/files", token=tok, raw=part, content_type=f"multipart/form-data; boundary={b}")
        fid = get(data, "file", "id")
        f = "/api/files/{id}"
        r("GET", f, at(f, fid), token=tok)
        r("GET", f + "/download", at(f + "/download", fid), token=tok)

        # groups
        handle = f"cov{rnd}"
        body = {"handle": handle, "name": "Coverage", "user_ids": [uid]}
        data = r("POST", w + "/groups", at(w + "/groups", slug), body, tok)
        handle = get(data, "group", "handle")
        r("GET", w + "/groups", at(w + "/groups", slug), token=tok)
        h = w + "/groups/{handle}"
        r("PATCH", h, at(h, slug, handle=handle), {"name": "Covered"}, tok)
        r("DELETE", h, at(h, slug, handle=handle), token=tok)

        # invitations
        inv = w + "/invitations"
        data = r("POST", inv, at(inv, slug), {"max_uses": 5}, tok)
        code = get(data, "invitation", "code")
        r("GET", inv, at(inv, slug), token=tok)
        a = "/api/invitations/{code}/accept"
        r("POST", a, at(a, code, code=code), {}, tok2)
        r("DELETE", inv + "/{code}", at(inv + "/{code}", slug, code=code), token=tok)

        # last, because they change who can do what
        r("POST", c + "/leave", at(c + "/leave", gen), {}, tok2)
        r("POST", c + "/archive", at(c + "/archive", cid), {}, tok)
        r("POST", c + "/unarchive", at(c + "/unarchive", cid), {}, tok)
        o = w + "/transfer_ownership"
        r("POST", o, at(o, slug), {"new_owner_id": uid2}, tok)

    def ui(self):
        """Returns the missing UI hooks, or None if the page is not served."""
        try:
            with urllib.request.urlopen(BASE + "/", timeout=2) as r:
                html = r.read().decode("utf-8", "replace")
        except Exception:
            return None

        def has(attr, value):
            return re.search(rf"""[\s"']{attr}\s*=\s*(["']{re.escape(value)}["']|{re.escape(value)}(?=[\s>/]))""", html)

        return [v for v in TEST_IDS if not has("data-testid", v)] + [f"#{v}" for v in DOM_IDS if not has("id", v)]


def measure():
    probe = Probe()
    if probe.call("GET", "/api/health")[0] == 0:
        return {"up": False}
    probe.routes()
    missing_ui = probe.ui()
    total_ui = len(TEST_IDS) + len(DOM_IDS)
    return {
        "up": True,
        "rest_total": len(probe.served) + len(probe.missing) + len(probe.errors) + len(probe.unknown),
        "served": probe.served,
        "missing": probe.missing,
        "errors": probe.errors,
        "unknown": probe.unknown,
        "ui_total": total_ui,
        "ui_missing": missing_ui if missing_ui is not None else ["GET / is not served"],
        "ui_served": 0 if missing_ui is None else total_ui - len(missing_ui),
    }


def report(m, limit=None):
    """The text shown to the agent. `limit` shortens each list."""
    if not m["up"]:
        return "App coverage: nothing answers on 127.0.0.1:8000, so nothing could be measured."

    def part(label, items):
        if not items:
            return []
        more = f" ... and {len(items) - limit} more" if limit and len(items) > limit else ""
        return [f"{label}: " + ", ".join(items[:limit]) + more]

    return "\n".join(
        [
            f"App coverage: REST {len(m['served'])}/{m['rest_total']} routes, UI {m['ui_served']}/{m['ui_total']} hooks in the page.",
            *part("Not served", m["missing"]),
            *part("Failing (no answer or 5xx)", m["errors"]),
            *part("Not checked, because a route it needs is missing", m["unknown"]),
            *part("UI hooks missing from GET /", m["ui_missing"]),
            "Served means the route exists, not that it is correct. WebSocket, IRC, Redis and resilience are not measured here.",
        ]
    )


def log(source, m, path):
    path = Path(path)
    if path.parent.is_dir():
        row = {"source": source, "time": round(time.time()), **{k: v for k, v in m.items() if k != "served"}}
        row["served"] = len(m.get("served", []))
        try:
            with path.open("a") as f:
                f.write(json.dumps(row) + "\n")
        except OSError:
            pass


class Coverage:
    """Adds a short report to every Nth command's output, and installs the `app-coverage` command."""

    def __init__(self, every, log_path):
        self.every, self.log_path, self.commands = every, log_path, 0
        try:
            COMMAND_PATH.write_text(f'#!/bin/sh\nexec "{sys.executable}" -I "{Path(__file__).resolve()}" "{log_path}"\n')
            COMMAND_PATH.chmod(0o755)
        except OSError:
            pass

    def after_command(self):
        """Returns text to append to this command's output, or an empty string."""
        self.commands += 1
        if self.commands % self.every:
            return ""
        try:
            m = measure()
            log("automatic", m, self.log_path)
            return f"\n\n[automatic, every {self.every} commands; run `app-coverage` for the full list]\n{report(m, limit=8)}\n"
        except Exception:  # a bug here must never break the run
            return ""


def main(log_path):
    m = measure()
    log("command", m, log_path)
    print(report(m))


if __name__ == "__main__":
    main(sys.argv[1] if len(sys.argv) > 1 else "/logs/agent/app_coverage.jsonl")
