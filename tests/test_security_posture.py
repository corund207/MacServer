import copy
import importlib.util
import json
from pathlib import Path
import unittest

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("security_posture", ROOT / "scripts/security_posture.py")
posture = importlib.util.module_from_spec(spec)
spec.loader.exec_module(posture)


class SecurityPostureTests(unittest.TestCase):
    def test_repository_posture_passes_offline(self):
        self.assertEqual(posture.audit(ROOT), [])

    def test_compose_regressions_are_detected(self):
        original = json.loads((ROOT / "infra/supabase/compose.json").read_text())
        changed = copy.deepcopy(original)
        changed["networks"]["default"]["internal"] = False
        changed["services"]["db"]["ports"] = ["5432:5432"]
        changed["services"]["db"]["image"] = "moving:latest"
        findings = "\n".join(posture.compose_findings(changed))
        self.assertIn("not internal-only", findings)
        self.assertIn("forbidden Compose key ports", findings)
        self.assertIn("not digest pinned", findings)


if __name__ == "__main__":
    unittest.main()
