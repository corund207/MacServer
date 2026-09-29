"""MacServer web terminal: a login shell in the browser, over one WebSocket.

Runs as the owner's account (not as the sandboxed admin page), listens on 127.0.0.1 only,
and is published on the tailnet by `tailscale serve` at /term. It has the same reach as
Tailscale SSH, and the same gate as the admin page: the request must come from loopback
with a Tailscale-User-Login on the ADMIN_LOGINS allowlist. Because browsers let any web
page open a WebSocket to any host, the Origin must also be this machine's own tailnet
name (a page on another site cannot get a shell by tricking the owner's browser).

One WebSocket is one shell. Binary frames carry terminal bytes both ways; a text frame
from the browser is a JSON message: {"type": "resize", "cols": N, "rows": N}.
Closing the socket hangs up the shell.
"""
import argparse
import base64
import fcntl
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
from pathlib import Path
import pwd
import select
import signal
import socket
import struct
import subprocess
import sys
import termios
import threading
import hashlib

sys.path.insert(0, str(Path(__file__).resolve().parent))
from server import allowed_logins, authorize, conf_value  # noqa: E402  (same directory)

GUID = "258EAFA5-E914-47DA-95CA-C5AB0DC85B11"   # RFC 6455
MAX_FRAME = 1 << 20
MAX_SESSIONS = 4
PATHS = ("/ws", "/term/ws")   # tailscale serve may or may not strip the /term mount point
sessions = threading.BoundedSemaphore(MAX_SESSIONS)


def accept_key(client_key):
    return base64.b64encode(hashlib.sha1((client_key + GUID).encode()).digest()).decode()


def encode_frame(opcode, payload=b""):
    """A server-to-client frame (never masked)."""
    n = len(payload)
    if n < 126:
        head = struct.pack("!BB", 0x80 | opcode, n)
    elif n < 1 << 16:
        head = struct.pack("!BBH", 0x80 | opcode, 126, n)
    else:
        head = struct.pack("!BBQ", 0x80 | opcode, 127, n)
    return head + payload


def read_exact(rfile, n):
    data = rfile.read(n)
    if data is None or len(data) != n:
        raise EOFError
    return data


def read_frame(rfile):
    """(opcode, payload) of one complete client message; fragments are joined."""
    message, first_opcode = b"", None
    while True:
        b0, b1 = read_exact(rfile, 2)
        fin, opcode, masked, n = b0 & 0x80, b0 & 0x0F, b1 & 0x80, b1 & 0x7F
        if n == 126:
            n = struct.unpack("!H", read_exact(rfile, 2))[0]
        elif n == 127:
            n = struct.unpack("!Q", read_exact(rfile, 8))[0]
        if not masked or n > MAX_FRAME or (b0 & 0x70):   # clients must mask; no extensions
            raise ValueError("bad frame")
        mask = read_exact(rfile, 4)
        payload = bytes(b ^ mask[i % 4] for i, b in enumerate(read_exact(rfile, n)))
        if opcode >= 0x8:            # control frames are never fragmented
            return opcode, payload
        if opcode != 0:
            first_opcode = opcode
        message += payload
        if len(message) > MAX_FRAME:
            raise ValueError("message too large")
        if fin:
            return first_opcode, message


def set_size(fd, rows, cols):
    fcntl.ioctl(fd, termios.TIOCSWINSZ, struct.pack("HHHH", rows, cols, 0, 0))


def owner_shell(user):
    entry = pwd.getpwnam(user)
    usable = os.access(entry.pw_shell, os.X_OK) and Path(entry.pw_shell).name not in ("nologin", "false")
    return entry.pw_dir, entry.pw_shell if usable else "/bin/bash"


def spawn_shell(shell, home, user, rows=24, cols=80):
    """A login shell on a new pseudo-terminal, in its own session. Returns (process, master fd)."""
    master, slave = os.openpty()
    set_size(master, rows, cols)
    env = {"TERM": "xterm-256color", "HOME": home, "SHELL": shell, "LANG": os.environ.get("LANG", "C.UTF-8"),
           "USER": user, "LOGNAME": user, "PATH": "/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin"}
    # `setsid -c` makes the pty the shell's controlling terminal (job control, Ctrl+C).
    process = subprocess.Popen(["setsid", "-c", shell, "-l"], stdin=slave, stdout=slave, stderr=slave,
                               cwd=home, env=env, close_fds=True)
    os.close(slave)
    return process, master


def hang_up(process):
    for sig in (signal.SIGHUP, signal.SIGKILL):
        try:
            os.killpg(os.getpgid(process.pid), sig)
        except (ProcessLookupError, PermissionError):
            break
        try:
            process.wait(timeout=2)
            break
        except subprocess.TimeoutExpired:
            continue


