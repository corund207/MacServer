from datetime import datetime, timezone
import importlib.util
from pathlib import Path
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("release_verify", ROOT / "scripts/release_verify.py")
release = importlib.util.module_from_spec(spec)
spec.loader.exec_module(release)


class ReleaseVerificationTests(unittest.TestCase):
    def test_manifest_never_promotes_missing_target_evidence(self):
        value = release.manifest(False, runner=lambda _argv, _cwd: True,
                                 now=datetime(2026, 9, 11, tzinfo=timezone.utc))
        self.assertEqual(value["verdict"], "NO-GO")
        self.assertIs(value["targetEvidence"], False)
        self.assertIs(value["publicExposureApproved"], False)
        self.assertIsInstance(value["upstreamSynchronized"], bool)
        self.assertTrue(all(check["status"] == "pass" for check in value["checks"]))
        self.assertNotIn("command", str(value).lower())

    def test_failure_is_recorded_without_command_output(self):
        value = release.manifest(False, runner=lambda argv, _cwd: "security_posture" not in " ".join(argv))
        failed = [check for check in value["checks"] if check["status"] == "fail"]
        self.assertEqual(failed, [{"id": "security-posture", "status": "fail"}])

    def test_output_is_confined_to_ignored_build_directory(self):
        self.assertEqual(release.output_path("build/evidence.json"), ROOT / "build/evidence.json")
        with self.assertRaises(ValueError):
            release.output_path("context/evidence.json")
        with tempfile.TemporaryDirectory() as directory:
            with self.assertRaises(ValueError):
                release.output_path(str(Path(directory) / "evidence.json"))
        destination = release.BUILD / "test-release-target"
        link = release.BUILD / "test-release-link.json"
        try:
            destination.write_text("preserve")
            link.symlink_to(destination)
            with self.assertRaises(ValueError):
                release.output_path(link)
        finally:
            link.unlink(missing_ok=True)
            destination.unlink(missing_ok=True)


if __name__ == "__main__":
    unittest.main()
