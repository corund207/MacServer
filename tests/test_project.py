import importlib.util
import json
from pathlib import Path
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("project", ROOT / "scripts/project.py")
project = importlib.util.module_from_spec(spec)
spec.loader.exec_module(project)


class ProjectTests(unittest.TestCase):
    def test_bundle_scopes_routes_and_enforces_owner_rls(self):
        with tempfile.TemporaryDirectory() as folder:
            output = project.create("vexvortex", "vexvortex_items", "https://api.example.test", "https://app.example.test", Path(folder) / "new")
            config = json.loads((output / "ingress.json").read_text())
            self.assertEqual(list(config["apps"][0]["tables"]), ["vexvortex_items"])
            self.assertRegex(config["apps"][0]["key"], r"^ms_pub_[A-Za-z0-9_-]{43}$")
            sql = (output / "schema.sql").read_text()
            for required in ["FORCE ROW LEVEL SECURITY", "WITH CHECK", "USING", "GRANT REFERENCES (id)", "SET LOCAL ROLE macserver_owner"]:
                self.assertIn(required, sql)
            original = (output / "ingress.json").read_bytes()
            with self.assertRaises(ValueError):
                project.create("vexvortex", "items", "https://api.example.test", "https://app.example.test", output)
            self.assertEqual((output / "ingress.json").read_bytes(), original)

    def test_identifier_and_url_injection_refused_without_writing(self):
        with tempfile.TemporaryDirectory() as folder:
            target = Path(folder) / "new"
            for name in ["a;drop table", "../../outside", "UPPER", "rpc"]:
                with self.assertRaises(ValueError):
                    project.create(name, "items", "https://api.example.test", "https://app.example.test", target)
            for url in ["http://app.test", "https://user:password@app.test", "https://app.test/path", "https://app.test?token=secret", "https://app.test\n"]:
                with self.assertRaises(ValueError):
                    project.origin(url)
            self.assertFalse(target.exists())

    def test_ingress_network_has_no_published_ports_or_management_route(self):
        config = json.loads((ROOT / "infra/ingress/compose.json").read_text())
        self.assertEqual(config["services"]["tunnel"]["networks"], ["edge"])
        self.assertEqual(set(config["services"]["gateway"]["networks"]), {"edge", "data"})
        for service in config["services"].values():
            self.assertNotIn("ports", service)
            self.assertRegex(service["image"], r"@sha256:[a-f0-9]{64}$")
            self.assertTrue(service["read_only"])
            self.assertEqual(service["cap_drop"], ["ALL"])
            self.assertNotIn("docker.sock", json.dumps(service))
