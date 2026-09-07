import importlib.util
import json
from pathlib import Path
import stat
import tempfile
import unittest
from unittest.mock import patch
import os

ROOT = Path(__file__).resolve().parents[1]


def module(name):
    spec = importlib.util.spec_from_file_location(name, ROOT / "scripts" / f"{name}.py")
    result = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(result)
    return result


bootstrap = module("bootstrap")
secret_tool = module("init_secrets")


class SafetyTests(unittest.TestCase):
    def test_stage_rejects_source_symlink_and_special_file(self):
        with tempfile.TemporaryDirectory() as base:
            root = Path(base)
            (root / "infra").mkdir()
            entry = root / "infra" / "unsafe"
            entry.symlink_to(ROOT / ".env.example")
            with patch.object(bootstrap, "ROOT", root):
                with self.assertRaises(ValueError):
                    bootstrap.stage(root / "bundle")
                entry.unlink()
                os.mkfifo(entry)
                with self.assertRaises(ValueError):
                    bootstrap.stage(root / "bundle")
            self.assertFalse((root / "bundle").exists())

    def test_stage_rejects_permission_drift(self):
        with tempfile.TemporaryDirectory() as base:
            output = Path(base) / "bundle"
            bootstrap.stage(output)
            output.chmod(0o755)
            with self.assertRaises(ValueError):
                bootstrap.stage(output)
            output.chmod(0o700)
            (output / "manifest.json").chmod(0o644)
            with self.assertRaises(ValueError):
                bootstrap.stage(output)

    def test_preflight_refuses_unsupported_targets(self):
        gib = 1024**3
        valid = ({"ID": "debian", "VERSION_ID": "13"}, "x86_64", 8*gib, 80*gib)
        self.assertTrue(bootstrap.assess(*valid)["eligible"])
        for args in [({"ID": "omarchy"}, *valid[1:]), (valid[0], "aarch64", *valid[2:]),
                     (*valid[:2], gib, valid[3]), (*valid[:3], gib)]:
            self.assertFalse(bootstrap.assess(*args)["eligible"])

    def test_stage_repeat_preserves_files_and_rejects_drift(self):
        with tempfile.TemporaryDirectory() as base:
            output = Path(base) / "bundle"
            bootstrap.stage(output)
            target = output / "infra/host/sshd.conf"
            before = target.stat().st_mtime_ns
            bootstrap.stage(output)
            self.assertEqual(target.stat().st_mtime_ns, before)
            self.assertIn("infra/host/sshd.conf", json.loads((output / "manifest.json").read_text()))
            target.write_text("operator edit")
            with self.assertRaises(ValueError):
                bootstrap.stage(output)
            self.assertEqual(target.read_text(), "operator edit")

    def test_stage_refuses_symlinks_and_extra_files(self):
        with tempfile.TemporaryDirectory() as base:
            base = Path(base)
            link = base / "link"
            link.symlink_to(base / "missing")
            with self.assertRaises(ValueError):
                bootstrap.stage(link)
            output = base / "bundle"
            bootstrap.stage(output)
            (output / "unexpected").write_text("preserve")
            with self.assertRaises(ValueError):
                bootstrap.stage(output)
            (output / "unexpected").unlink()
            (output / "linked").symlink_to(base / "missing")
            with self.assertRaises(ValueError):
                bootstrap.stage(output)

    def test_secret_permissions_and_no_rotation(self):
        with tempfile.TemporaryDirectory() as base:
            directory = Path(base) / "secrets"
            secret_tool.initialize(directory)
            target = directory / "restic-password"
            original = target.read_bytes()
            self.assertGreaterEqual(len(original), 64)
            self.assertEqual(stat.S_IMODE(directory.stat().st_mode), 0o700)
            self.assertEqual(stat.S_IMODE(target.stat().st_mode), 0o600)
            with self.assertRaises(FileExistsError):
                secret_tool.initialize(directory)
            self.assertEqual(target.read_bytes(), original)

    def test_secret_refuses_symlink(self):
        with tempfile.TemporaryDirectory() as base:
            link = Path(base) / "secrets"
            link.symlink_to(Path(base) / "missing")
            with self.assertRaises(FileExistsError):
                secret_tool.initialize(link)
            self.assertFalse((Path(base) / "missing").exists())
