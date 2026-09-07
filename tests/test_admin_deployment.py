import json
from pathlib import Path
import unittest

ROOT = Path(__file__).resolve().parents[1]


class AdminDeploymentTests(unittest.TestCase):
    def test_private_inert_deployment_boundary(self):
        unit = (ROOT / "infra/admin/macserver-admin.service").read_text()
        for directive in ("ConditionPathExists=/etc/macserver-admin/DEPLOYMENT-APPROVED",
                          "User=macserver-admin", "Group=macserver-admin",
                          "NoNewPrivileges=true", "CapabilityBoundingSet=\n",
                          "ProtectSystem=strict", "ProtectHome=true", "IPAddressDeny=any",
                          "IPAddressAllow=localhost 100.64.0.0/10 fd7a:115c:a1e0::/48",
                          "LoadCredential=credentials.json:", "StateDirectoryMode=0700"):
            self.assertIn(directive, unit)
        self.assertNotIn("SupplementaryGroups=", unit)
        self.assertNotIn("ExecStartPre=", unit)
        lock = json.loads((ROOT / "infra/admin/runtime.lock.json").read_text())
        self.assertIs(lock["deployment_enabled"], False)
        self.assertEqual(lock["tailscale_identity_contract"], "1.102.3")
        self.assertRegex(lock["node"]["sha256"], "^[a-f0-9]{64}$")
        workflow = (ROOT / ".github/workflows/validate.yml").read_text()
        self.assertIn("node-version: '" + lock["node"]["version"] + "'", workflow)
        self.assertIn("npm run test:browser", workflow)
        self.assertIn("persist-credentials: false", workflow)
