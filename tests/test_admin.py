import http.client
import importlib.util
import json
from pathlib import Path
import tempfile
import threading
import time
import unittest

ROOT = Path(__file__).resolve().parents[1]


def load(name, path):
    spec = importlib.util.spec_from_file_location(name, ROOT / path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


server = load("admin_server", "admin/server.py")


class AuthorizationTests(unittest.TestCase):
    def test_requires_loopback_and_allowlisted_login(self):
        allow = {"admin@example.com"}
        self.assertEqual(server.authorize("127.0.0.1", "Admin@Example.com", allow), "admin@example.com")
        self.assertEqual(server.authorize("::1", "admin@example.com", allow), "admin@example.com")
        self.assertIsNone(server.authorize("100.64.0.9", "admin@example.com", allow))
        self.assertIsNone(server.authorize("127.0.0.1", "other@example.com", allow))
        self.assertIsNone(server.authorize("127.0.0.1", None, allow))
        self.assertIsNone(server.authorize("127.0.0.1", "", {""}))

    def test_allowlist_parsing(self):
        with tempfile.TemporaryDirectory() as tmp:
            conf = Path(tmp) / "macserver.conf"
            conf.write_text("TAILNET_NAME=x\nADMIN_LOGINS= A@example.com, b@example.com ,\n")
            self.assertEqual(server.allowed_logins(conf), {"a@example.com", "b@example.com"})
            self.assertEqual(server.allowed_logins(Path(tmp) / "missing"), set())

    def test_stale_status(self):
        with tempfile.TemporaryDirectory() as tmp:
            status = Path(tmp) / "status.json"
            status.write_text(json.dumps({"generated_at": 1000}))
            self.assertFalse(server.load_status(status, now=1000 + server.STALE_AFTER_S - 1)["stale"])
            self.assertTrue(server.load_status(status, now=1000 + server.STALE_AFTER_S + 1)["stale"])
            self.assertIn("error", server.load_status(Path(tmp) / "none.json"))


class LiveServerTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        base = Path(cls.tmp.name)
        (base / "macserver.conf").write_text("ADMIN_LOGINS=admin@example.com\nTAILNET_NAME=mac.tail1.ts.net\n")
        (base / "status.json").write_text(json.dumps({"generated_at": time.time(), "host": {}}))
        cls.claude_request = base / "claude-request"
        server.Handler.config = {"status": str(base / "status.json"), "conf": str(base / "macserver.conf"),
                                 "claude_request": str(cls.claude_request)}
        cls.httpd = server.ThreadingHTTPServer(("127.0.0.1", 0), server.Handler)
        threading.Thread(target=cls.httpd.serve_forever, daemon=True).start()

    @classmethod
    def tearDownClass(cls):
        cls.httpd.shutdown()
        cls.httpd.server_close()
        cls.tmp.cleanup()

    def request(self, path, method="GET", login="admin@example.com", body=None, headers=None):
        conn = http.client.HTTPConnection("127.0.0.1", self.httpd.server_address[1], timeout=5)
        all_headers = {"Tailscale-User-Login": login} if login else {}
        all_headers.update(headers or {})
        conn.request(method, path, body=body, headers=all_headers)
        response = conn.getresponse()
        body = response.read()
        conn.close()
        return response, body

    def test_admin_gets_status_with_security_headers(self):
        response, body = self.request("/api/status")
        self.assertEqual(response.status, 200)
        self.assertEqual(json.loads(body)["viewer"], "admin@example.com")
        self.assertIn("frame-ancestors 'none'", response.getheader("Content-Security-Policy"))
        self.assertEqual(response.getheader("Cache-Control"), "no-store")
        self.assertEqual(self.request("/")[0].status, 200)

    def test_rejects_missing_or_unknown_login(self):
        self.assertEqual(self.request("/api/status", login=None)[0].status, 403)
        self.assertEqual(self.request("/", login="intruder@example.com")[0].status, 403)

    def test_only_known_files_and_read_only(self):
        self.assertEqual(self.request("/../server.py")[0].status, 404)
        self.assertEqual(self.request("/static/../server.py")[0].status, 404)
        self.assertEqual(self.request("/api/status", method="POST")[0].status, 405)
        self.assertEqual(self.request("/api/status", method="PUT")[0].status, 405)

    def test_terminal_page_has_its_own_policy(self):
        page, _ = self.request("/terminal.html")
        policy = page.getheader("Content-Security-Policy")
        self.assertEqual(page.status, 200)
        self.assertIn("frame-ancestors 'self'", policy)
        self.assertIn("wss://mac.tail1.ts.net", policy)
        self.assertIn("style-src 'self' 'unsafe-inline'", policy)
        main = self.request("/")[0].getheader("Content-Security-Policy")   # the rest stays strict
        self.assertNotIn("unsafe-inline", main)
        self.assertIn("frame-ancestors 'none'", main)
        self.assertIn("frame-src 'self'", main)
        for path in ("/terminal.js", "/xterm.js", "/xterm.css", "/xterm-addon-fit.js"):
            self.assertEqual(self.request(path)[0].status, 200, path)
        self.assertEqual(self.request("/terminal.html", login=None)[0].status, 403)

    def claude(self, action="start", login="admin@example.com", **headers):
        sent = {"Content-Type": "application/json", "Origin": "https://mac.tail1.ts.net",
                "Sec-Fetch-Site": "same-origin"}
        sent.update({k.replace("_", "-"): v for k, v in headers.items()})
        self.claude_request.unlink(missing_ok=True)
        return self.request("/api/claude", "POST", login, json.dumps({"action": action}), sent)[0].status

    def test_claude_session_request(self):
        self.assertEqual(self.claude("start"), 202)
        self.assertEqual(self.claude_request.read_text(), "start")
        self.assertEqual(self.claude("stop"), 202)
        self.assertEqual(self.claude_request.read_text(), "stop")

    def test_claude_request_refused(self):
        self.assertEqual(self.claude("start", login=None), 403)
        self.assertEqual(self.claude("start", login="intruder@example.com"), 403)
        self.assertEqual(self.claude("rm -rf /"), 400)
        self.assertEqual(self.claude("start", Origin="https://evil.example"), 400)
        self.assertEqual(self.claude("start", Sec_Fetch_Site="cross-site"), 400)
        self.assertEqual(self.claude("start", Content_Type="text/plain"), 400)
        self.assertFalse(self.claude_request.exists())


class VendoredTerminalTests(unittest.TestCase):
    """xterm.js is copied from npm, not built here. A changed byte must be a decision."""
    PINNED = {
        "xterm.js": "1f991ac3b4b283ebf96e60ae23a00a52765dd3a2e46fa6fdda9f1aab032f7495",   # @xterm/xterm 5.5.0
        "xterm.css": "ba8e6985669488981ccf40c0cefe3aba80722cb6c92de7ad628b0bd717faf2b6",  # @xterm/xterm 5.5.0
        "xterm-addon-fit.js": "bdaefa370b1bfc42ee88d46fe6072400902a4d4b2d45cd93438dda9b23c97089",  # @xterm/addon-fit 0.10.0
    }

    def test_files_match_their_pinned_hashes(self):
        import hashlib
        for name, digest in self.PINNED.items():
            data = (ROOT / "admin/static" / name).read_bytes()
            self.assertEqual(hashlib.sha256(data).hexdigest(), digest, name)

    def test_every_served_file_exists(self):
        for name, _ in server.FILES.values():
            self.assertTrue((ROOT / "admin/static" / name).is_file(), name)


if __name__ == "__main__":
    unittest.main()
