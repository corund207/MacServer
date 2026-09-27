#!/usr/bin/env python3
"""MacServer private admin page (read-only).

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
FILES = {"/": ("index.html", "text/html; charset=utf-8"),
         "/app.js": ("app.js", "text/javascript; charset=utf-8"),
         "/style.css": ("style.css", "text/css; charset=utf-8")}
SECURITY_HEADERS = {
    "Content-Security-Policy": "default-src 'self'; img-src 'self' data:; frame-ancestors 'none'; "
                               "base-uri 'none'; form-action 'none'",
    "X-Content-Type-Options": "nosniff",
    "Referrer-Policy": "no-referrer",
    "Cache-Control": "no-store",
    "Cross-Origin-Opener-Policy": "same-origin",
    "Cross-Origin-Resource-Policy": "same-origin",
}
STALE_AFTER_S = 120


def allowed_logins(conf_path):
    try:
        text = Path(conf_path).read_text()
    except OSError:
        return set()
    for line in text.splitlines():
        if line.startswith("ADMIN_LOGINS="):
            return {x.strip().lower() for x in line.split("=", 1)[1].split(",") if x.strip()}
    return set()


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

    def send(self, status, body, content_type):
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        for name, value in SECURITY_HEADERS.items():
            self.send_header(name, value)
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
            self.send(HTTPStatus.OK, (STATIC / name).read_bytes(), content_type)
        else:
            self.send(HTTPStatus.NOT_FOUND, b"Not found\n", "text/plain; charset=utf-8")

    do_HEAD = do_GET

    def do_POST(self):
        self.send(HTTPStatus.METHOD_NOT_ALLOWED, b"Read-only\n", "text/plain; charset=utf-8")

    do_PUT = do_DELETE = do_PATCH = do_POST

    def log_message(self, fmt, *args):  # keep the journal short; no headers logged
        pass


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--port", type=int, default=8090)
    parser.add_argument("--status", default="/run/macserver/status.json")
    parser.add_argument("--conf", default="/etc/macserver/macserver.conf")
    args = parser.parse_args()
    Handler.config = {"status": args.status, "conf": args.conf}
    ThreadingHTTPServer(("127.0.0.1", args.port), Handler).serve_forever()


if __name__ == "__main__":
    main()
