from datetime import datetime, timezone
import json
from pathlib import Path
import tempfile
import unittest
from test_supabase import module

collector = module("collect_status")


class StatusCollectorTests(unittest.TestCase):
    def test_compose_json_array_and_json_lines_are_supported(self):
        row = {"Service": "db", "State": "running", "Health": "healthy"}
        self.assertEqual(collector.parse_docker_output(json.dumps([row])), [row])
        self.assertEqual(collector.parse_docker_output(json.dumps(row)), [row])
        self.assertEqual(collector.parse_docker_output(json.dumps(row) + "\n" + json.dumps(row)), [row, row])
        self.assertEqual(collector.parse_docker_output(""), [])

    def test_snapshot_maps_bounded_health_without_secrets(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            backup = root / "backup.json"
            requests = root / "requests.json"
            backup.write_text(json.dumps({"state": "healthy", "detail": "Verified", "lastSuccess": "2026-09-11T12:00:00+00:00", "secret": "ignored"}))
            requests.write_text(json.dumps({"state": "healthy", "detail": "60 second window", "perMinute": 4, "errorsPerMinute": 0}))

            def run(argv):
                if argv[0].endswith("docker"):
                    return [{"Service": "db", "State": "running", "Health": "healthy"},
                            {"Service": "auth", "State": "exited", "Health": ""}]
                return {"BackendState": "Running", "TailscaleIPs": ["100.64.0.1"]}

            value = collector.snapshot("/safe/compose.json", str(backup), str(requests), runner=run,
                                       now=datetime(2026, 9, 11, 12, tzinfo=timezone.utc))
            self.assertEqual(value["services"][0]["state"], "healthy")
            self.assertEqual(value["services"][1]["state"], "failed")
            self.assertEqual(value["tailscale"]["state"], "healthy")
            self.assertNotIn("secret", json.dumps(value))

    def test_atomic_write_and_missing_inputs(self):
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory) / "status.json"
            collector.atomic_write(target, {"format": 1})
            self.assertEqual(json.loads(target.read_text()), {"format": 1})
            self.assertEqual(target.stat().st_mode & 0o777, 0o640)
            value = collector.external_status(str(Path(directory) / "missing"), "backup")
            self.assertEqual(value["state"], "unavailable")

    def test_command_failures_become_unavailable(self):
        def fail(_argv):
            raise RuntimeError("synthetic")
        self.assertTrue(all(row["state"] == "unavailable" for row in collector.docker_services("/safe", fail)))
        self.assertEqual(collector.tailscale(fail)["state"], "unavailable")
