"""Runs one test script written by the independent reviewer, and records what really happened.

Usage: review_kit.py <script> <results.jsonl> <real|stub200|stub404> [rule,rule,...]

Every request goes through this file, so the evidence for a result is recorded here and not claimed by the script.
In the stub modes there is no app: every request "answers" 200 {} or 404 {}. A check that passes in both proves
nothing about an app. It holds generic clients only, no app code. Standard library only (a browser test uses Playwright).
"""

import json
import os
import re
import secrets
import subprocess
import sys
import tempfile
import threading
import time
import urllib.error
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
import clients as base  # noqa: E402

TIMEOUT = 6


class Unavailable(Exception):
    """The app did not give what a test needs to go on."""


class Stalled(Unavailable):
    """The app gave no answer in time. Reported at once, with the request that came before."""


class State:
    mode, out, exchanges, rule = "real", None, [], None
    killed, probes, stalls = False, 0, 0


def short(value, n=160):
    text = value if isinstance(value, str) else json.dumps(value, ensure_ascii=False, default=repr)
    return text if len(text) <= n else text[:n] + "..."


def note(text):
    State.exchanges.append(text)


def emit(row):
    State.out.write(json.dumps(row, ensure_ascii=False, default=repr) + "\n")
    State.out.flush()


class Answer:
    timed_out = False

    def __init__(self, status, raw=b"", headers=None):
        self.status, self.raw, self.headers = status, raw, {k.lower(): v for k, v in (headers or {}).items()}
        self.text = raw.decode("utf-8", "replace")
        try:
            self.json = json.loads(raw)
        except ValueError:
            self.json = None

    def get(self, path, default=None):
        """get("message.reactions.0.count"): walks dicts and lists, returns default if anything is missing."""
        node = self.json
        for part in str(path).split("."):
            if isinstance(node, dict) and part in node:
                node = node[part]
            elif isinstance(node, list) and re.fullmatch(r"-?\d+", part) and -len(node) <= int(part) < len(node):
                node = node[int(part)]
            else:
                return default
        return node

    def list(self, path):
        value = self.get(path)
        return value if isinstance(value, list) else []

    def __repr__(self):
        return f"status {self.status}"


def token_of(who):
    return getattr(who, "token", who)


def call(method, path, body=None, user=None, node=0, files=None, headers=None, raw=None):
    if State.mode != "real":
        answer = Answer(200 if State.mode == "stub200" else 404, b"{}")
        note(f"{method} {path} -> stub")
        return answer
    if State.mode == "real":
        answer = send(method, path, body, user, node, files, headers, raw)
        if answer.timed_out and not State.killed:
            raise stalled(f"{method} {shape(path)} got no answer within {TIMEOUT} seconds")
        if method != "GET" and 400 <= answer.status < 500 and answer.status != 401 and not State.killed and State.probes < 4:
            # does this rejected request block the requests after it?
            State.probes += 1
            kept = len(State.exchanges)
            if register_probe(node).timed_out:
                raise stalled(
                    f"after {method} {shape(path)} was answered with {answer.status}, the next request (POST /api/auth/register, a new user) "
                    f"got no answer within {TIMEOUT} seconds. The rejected request leaves something locked or open"
                )
            del State.exchanges[kept:]
        return answer


def shape(path):
    return re.sub(r"/[0-9a-z_-]*\d[0-9a-z_-]*", "/{id}", str(path).split("?")[0])


def register_probe(node):
    body = {"username": "p" + secrets.token_hex(6), "password": "Pass-" + secrets.token_hex(5)}
    return send("POST", "/api/auth/register", body, None, node)


def stalled(text):
    emit({"rule": State.rule, "what": text, "ok": False, "stall": True, "got": "no answer", "want": "an answer",
          "evidence": State.exchanges[-5:], "requests": len(State.exchanges)})  # fmt: skip
    return Stalled(text)


