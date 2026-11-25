"""Generic clients for testing the running app from outside: HTTP, WebSocket, IRC, and processes found by port.

Standard library only. Linux only (/proc). Used by the independent reviewer and its review_kit.
"""

import base64
import json
import os
import signal
import socket
import struct
import threading
import time
import urllib.error
import urllib.request
from pathlib import Path

APP = Path("/app")
PORTS = (8000, 8001, 8002)
IRC_PORT, REDIS_PORT = 6667, 6379


class Failed(Exception):
    """A test failed. The message is shown to the agent."""


class Inconclusive(Exception):
    """A test could not be decided (for example no network). It never blocks a submit."""


def need(ok, message):
    if not ok:
        raise Failed(message)


# ---------- HTTP ----------
def api(method, port, path, body=None, token=None, timeout=5):
    """Returns (status, json-or-None). Status 0 means no answer."""
    req = urllib.request.Request(
        f"http://127.0.0.1:{port}{path}", method=method, data=None if body is None else json.dumps(body).encode()
    )
    if body is not None:
        req.add_header("Content-Type", "application/json")
    if token:
        req.add_header("Authorization", f"Bearer {token}")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            status, raw = r.status, r.read()
    except urllib.error.HTTPError as e:
        status, raw = e.code, e.read()
    except Exception:
        return 0, None
    try:
        return status, json.loads(raw)
    except Exception:
        return status, None


def healthy(port):
    status, body = api("GET", port, "/api/health", timeout=2)
    return status == 200 and isinstance(body, dict) and body.get("status") == "ok" and body.get("node_id") == port - 8000


def wait_until(fn, seconds, step=0.25):
    end = time.time() + seconds
    while True:
        if fn():
            return True
        if time.time() >= end:
            return False
        time.sleep(step)


# ---------- WebSocket ----------
class WebSocket:
    """Minimal client. Raises OSError if the connection is not upgraded."""

    def __init__(self, port, token):
        self.frames, self.closed = [], False
        self.sock = socket.create_connection(("127.0.0.1", port), timeout=4)
        key = base64.b64encode(os.urandom(16)).decode()
        self.sock.sendall(
            f"GET /api/ws?token={token} HTTP/1.1\r\nHost: 127.0.0.1:{port}\r\nUpgrade: websocket\r\n"
            f"Connection: Upgrade\r\nSec-WebSocket-Key: {key}\r\nSec-WebSocket-Version: 13\r\n\r\n".encode()
        )
        data = b""
        while b"\r\n\r\n" not in data:
            chunk = self.sock.recv(4096)
            if not chunk:
                raise OSError("connection closed during the handshake")
            data += chunk
        head, _, rest = data.partition(b"\r\n\r\n")
        first = head.split(b"\r\n")[0].decode("latin1")
        if " 101" not in first:
            raise OSError(f"answered '{first}' instead of 101")
        self.sock.settimeout(None)
        threading.Thread(target=self._read, args=(rest,), daemon=True).start()

    def _read(self, buf):
        try:
            while True:
                while len(buf) >= 2:
                    n, i = buf[1] & 0x7F, 2
                    if n == 126:
                        if len(buf) < 4:
                            break
                        n, i = struct.unpack(">H", buf[2:4])[0], 4
                    elif n == 127:
                        if len(buf) < 10:
                            break
                        n, i = struct.unpack(">Q", buf[2:10])[0], 10
                    if len(buf) < i + n:
                        break
                    op, payload, buf = buf[0] & 0x0F, buf[i : i + n], buf[i + n :]
                    if op == 1:
                        try:
                            frame = json.loads(payload)
                        except ValueError:
                            continue
                        if isinstance(frame, dict):
                            self.frames.append(frame)
                    elif op == 8:
                        return
                    elif op == 9:
                        self._send(0xA, payload)
                chunk = self.sock.recv(65536)
                if not chunk:
                    return
                buf += chunk
        except OSError:
            pass
        finally:
            self.closed = True

    def _send(self, op, payload):
        mask = os.urandom(4)
        n = len(payload)
        head = bytes([0x80 | op]) + (bytes([0x80 | n]) if n < 126 else bytes([0xFE]) + struct.pack(">H", n))
        self.sock.sendall(head + mask + bytes(b ^ mask[i % 4] for i, b in enumerate(payload)))

    def subscribe(self, channel):
        self._send(1, json.dumps({"type": "subscribe", "channel_id": channel}).encode())
        return wait_until(lambda: any(f.get("type") == "subscribed" for f in self.frames), 4)

    def created(self):
        return [f for f in self.frames if f.get("type") == "message.created"]

    def close(self):
        try:
            self.sock.close()
        except OSError:
            pass


# ---------- IRC ----------
class Irc:
    def __init__(self):
        self.sock = socket.create_connection(("127.0.0.1", IRC_PORT), timeout=4)
        self.sock.settimeout(0.3)
        self.buf, self.lines = b"", []

    def send(self, line):
        self.sock.sendall((line + "\r\n").encode())

    def wait(self, pred, seconds=4):
        end = time.time() + seconds
        while time.time() < end:
            try:
                data = self.sock.recv(65536)
                if not data:
                    time.sleep(0.1)
                self.buf += data
            except (TimeoutError, OSError):
                pass
            *done, self.buf = self.buf.split(b"\n")
            self.lines += [d.decode("utf-8", "replace").rstrip("\r") for d in done]
            if any(pred(line) for line in self.lines):
                return True
        return False

    def close(self):
        try:
            self.sock.close()
        except OSError:
            pass


# ---------- processes (found by the port they own, never by name) ----------
def port_owners(port):
    inodes = set()
    for table in ("/proc/net/tcp", "/proc/net/tcp6"):
        try:
            rows = Path(table).read_text().splitlines()[1:]
        except OSError:
            continue
        for row in rows:
            f = row.split()
            if f[3] == "0A" and int(f[1].rsplit(":", 1)[1], 16) == port:
                inodes.add(f"socket:[{f[9]}]")
    owners = set()
    for pid in (p for p in os.listdir("/proc") if p.isdigit()):
        try:
            if any(os.readlink(f"/proc/{pid}/fd/{fd}") in inodes for fd in os.listdir(f"/proc/{pid}/fd")):
                owners.add(int(pid))
        except OSError:
            continue
    return owners


def own_ancestors():
    pids, pid = set(), os.getpid()
    while pid > 0:
        pids.add(pid)
        try:
            pid = int(Path(f"/proc/{pid}/stat").read_text().rsplit(")", 1)[1].split()[1])
        except (OSError, ValueError, IndexError):
            break
    return pids


def kill_port(port, must_not_own):
    pids = port_owners(port)
    need(pids, f"nothing is listening on port {port}")
    others = set().union(*(port_owners(p) for p in must_not_own))
    need(
        not pids & others,
        f"the process on port {port} also owns port(s) among {sorted(must_not_own)}: each HTTP node and Redis must be its own process",
    )
    if pids & own_ancestors():
        raise Inconclusive("the port belongs to the agent itself")
    for pid in pids:
        try:
            os.kill(pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
