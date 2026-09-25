import importlib.util
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("appliance", ROOT / "scripts/appliance.py")
appliance = importlib.util.module_from_spec(spec); spec.loader.exec_module(appliance)


class ApplianceTests(unittest.TestCase):
    def test_qualification_requires_exact_current_complete_evidence(self):
        now = datetime(2026, 9, 12, tzinfo=timezone.utc)
        commit = "a" * 40
        value = {"format": 1, "approvedCommit": commit, "checkedAt": now.isoformat(),
                 "checks": {name: True for name in appliance.REQUIRED_CHECKS}}
        self.assertEqual(appliance.validate_qualification(value, commit, now), value)
        for mutate in (
            lambda x: x["checks"].__setitem__("keyOnlySsh", False),
            lambda x: x.__setitem__("approvedCommit", "b" * 40),
            lambda x: x.__setitem__("checkedAt", (now - timedelta(days=8)).isoformat()),
            lambda x: x["checks"].__setitem__("keyOnlySsh", "false"),
            lambda x: x["checks"].__setitem__("keyOnlySsh", 1),
            lambda x: x.__setitem__("checkedAt", "2026-09-12T00:00:00"),
            lambda x: x.__setitem__("checkedAt", 20260912),
            lambda x: x.__setitem__("checks", []),
        ):
            candidate = {**value, "checks": dict(value["checks"])}; mutate(candidate)
            with self.assertRaises(appliance.Refusal):
                appliance.validate_qualification(candidate, commit, now)

    def test_update_requires_recent_backup_and_restore(self):
        now = datetime(2026, 9, 12, tzinfo=timezone.utc)
        value = {"state": "healthy", "lastSuccess": (now - timedelta(hours=2)).isoformat(),
                 "lastRestoreDrill": (now - timedelta(days=2)).isoformat()}
        appliance.validate_backup(value, now)
        value["lastSuccess"] = (now - timedelta(days=2)).isoformat()
        with self.assertRaises(appliance.Refusal): appliance.validate_backup(value, now)
        for invalid in (None, "2026-09-12T00:00:00", []):
            with self.assertRaises(appliance.Refusal):
                appliance.validate_backup({**value, "lastSuccess": invalid}, now)
        with self.assertRaises(appliance.Refusal): appliance.validate_backup([], now)

    def test_release_commit_requires_matching_metadata(self):
        with tempfile.TemporaryDirectory() as base:
            release = Path(base) / ("d" * 40); release.mkdir()
            with self.assertRaises(appliance.Refusal): appliance.release_commit(release)
            (release / ".macserver-release.json").write_text('{"commit": "' + "e" * 40 + '"}')
            with self.assertRaises(appliance.Refusal): appliance.release_commit(release)
            (release / ".macserver-release.json").write_text('{"commit": "' + "d" * 40 + '"}')
            self.assertEqual(appliance.release_commit(release), "d" * 40)

    def test_update_approval_is_short_lived_and_release_specific(self):
        now = datetime(2026, 9, 12, tzinfo=timezone.utc)
        value = {"format": 1, "currentCommit": "a" * 40, "targetCommit": "b" * 40,
                 "checkedAt": now.isoformat(), "writersDrained": True,
                 "dataCompatibilityReviewed": True}
        appliance.validate_update_approval(value, "a" * 40, "b" * 40, now)
        value["writersDrained"] = False
        with self.assertRaises(appliance.Refusal):
            appliance.validate_update_approval(value, "a" * 40, "b" * 40, now)

    def test_release_copy_is_new_immutable_tree(self):
        with tempfile.TemporaryDirectory() as base:
            root = Path(base); source = root / "source"; source.mkdir()
            (source / "scripts").mkdir(); script = source / "scripts/tool.py"
            script.write_text("pass\n"); script.chmod(0o755)
            with patch.object(appliance, "tracked_files", return_value=[Path("scripts/tool.py")]):
                target = appliance.install_release(source, "c" * 40, root / "releases")
                self.assertEqual((target / "scripts/tool.py").read_text(), "pass\n")
                self.assertEqual(appliance.install_release(source, "c" * 40, root / "releases"), target)
                self.assertTrue((target / ".macserver-release.json").is_file())

    def test_compose_uses_only_reviewed_profiles(self):
        with tempfile.TemporaryDirectory() as base:
            runtime = Path(base) / "runtime.env"
            runtime.write_text("# selected profiles\nCOMPOSE_PROFILES=core,storage\n")
            with patch.object(appliance, "RUNTIME_ENV", runtime):
                command = appliance.compose(Path("/release"))
                self.assertIn("core", command); self.assertIn("storage", command)
            runtime.write_text("COMPOSE_PROFILES=core,unknown\n")
            with patch.object(appliance, "RUNTIME_ENV", runtime):
                with self.assertRaises(appliance.Refusal): appliance.compose(Path("/release"))


if __name__ == "__main__": unittest.main()