def send(method, path, body=None, user=None, node=0, files=None, headers=None, raw=None):
    data, head = raw, dict(headers or {})
    if files:  # {"file": ("name.txt", b"bytes", "text/plain")}
        mark = secrets.token_hex(8)
        data = b""
        for field, (name, content, kind) in files.items():
            content = content.encode() if isinstance(content, str) else content
            data += (
                f'--{mark}\r\nContent-Disposition: form-data; name="{field}"; filename="{name}"\r\n'
                f"Content-Type: {kind}\r\n\r\n"
            ).encode() + content + b"\r\n"
        data += f"--{mark}--\r\n".encode()
        head["Content-Type"] = f"multipart/form-data; boundary={mark}"
    elif body is not None:
        data = json.dumps(body, default=lambda o: getattr(o, "id", str(o))).encode()
        head.setdefault("Content-Type", "application/json")
    if user is not None:
        head["Authorization"] = f"Bearer {token_of(user)}"
    url = f"http://127.0.0.1:{8000 + node}{path}"
    try:
        req = urllib.request.Request(re.sub(r"\s", "%20", url), method=method, data=data, headers=head)
        with urllib.request.urlopen(req, timeout=TIMEOUT) as r:
            answer = Answer(r.status, r.read(), dict(r.headers))
    except urllib.error.HTTPError as e:
        answer = Answer(e.code, e.read(), dict(e.headers))
    except Exception as e:
        answer = Answer(0)
        answer.timed_out = isinstance(e, TimeoutError) or isinstance(getattr(e, "reason", None), TimeoutError)
        answer.text = f"no answer: {type(e).__name__}: {e}"
    shown = "<file>" if files else short(body, 140) if body is not None else ""
    note(f"{method} :{8000 + node}{path} {shown} -> {answer.status} {short(answer.text, 260)}")
    return answer


class Thing:
    def __init__(self, **kw):
        self.__dict__.update(kw)

    def __str__(self):  # so that it can stand for its id in a path
        return str(self.id)

    def __call__(self, who, node=0):  # a script that names its workspace `ws` hides the helper ws()
        return Ws(who, node)


def made(answer, status, *fields):
    if State.mode == "real" and (answer.status != status or any(answer.get(f) is None for f in fields)):
        raise Unavailable(f"test setup: {State.exchanges[-1]} (needed {status} with {', '.join(fields)})")
    return [answer.get(f) if answer.get(f) is not None else "stub" for f in fields]


def user(prefix="u", node=0):
    name = re.sub(r"\W", "", prefix)[:8] + secrets.token_hex(4)
    password = "Pass-" + secrets.token_hex(5)
    body = {"username": name, "password": password, "display_name": name.title()}
    uid, token = made(call("POST", "/api/auth/register", body, node=node), 201, "user.id", "token")
    return Thing(id=uid, name=name, username=name, token=token, password=password)


def workspace(owner, node=0):
    slug = "w" + secrets.token_hex(5)
    r = call("POST", "/api/workspaces", {"slug": slug, "name": slug.title()}, owner, node)
    wid, general = made(r, 201, "workspace.id", "general_channel.id")
    return Thing(id=wid, slug=slug, general=general, owner=owner)


def channel(owner, ws, node=0, **body):
    if hasattr(owner, "slug"):  # either order
        ws, owner = owner, ws
    body = {"name": "c" + secrets.token_hex(5)} | body
    r = call("POST", f"/api/workspaces/{ws.slug}/channels", body, owner, node)
    return Thing(id=made(r, 201, "channel.id")[0], name=body["name"], workspace=ws)


def member(ws, who, role=None, node=0):
    """Joins the user to the (open) workspace through #general; with a role, the owner then gives that role."""
    if hasattr(who, "slug"):  # either order
        ws, who = who, ws
    made(call("POST", f"/api/channels/{ws.general}/join", {}, who, node), 200)
    if role and role != "member":
        made(call("PATCH", f"/api/workspaces/{ws.slug}/members/{who.id}", {"role": role}, ws.owner, node), 200)
    return who


def message(who, channel_id, body="hello", node=0, **more):
    r = call("POST", f"/api/channels/{getattr(channel_id, 'id', channel_id)}/messages", {"body": body} | more, who, node)
    return Thing(id=made(r, 201, "message.id")[0], body=body)


def join(who, channel_id, node=0):
    return call("POST", f"/api/channels/{getattr(channel_id, 'id', channel_id)}/join", {}, who, node)


def need_app(what):
    if State.mode != "real":
        raise Unavailable(f"no app: {what}")


