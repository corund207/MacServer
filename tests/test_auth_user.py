import importlib.util
from pathlib import Path
from types import SimpleNamespace
import unittest

spec = importlib.util.spec_from_file_location("auth_user", Path(__file__).resolve().parents[1] / "scripts/auth_user.py")
auth_user = importlib.util.module_from_spec(spec)
spec.loader.exec_module(auth_user)


class AuthUserTests(unittest.TestCase):
    def test_credentials_only_enter_stdin_and_runtime_is_scoped(self):
        calls = []
        def runner(argv, **options):
            calls.append((argv, options))
            return SimpleNamespace(returncode=0, stdout="created")
        auth_user.create_user("synthetic@example.test", "synthetic-password", "synthetic-secret", runner)
        argv, options = calls[0]
        self.assertNotIn("synthetic-secret", str(argv))
        self.assertNotIn("synthetic-password", str(argv))
        self.assertIn("synthetic-secret", options["input"])
        self.assertIn("--pull=never", argv)
        self.assertIn("--read-only", argv)
        self.assertIn("macserver-data_default", argv)
        self.assertNotIn("--publish", argv)

    def test_invalid_inputs_never_start_runtime(self):
        def runner(*args, **kwargs):
            self.fail("invalid input reached Docker")
        for email, password in [("invalid", "long-enough-password"), ("a@example.test\nx", "long-enough-password"), ("a@example.test", "short")]:
            with self.assertRaises(ValueError):
                auth_user.create_user(email, password, "fixture", runner)

    def test_backend_errors_are_redacted(self):
        with self.assertRaisesRegex(RuntimeError, "Auth refused"):
            auth_user.create_user("synthetic@example.test", "synthetic-password", "fixture", lambda *a, **k: SimpleNamespace(returncode=2, stdout="sensitive upstream failure"))
