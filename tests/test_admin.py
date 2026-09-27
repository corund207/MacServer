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
            self.assertFalse(server.load_status(status, now=1050)["stale"])
            self.assertTrue(server.load_status(status, now=1000 + server.STALE_AFTER_S + 1)["stale"])
            self.assertIn("error", server.load_status(Path(tmp) / "none.json"))


class LiveServerTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        base = Path(cls.tmp.name)
        (base / "macserver.conf").write_text("ADMIN_LOGINS=admin@example.com\n")
        (base / "status.json").write_text(json.dumps({"generated_at": time.time(), "host": {}}))
        server.Handler.config = {"status": str(base / "status.json"), "conf": str(base / "macserver.conf")}
        cls.httpd = server.ThreadingHTTPServer(("127.0.0.1", 0), server.Handler)
        threading.Thread(target=cls.httpd.serve_forever, daemon=True).start()

    @classmethod
    def tearDownClass(cls):
        cls.httpd.shutdown()
        cls.httpd.server_close()
        cls.tmp.cleanup()

    def request(self, path, method="GET", login="admin@example.com"):
        conn = http.client.HTTPConnection("127.0.0.1", self.httpd.server_address[1], timeout=5)
        conn.request(method, path, headers={"Tailscale-User-Login": login} if login else {})
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


if __name__ == "__main__":
    unittest.main()
