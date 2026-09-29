#!/usr/bin/env python3
"""MacServer private admin page.

Read-only except for one action: starting or stopping the Claude Code Remote Control
session. The page cannot run anything itself; it writes "start" or "stop" to a
request file that a root-owned systemd path unit acts on.

Listens on 127.0.0.1 only. `tailscale serve` puts it on the tailnet over HTTPS and
adds the Tailscale-User-Login header, which Tailscale sets itself and does not
accept from clients. A request is served only when it comes from loopback and
that login is on the ADMIN_LOGINS allowlist in /etc/macserver/macserver.conf.
"""
import argparse
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
from pathlib import Path
import time

STATIC = Path(__file__).resolve().parent / "static"
HTML, JS, CSS = "text/html; charset=utf-8", "text/javascript; charset=utf-8", "text/css; charset=utf-8"
FILES = {"/": ("index.html", HTML), "/app.js": ("app.js", JS), "/style.css": ("style.css", CSS),
         "/terminal.html": ("terminal.html", HTML), "/terminal.js": ("terminal.js", JS),
         "/xterm.js": ("xterm.js", JS), "/xterm.css": ("xterm.css", CSS),
         "/xterm-addon-fit.js": ("xterm-addon-fit.js", JS)}
SECURITY_HEADERS = {
    "Content-Security-Policy": "default-src 'self'; img-src 'self' data:; frame-src 'self'; "
                               "frame-ancestors 'none'; base-uri 'none'; form-action 'none'",
    "X-Content-Type-Options": "nosniff",
    "Referrer-Policy": "no-referrer",
    "Cache-Control": "no-store",
    "Cross-Origin-Opener-Policy": "same-origin",
    "Cross-Origin-Resource-Policy": "same-origin",
}

def terminal_policy(tailnet_name):
    """The terminal page is the one page that needs more: xterm.js styles itself with inline
    CSS, and it opens the WebSocket to /term. It is framed only by the admin page."""
    return ("default-src 'self'; style-src 'self' 'unsafe-inline'; "
            f"connect-src 'self' wss://{tailnet_name}; frame-ancestors 'self'; base-uri 'none'; form-action 'none'")


STALE_AFTER_S = 20
CLAUDE_ACTIONS = ("start", "stop")
MAX_BODY = 256


def conf_value(conf_path, key):
    try:
        text = Path(conf_path).read_text()
    except OSError:
        return ""
    for line in text.splitlines():
        if line.startswith(f"{key}="):
            return line.split("=", 1)[1].strip()
    return ""


def allowed_logins(conf_path):
    return {x.strip().lower() for x in conf_value(conf_path, "ADMIN_LOGINS").split(",") if x.strip()}


def authorize(client_ip, login, allowlist):
    """Return the normalized login if the request may be served, else None."""
    if client_ip not in ("127.0.0.1", "::1"):
        return None
    login = (login or "").strip().lower()
    return login if login and login in allowlist else None


def load_status(path, now=None):
    now = time.time() if now is None else now
    try:
        data = json.loads(Path(path).read_text())
    except (OSError, ValueError):
        return {"error": "status not collected yet"}
    data["stale"] = now - data.get("generated_at", 0) > STALE_AFTER_S
    return data


class Handler(BaseHTTPRequestHandler):
    server_version = "macserver-admin"
    sys_version = ""
    config = {}

    def send(self, status, body, content_type, policy=None):
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        for name, value in SECURITY_HEADERS.items():
            self.send_header(name, policy if policy and name == "Content-Security-Policy" else value)
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(body)

    def do_GET(self):
        login = authorize(self.client_address[0], self.headers.get("Tailscale-User-Login"),
                          allowed_logins(self.config["conf"]))
        if login is None:
            self.send(HTTPStatus.FORBIDDEN, b"Forbidden: not an approved MacServer admin.\n",
                      "text/plain; charset=utf-8")
            return
        path = self.path.split("?", 1)[0]
        if path == "/api/status":
            data = load_status(self.config["status"])
            data["viewer"] = login
            self.send(HTTPStatus.OK, json.dumps(data).encode(), "application/json")
        elif path in FILES:
            name, content_type = FILES[path]
            policy = terminal_policy(conf_value(self.config["conf"], "TAILNET_NAME")) if path == "/terminal.html" else None
            self.send(HTTPStatus.OK, (STATIC / name).read_bytes(), content_type, policy)
        else:
            self.send(HTTPStatus.NOT_FOUND, b"Not found\n", "text/plain; charset=utf-8")

    do_HEAD = do_GET

    def do_POST(self):
        login = authorize(self.client_address[0], self.headers.get("Tailscale-User-Login"),
                          allowed_logins(self.config["conf"]))
        if login is None:
            self.send(HTTPStatus.FORBIDDEN, b"Forbidden: not an approved MacServer admin.\n",
                      "text/plain; charset=utf-8")
            return
        if self.path.split("?", 1)[0] != "/api/claude" or not self.config.get("claude_request"):
            self.send(HTTPStatus.METHOD_NOT_ALLOWED, b"Read-only\n", "text/plain; charset=utf-8")
            return
        action = self.read_action()
        if action is None:
            self.send(HTTPStatus.BAD_REQUEST, b"Bad request\n", "text/plain; charset=utf-8")
            return
        Path(self.config["claude_request"]).write_text(action)
        self.send(HTTPStatus.ACCEPTED, json.dumps({"action": action}).encode(), "application/json")

    def read_action(self):
        """The requested Claude action, or None. Only same-origin JSON is accepted: a
        cross-site page cannot send application/json without a CORS preflight, which
        this server never answers."""
        if self.headers.get("Content-Type", "").split(";")[0].strip() != "application/json":
            return None
        name = conf_value(self.config["conf"], "TAILNET_NAME")
        if not name or self.headers.get("Origin") not in (None, f"https://{name}"):
            return None
        if self.headers.get("Sec-Fetch-Site", "same-origin") != "same-origin":
            return None
        try:
            length = int(self.headers.get("Content-Length", "0"))
        except ValueError:
            return None
        if not 0 < length <= MAX_BODY:
            return None
        try:
            action = json.loads(self.rfile.read(length)).get("action")
        except (ValueError, AttributeError):
            return None
        return action if action in CLAUDE_ACTIONS else None

    def do_PUT(self):
        self.send(HTTPStatus.METHOD_NOT_ALLOWED, b"Read-only\n", "text/plain; charset=utf-8")

    do_DELETE = do_PATCH = do_PUT

    def log_message(self, fmt, *args):  # keep the journal short; no headers logged
        pass


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--port", type=int, default=8090)
    parser.add_argument("--status", default="/run/macserver/status.json")
    parser.add_argument("--conf", default="/etc/macserver/macserver.conf")
    parser.add_argument("--claude-request", default="",
                        help="file that Claude session requests are written to (empty: disabled)")
    args = parser.parse_args()
    Handler.config = {"status": args.status, "conf": args.conf, "claude_request": args.claude_request}
    ThreadingHTTPServer(("127.0.0.1", args.port), Handler).serve_forever()


if __name__ == "__main__":
    main()
