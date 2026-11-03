#!/usr/bin/env python3
"""Serve the admin page with sample data, to look at it in a browser without the Mac.

    python3 tests/web_preview.py [healthy|warning|trouble] [--port 8765]

Open http://127.0.0.1:8765/ . The numbers move a little on every request so the charts have something to
draw. The buttons do nothing real: POSTs are answered with a canned reply. Sample data only.
"""
import json
import math
from pathlib import Path
import sys
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

sys.path.insert(0, str(Path(__file__).resolve().parent))
import tui_fixture  # noqa: E402  (also writes the sample status.json the page reads)

STATIC = Path(tui_fixture.ROOT) / "admin/static"
TYPES = {".html": "text/html; charset=utf-8", ".js": "text/javascript; charset=utf-8", ".css": "text/css; charset=utf-8"}
FILES = {"/": "index.html", "/app.js": "app.js", "/style.css": "style.css", "/terminal.html": "terminal.html", "/terminal.js": "terminal.js",
         "/xterm.js": "xterm.js", "/xterm.css": "xterm.css", "/xterm-addon-fit.js": "xterm-addon-fit.js"}


def sample(scenario):
    tui_fixture.make_sampler(scenario)                       # writes the fixture status.json
    return json.loads((tui_fixture.BASE / "status.json").read_text())


def alive(base, t):
    """The same sample, moving: CPU, traffic and temperature drift like a real machine's."""
    d = json.loads(json.dumps(base))
    wave = (math.sin(t / 9) + 1) / 2
    fast = (math.sin(t / 2.3) + 1) / 2
    live = d["live"]
    live["cpu"]["total"] = round(20 + 45 * wave + 8 * fast, 1)
    live["cpu"]["cores"] = [round(min(100, live["cpu"]["total"] + k * 6 * (fast - 0.5)), 1) for k in range(-2, 2)]
    live["net"]["rx"] = int(200_000 + 1_800_000 * wave ** 3)
    live["net"]["tx"] = int(30_000 + 220_000 * (1 - wave) ** 2)
    live["disk_io"] = {"read": int(300_000 + 1_500_000 * fast ** 4), "write": int(100_000 + 400_000 * wave)}
    d["host"]["sensors"]["cpu_temp_c"] = round(72 + 20 * wave + 4 * fast, 1)
    d["host"]["load"] = [round(1 + 4 * wave, 2), 2.9, 2.4]
    for c in d["containers"]:
        if c.get("cpu") is not None:
            c["cpu"] = round(c["cpu"] * (0.5 + fast), 2)
    d["system"]["db"]["tps"] = round(2 + 3 * fast, 1)
    d["debug"]["requests"]["per_2s"] = [int(2 + 3 * (math.sin((t + i) / 3) + 1) / 2) for i in range(30)]
    d.update(generated_at=int(t), stale=False, viewer="preview@example.com")
    return d


class Handler(BaseHTTPRequestHandler):
    base = {}
    start, ticks = time.time() - 600, 0

    def send(self, status, body, ctype):
        self.send_response(status)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        # the page's real policy: no inline script or style, no outside fonts or libraries
        self.send_header("Content-Security-Policy", "default-src 'self'; img-src 'self' data:; frame-src 'self'; base-uri 'none'; form-action 'none'")
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        path = self.path.split("?", 1)[0]
        if path == "/api/status":
            Handler.ticks += 1                                  # each poll is a new 2 s reading, however fast it came
            self.send(200, json.dumps(alive(self.base, Handler.start + 2 * Handler.ticks)).encode(), "application/json")
        elif path in FILES:
            f = STATIC / FILES[path]
            self.send(200, f.read_bytes(), TYPES.get(f.suffix, "application/octet-stream"))
        else:
            self.send(404, b"Not found\n", "text/plain")

    def do_POST(self):
        self.rfile.read(int(self.headers.get("Content-Length", "0")))
        self.send(202 if self.path == "/api/claude" else 200, b"(sample data: nothing was run)\n", "text/plain; charset=utf-8")

    def log_message(self, *args):
        pass


if __name__ == "__main__":
    args = sys.argv[1:]
    scenario = next((a for a in args if a in ("healthy", "warning", "trouble")), "healthy")
    port = int(args[args.index("--port") + 1]) if "--port" in args else 8765
    Handler.base = sample(scenario)
    print(f"sample admin page ({scenario}) on http://127.0.0.1:{port}/  (Ctrl+C to stop)")
    ThreadingHTTPServer(("127.0.0.1", port), Handler).serve_forever()
