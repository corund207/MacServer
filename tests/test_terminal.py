import importlib.util
import io
import json
import os
from pathlib import Path
import pwd
import shutil
import socket
import struct
import sys
import tempfile
import threading
import time
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "admin"))


def load(name, path):
    spec = importlib.util.spec_from_file_location(name, ROOT / path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


terminal = load("admin_terminal", "admin/terminal.py") if sys.platform != "win32" else None


def client_frame(opcode, payload=b"", fin=True, mask=b"\x01\x02\x03\x04", masked=True):
    n = len(payload)
    head = bytes([(0x80 if fin else 0) | opcode])
    flag = 0x80 if masked else 0
    head += bytes([flag | n]) if n < 126 else bytes([flag | 126]) + struct.pack("!H", n)
    body = bytes(b ^ mask[i % 4] for i, b in enumerate(payload)) if masked else payload
    return head + (mask if masked else b"") + body


@unittest.skipIf(terminal is None, "the terminal needs a Unix pty")
class FramingTests(unittest.TestCase):
    def test_accept_key_matches_rfc6455(self):
        self.assertEqual(terminal.accept_key("dGhlIHNhbXBsZSBub25jZQ=="), "s3pPLMBiTxaQ9kYGzzhZRbK+xOo=")

    def test_server_frames_are_unmasked_and_sized(self):
        self.assertEqual(terminal.encode_frame(2, b"hi"), b"\x82\x02hi")
        self.assertEqual(terminal.encode_frame(2, b"x" * 200)[:4], b"\x82\x7e\x00\xc8")
        self.assertEqual(terminal.encode_frame(2, b"x" * 70000)[:2], b"\x82\x7f")

    def test_reads_masked_client_frames(self):
        self.assertEqual(terminal.read_frame(io.BytesIO(client_frame(2, b"ls\n"))), (2, b"ls\n"))
        self.assertEqual(terminal.read_frame(io.BytesIO(client_frame(1, b"x" * 300))), (1, b"x" * 300))

    def test_joins_fragments(self):
        data = client_frame(2, b"ab", fin=False) + client_frame(0, b"cd")
        self.assertEqual(terminal.read_frame(io.BytesIO(data)), (2, b"abcd"))

    def test_refuses_unmasked_or_oversized_frames(self):
        with self.assertRaises(ValueError):
            terminal.read_frame(io.BytesIO(client_frame(2, b"x", masked=False)))
        huge = bytes([0x82, 0x80 | 127]) + struct.pack("!Q", terminal.MAX_FRAME + 1) + b"\0\0\0\0"
        with self.assertRaises(ValueError):
            terminal.read_frame(io.BytesIO(huge))

    def test_truncated_frame_is_end_of_stream(self):
        with self.assertRaises(EOFError):
            terminal.read_frame(io.BytesIO(client_frame(2, b"hello")[:-2]))


@unittest.skipIf(terminal is None or not shutil.which("setsid"), "needs Unix and setsid")
class LiveShellTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        conf = Path(cls.tmp.name) / "macserver.conf"
        conf.write_text("ADMIN_LOGINS=admin@example.com\nTAILNET_NAME=mac.tail1.ts.net\n")
        terminal.Handler.config = {"conf": str(conf), "user": pwd.getpwuid(os.getuid()).pw_name}
        terminal.ThreadingHTTPServer.daemon_threads = True
        cls.httpd = terminal.ThreadingHTTPServer(("127.0.0.1", 0), terminal.Handler)
        threading.Thread(target=cls.httpd.serve_forever, daemon=True).start()

    @classmethod
    def tearDownClass(cls):
        cls.httpd.shutdown()
        cls.httpd.server_close()
        cls.tmp.cleanup()

    def open(self, path="/term/ws", login="admin@example.com", origin="https://mac.tail1.ts.net", upgrade=True, **extra):
        sock = socket.create_connection(("127.0.0.1", self.httpd.server_address[1]), timeout=10)
        headers = {"Host": "mac.tail1.ts.net"}
        if login:
            headers["Tailscale-User-Login"] = login
        if origin:
            headers["Origin"] = origin
        if upgrade:
            headers.update({"Upgrade": "websocket", "Connection": "Upgrade", "Sec-WebSocket-Version": "13",
                            "Sec-WebSocket-Key": "dGhlIHNhbXBsZSBub25jZQ=="})
        headers.update(extra)
        sock.sendall((f"GET {path} HTTP/1.1\r\n" + "".join(f"{k}: {v}\r\n" for k, v in headers.items()) + "\r\n").encode())
        head = b""
        while b"\r\n\r\n" not in head:
            chunk = sock.recv(4096)
            if not chunk:
                break
            head += chunk
        return sock, head

    def status(self, **kwargs):
        sock, head = self.open(**kwargs)
        sock.close()
        return int(head.split()[1])

    def test_gate(self):
        self.assertEqual(self.status(login=None), 403)
        self.assertEqual(self.status(login="intruder@example.com"), 403)
        self.assertEqual(self.status(origin=None), 403)
        self.assertEqual(self.status(origin="https://evil.example"), 403)   # a page on another site
        self.assertEqual(self.status(**{"Sec-Fetch-Site": "cross-site"}), 403)
        self.assertEqual(self.status(upgrade=False), 400)
        self.assertEqual(self.status(path="/other"), 404)

    def read_until(self, sock, needle, seconds=10):
        seen, deadline = b"", time.time() + seconds
        stream = sock.makefile("rb")
        sock.settimeout(seconds)
        while needle not in seen and time.time() < deadline:
            b0, b1 = stream.read(2)
            n = b1 & 0x7F
            if n == 126:
                n = struct.unpack("!H", stream.read(2))[0]
            elif n == 127:
                n = struct.unpack("!Q", stream.read(8))[0]
            payload = stream.read(n)
            if b0 & 0x0F == 2:
                seen += payload
            elif b0 & 0x0F == 8:
                break
        return seen

    def test_runs_a_shell_and_resizes(self):
        sock, head = self.open(path="/ws")   # the mount point may or may not be stripped
        self.assertIn(b"101", head.split(b"\r\n")[0])
        self.assertIn(b"s3pPLMBiTxaQ9kYGzzhZRbK+xOo=", head)
        sock.sendall(client_frame(1, json.dumps({"type": "resize", "cols": 100, "rows": 30}).encode()))
        sock.sendall(client_frame(2, b"stty size; echo macserver-$((6*7))\n"))
        seen = self.read_until(sock, b"macserver-42\r\n")
        self.assertIn(b"30 100", seen)
        self.assertIn(b"macserver-42", seen)
        sock.sendall(client_frame(8, b"\x03\xe8"))
        sock.close()

    def test_closing_the_socket_hangs_up_the_shell(self):
        sock, _ = self.open()
        sock.sendall(client_frame(2, b"echo $$ > %s/pid; sleep 300\n" % self.tmp.name.encode()))
        pidfile = Path(self.tmp.name) / "pid"
        for _ in range(100):
            if pidfile.exists() and pidfile.read_text().strip():
                break
            time.sleep(0.1)
        pid = int(pidfile.read_text())
        sock.close()
        for _ in range(100):
            try:
                os.kill(pid, 0)
            except ProcessLookupError:
                return
            time.sleep(0.1)
        self.fail("the shell outlived its WebSocket")


if __name__ == "__main__":
    unittest.main()