class Ws:
    def __init__(self, who, node=0):
        need_app("WebSocket")
        try:
            self.conn = base.WebSocket(8000 + node, token_of(who))
        except OSError as e:
            note(f"WebSocket to :{8000 + node} failed: {e}")
            raise Unavailable(f"WebSocket to :{8000 + node} failed: {e}") from None
        note(f"WebSocket to :{8000 + node} opened")

    @property
    def frames(self):
        return list(self.conn.frames)

    def send(self, frame):
        note(f"WebSocket sends {short(frame)}")
        try:
            self.conn._send(1, json.dumps(frame).encode())
        except OSError as e:
            raise Unavailable(f"WebSocket closed: {e}") from None

    def wait(self, pred, seconds=4):
        """Returns the first frame that matches, or None."""
        base.wait_until(lambda: any(pred(f) for f in self.frames), seconds, 0.05)
        found = next((f for f in self.frames if pred(f)), None)
        note(f"WebSocket frames so far: {short(self.frames[-4:], 400)}")
        return found

    def subscribe(self, channel_id, seconds=4):
        cid = getattr(channel_id, "id", channel_id)
        self.send({"type": "subscribe", "channel_id": cid})
        return self.wait(lambda f: f.get("type") == "subscribed" and str(f.get("channel_id")) == str(cid), seconds)

    def close(self):
        self.conn.close()


def ws(who, node=0):
    return Ws(who, node)


class IrcClient:
    def __init__(self):
        need_app("IRC")
        try:
            self.conn = base.Irc()
        except OSError as e:
            raise Unavailable(f"IRC connection to :6667 failed: {e}") from None

    @property
    def lines(self):
        return list(self.conn.lines)

    def send(self, line):
        note(f"IRC sends {short(line, 100)}")
        try:
            self.conn.send(line)
        except OSError as e:
            raise Unavailable(f"IRC connection closed: {e}") from None

    def wait(self, pred, seconds=4):
        """pred is a function of a line, or a numeric or word that is the command of a line ("433"). Returns the line or None."""
        wanted = pred
        if isinstance(wanted, str | int):

            def pred(line):
                return wanted_is_command(line, str(wanted))

        self.conn.wait(pred, seconds)
        note(f"IRC lines so far: {short(self.lines[-4:], 400)}")
        return next((x for x in self.lines if pred(x)), None)

    def login(self, who, nick=None):
        nick = nick or who.name
        self.send(f"PASS {token_of(who)}")
        self.send(f"NICK {nick}")
        self.send(f"USER {nick} 0 * :{nick}")
        return self.wait("001")

    def close(self):
        self.conn.close()


def wanted_is_command(line, command):
    parts = line.split()
    return bool(parts) and (parts[1:2] == [command] if parts[0].startswith(":") else parts[0] == command)


def irc():
    return IrcClient()


def kill(port):
    """SIGKILLs the process that owns the port (8000-8002 an HTTP node, 6379 Redis)."""
    need_app("kill")
    State.killed = True
    note(f"SIGKILL the process on port {port}")
    try:
        base.kill_port(port, {8000, 8001, 8002, 6379, 6667} - {port} if port != 6667 else set())
    except (base.Failed, base.Inconclusive) as e:
        # an IRC gateway that shares a process with a node is allowed, so only other nodes and Redis count
        if "6667" not in str(e) or base.port_owners(port) & set().union(
            *(base.port_owners(p) for p in (8000, 8001, 8002, 6379) if p != port)
        ):
            raise Unavailable(str(e)) from None
        base.kill_port(port, {8000, 8001, 8002, 6379} - {port})


def wait_until(fn, seconds=4, step=0.1):
    return base.wait_until(fn, seconds if State.mode == "real" else 0, step)


def healthy(node):
    return call("GET", "/api/health", node=node).get("status") == "ok" if State.mode == "real" else False


def fresh_install():
    """Installs the app's requirements.txt in an empty virtual environment. Returns (exit code, output)."""
    need_app("install")
    with tempfile.TemporaryDirectory() as tmp:
        subprocess.run([sys.executable, "-m", "venv", tmp], check=True, capture_output=True)
        r = subprocess.run(
            [f"{tmp}/bin/pip", "install", "-q", "--disable-pip-version-check", "-r", str(base.APP / "requirements.txt")],
            capture_output=True, text=True, timeout=120,
        )  # fmt: skip
    note(f"pip install -r requirements.txt in a clean environment -> exit {r.returncode} {short(r.stderr, 200)}")
    return r.returncode, r.stderr[-400:]