class Handler(BaseHTTPRequestHandler):
    server_version = "macserver-terminal"
    sys_version = ""
    protocol_version = "HTTP/1.1"
    config = {}

    def refuse(self, status, text):
        body = f"{text}\n".encode()
        self.send_response(status)
        self.send_header("Content-Type", "text/plain; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Connection", "close")
        self.end_headers()
        self.wfile.write(body)
        self.close_connection = True

    def allowed(self):
        """None when the browser may open a shell, else a reason."""
        conf = self.config["conf"]
        if authorize(self.client_address[0], self.headers.get("Tailscale-User-Login"), allowed_logins(conf)) is None:
            return "Forbidden: not an approved MacServer admin."
        name = conf_value(conf, "TAILNET_NAME")
        if not name or self.headers.get("Origin") != f"https://{name}":
            return "Forbidden: bad origin."
        if self.headers.get("Sec-Fetch-Site", "same-origin") != "same-origin":
            return "Forbidden: cross-site request."
        return None

    def do_GET(self):
        if self.path.split("?", 1)[0] not in PATHS:
            return self.refuse(HTTPStatus.NOT_FOUND, "Not found")
        reason = self.allowed()
        if reason:
            return self.refuse(HTTPStatus.FORBIDDEN, reason)
        key = self.headers.get("Sec-WebSocket-Key", "")
        if ("websocket" not in self.headers.get("Upgrade", "").lower()
                or self.headers.get("Sec-WebSocket-Version") != "13" or not key):
            return self.refuse(HTTPStatus.BAD_REQUEST, "WebSocket required")
        if not sessions.acquire(blocking=False):
            return self.refuse(HTTPStatus.SERVICE_UNAVAILABLE, "Too many open terminals")
        try:
            self.send_response(HTTPStatus.SWITCHING_PROTOCOLS)
            self.send_header("Upgrade", "websocket")
            self.send_header("Connection", "Upgrade")
            self.send_header("Sec-WebSocket-Accept", accept_key(key))
            self.end_headers()
            self.close_connection = True
            self.session()
        finally:
            sessions.release()

    def session(self):
        home, shell = owner_shell(self.config["user"])
        process, master = spawn_shell(shell, home, self.config["user"])
        lock = threading.Lock()
        done = threading.Event()

        def send(opcode, payload=b""):
            with lock:
                self.connection.sendall(encode_frame(opcode, payload))

        def shell_to_browser():
            try:
                while not done.is_set():
                    ready, _, _ = select.select([master], [], [], 0.5)
                    if ready:
                        data = os.read(master, 8192)
                        if not data:
                            break
                        send(0x2, data)
                    elif process.poll() is not None:
                        break
                send(0x8, struct.pack("!H", 1000))
            except OSError:
                pass
            finally:
                done.set()
                try:   # unblock the read below
                    self.connection.shutdown(socket.SHUT_RDWR)
                except OSError:
                    pass

        pump = threading.Thread(target=shell_to_browser, daemon=True)
        pump.start()
        try:
            while not done.is_set():
                opcode, payload = read_frame(self.rfile)
                if opcode == 0x2:
                    os.write(master, payload)
                elif opcode == 0x1:
                    self.control(master, payload)
                elif opcode == 0x8:
                    break
                elif opcode == 0x9:
                    send(0xA, payload[:125])
        except (EOFError, ValueError, OSError):
            pass
        finally:
            done.set()
            hang_up(process)
            pump.join(timeout=3)
            os.close(master)

    @staticmethod
    def control(master, payload):
        try:
            message = json.loads(payload)
            if message.get("type") == "resize":
                cols, rows = int(message["cols"]), int(message["rows"])
                if 1 <= cols <= 500 and 1 <= rows <= 200:
                    set_size(master, rows, cols)
        except (ValueError, KeyError, TypeError, AttributeError, OSError):
            pass

    do_POST = do_PUT = do_DELETE = do_PATCH = do_HEAD = lambda self: self.refuse(
        HTTPStatus.METHOD_NOT_ALLOWED, "WebSocket only")

    def log_message(self, fmt, *args):
        pass


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--port", type=int, default=8091)
    parser.add_argument("--conf", default="/etc/macserver/macserver.conf")
    parser.add_argument("--user", default=pwd.getpwuid(os.getuid()).pw_name,
                        help="account the shell runs as (default: the account this service runs as)")
    args = parser.parse_args()
    Handler.config = {"conf": args.conf, "user": args.user}
    ThreadingHTTPServer.daemon_threads = True
    ThreadingHTTPServer(("127.0.0.1", args.port), Handler).serve_forever()


if __name__ == "__main__":
    main()
