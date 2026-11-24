"""restart-app: one command to (re)start the whole app and see whether it came up.
Milestone 3, improvement 3.

Why: in the baseline run, 7 of 24 steps went on starting, killing and re-starting the
cluster (node 8000 failing on a SQLite race, the IRC listener never launching, a
`sleep 30` that hit the 30 s command limit and lost its output). This tool does it in
one step, always inside the 30 s command limit, and shows the startup error at once.

What it does (no model calls, no app code, nothing written to /app):
  1. Stops whatever is listening on 8000-8002 and 6667, by PID (never by process name),
     plus the process group it started last time.
  2. Starts `bash /app/start.sh` detached (its own session, output to /tmp/huddle.log),
     so the command returns instead of hanging on the child.
  3. Waits up to ~18 s for GET /api/health == 200 on 8000-8002 and a TCP accept on 6667.
  4. Prints one line per port, and the last lines of the log if anything is down.
Exit code 0 = everything is up, 1 = something is down, 2 = no /app/start.sh.
Usage:  restart-app
"""

import json
import os
import signal
import socket
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

APP_DIR = Path(os.getenv("MSWEA_APP_DIR", "/app"))
LOG = Path(os.getenv("MSWEA_APP_LOG", "/tmp/huddle.log"))
PIDFILE = Path(os.getenv("MSWEA_STATE_DIR", "/tmp")) / ".mswea_app_pgid"
HTTP_PORTS = [int(p) for p in os.getenv("MSWEA_HTTP_PORTS", "8000,8001,8002").split(",")]
IRC_PORT = int(os.getenv("MSWEA_IRC_PORT", "6667"))
WAIT = float(os.getenv("MSWEA_RESTART_WAIT", "18"))


def _listening_pids(ports: set[int]) -> set[int]:
    inodes = set()
    for f in ("/proc/net/tcp", "/proc/net/tcp6"):
        try:
            lines = Path(f).read_text().splitlines()[1:]
        except OSError:
            continue
        for ln in lines:
            parts = ln.split()
            if len(parts) > 9 and parts[3] == "0A" and int(parts[1].rsplit(":", 1)[1], 16) in ports:
                inodes.add(parts[9])
    pids = set()
    for d in Path("/proc").iterdir():
        if not d.name.isdigit() or int(d.name) == os.getpid():
            continue
        try:
            for fd in (d / "fd").iterdir():
                try:
                    t = os.readlink(fd)
                except OSError:
                    continue
                if t.startswith("socket:[") and t[8:-1] in inodes:
                    pids.add(int(d.name))
        except OSError:
            continue
    return pids


def _stop() -> int:
    killed = 0
    try:
        os.killpg(int(PIDFILE.read_text()), signal.SIGTERM)
        killed += 1
    except (OSError, ValueError):
        pass
    pids = _listening_pids(set(HTTP_PORTS) | {IRC_PORT})
    for sig in (signal.SIGTERM, signal.SIGKILL):
        for pid in pids:
            try:
                os.kill(pid, sig)
            except OSError:
                pass
        deadline = time.time() + 2
        while time.time() < deadline and _listening_pids(set(HTTP_PORTS) | {IRC_PORT}):
            time.sleep(0.2)
        pids = _listening_pids(set(HTTP_PORTS) | {IRC_PORT})
        if not pids:
            break
    return killed + len(pids)


def _http_ok(port: int) -> str | None:
    try:
        with urllib.request.urlopen(f"http://127.0.0.1:{port}/api/health", timeout=1.0) as r:
            body = json.loads(r.read().decode() or "{}")
            return f"200 node_id={body.get('node_id')}" if r.status == 200 else None
    except Exception:
        return None


def _tcp_ok(port: int) -> bool:
    try:
        socket.create_connection(("127.0.0.1", port), 1.0).close()
        return True
    except OSError:
        return False


def main() -> int:
    script = APP_DIR / "start.sh"
    if not script.exists():
        print(f"{script} does not exist: create it first (it must start all three nodes, Redis and IRC).")
        return 2
    _stop()
    out = open(LOG, "wb")
    proc = subprocess.Popen(
        ["bash", str(script)], cwd=APP_DIR, stdin=subprocess.DEVNULL, stdout=out, stderr=subprocess.STDOUT,
        start_new_session=True,
    )
    try:
        PIDFILE.write_text(str(os.getpgid(proc.pid)))
    except OSError:
        pass
    t0 = time.time()
    status: dict = {}
    while time.time() - t0 < WAIT:
        status = {f"http {p}": _http_ok(p) for p in HTTP_PORTS}
        status[f"irc {IRC_PORT}"] = "accepting" if _tcp_ok(IRC_PORT) else None
        if all(status.values()):
            break
        time.sleep(0.7)
    up = all(status.values())
    for k, v in status.items():
        print(f"{k:10} {'UP   ' + v if v else 'DOWN'}")
    print(f"({time.time() - t0:.0f}s; log: {LOG})")
    if not up:
        try:
            tail = LOG.read_text(errors="replace").splitlines()[-15:]
        except OSError:
            tail = []
        print("--- last lines of the log ---")
        print("\n".join(tail) if tail else "(the log is empty: start.sh printed nothing)")
        if proc.poll() is not None:
            print(f"start.sh exited with code {proc.returncode}: it has to leave the services running.")
    print("ALL UP" if up else "NOT UP: fix the cause above, then run `restart-app` again.")
    return 0 if up else 1


if __name__ == "__main__":
    sys.exit(main())