BROWSER = {}


def page(who=None, node=0):
    """A Playwright (sync) page on the app at 1280x900, logged in through the token if a user is given."""
    need_app("browser")
    if not BROWSER:
        from playwright.sync_api import sync_playwright

        BROWSER["pw"] = sync_playwright().start()
        BROWSER["b"] = BROWSER["pw"].chromium.launch(args=["--no-sandbox"])
    context = BROWSER["b"].new_context(viewport={"width": 1280, "height": 900})
    context.set_default_timeout(4000)
    url = f"http://127.0.0.1:{8000 + node}/"
    if who is not None:
        token = token_of(who)
        # only on the first load, so a logout followed by a reload stays logged out
        context.add_init_script(
            "if(!sessionStorage.getItem('rk')){sessionStorage.setItem('rk','1');"
            "for(const k of ['huddle.token','token','auth_token'])localStorage.setItem(k,%s)}" % json.dumps(token)
        )
        url += f"?token={token}"
    p = context.new_page()
    p.goto(url, timeout=8000, wait_until="load")
    p.wait_for_timeout(500)
    note(f"browser opened / {'as ' + who.name if hasattr(who, 'name') else ''}")
    return p


def tid(name):
    return f'[data-testid="{name}"]'


def any_of(*values):
    def test(got):
        return got in values

    test.label = " or ".join(short(v, 40) for v in values)
    return test


def check(what, got, want=True):
    """Records one result. `got` may be an answer (its status is compared); `want` a value or a function of `got`."""
    if isinstance(got, Answer):
        got = got.status
    try:
        ok = bool(want(got)) if callable(want) else type(got) is type(want) and got == want or (
            not isinstance(want, bool) and not isinstance(got, bool) and str(got) == str(want)
        )
    except Exception as e:
        ok, got = False, f"{short(got, 80)} ({type(e).__name__}: {e})"
    label = getattr(want, "label", "a value that passes the test") if callable(want) else want
    emit(
        {"rule": State.rule, "what": re.sub(r"[0-9a-f]{8,}", "#", str(what))[:200], "ok": ok, "got": short(got, 120), "want": short(label, 80),
         "evidence": State.exchanges[-4:], "requests": len(State.exchanges)}
    )  # fmt: skip
    return ok


RULES = []


def rule(number):
    def add(fn):
        RULES.append((number, fn))
        return fn

    return add


def main():
    script, out, State.mode = sys.argv[1:4]
    only = {int(x) for x in sys.argv[4].split(",")} if len(sys.argv) > 4 else None
    State.out = open(out, "w", encoding="utf-8")
    if State.mode != "real":
        time.sleep = lambda s: None
    names = (
        call, user, workspace, channel, member, message, join, ws, irc, kill, wait_until, healthy, fresh_install,
        page, tid, any_of, check, rule,
    )  # fmt: skip
    space = {f.__name__: f for f in names} | {"__name__": "probe", "time": time, "json": json, "re": re,
                                              "secrets": secrets, "threading": threading}  # fmt: skip
    source = Path(script).read_text(encoding="utf-8")
    try:
        exec(compile(source, "probe.py", "exec"), space)
    except Exception as e:
        emit({"load_error": f"{type(e).__name__}: {e}"})
        return
    for number, fn in RULES:
        if only and number not in only:
            continue
        State.rule, State.exchanges, State.probes, State.killed = number, [], 0, False
        try:
            fn()
        except Stalled:
            State.stalls += 1
            end = time.time() + 35
            while time.time() < end and register_probe(0).timed_out:
                pass
            break  # the app has to be fixed first
        except Exception as e:
            emit(
                {"rule": number, "what": f"{fn.__name__} could not finish", "ok": False, "error": type(e).__name__,
                 "got": short(str(e).split("\nCall log")[0], 300), "want": "the test runs to its end",
                 "evidence": State.exchanges[-4:], "requests": len(State.exchanges)}
            )  # fmt: skip
        emit({"finished": number})
    for close in (lambda: BROWSER["b"].close(), lambda: BROWSER["pw"].stop()):
        try:
            close()
        except Exception:
            pass
    State.out.close()
    os._exit(0)


if __name__ == "__main__":
    main()
