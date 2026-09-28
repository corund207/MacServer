"""Repository invariants that protect the security model."""
import json
from pathlib import Path
import re
import unittest

ROOT = Path(__file__).resolve().parents[1]
SHELL = [ROOT / "install.sh", ROOT / "macserver", ROOT / "installer/lib.sh",
         *sorted((ROOT / "installer/steps").glob("*.sh"))]


class RepoTests(unittest.TestCase):
    def test_every_installed_source_file_exists(self):
        for script in SHELL:
            for ref in re.findall(r'\$SRC/((?:host|admin|gateway|installer)/[\w./-]+)', script.read_text()):
                if "*" in ref:
                    continue
                self.assertTrue((ROOT / ref).exists(), f"{script.name} references missing {ref}")

    def test_step_functions_exist_for_every_step(self):
        install = (ROOT / "install.sh").read_text()
        steps = re.search(r"STEPS=\(([^)]*)\)", install).group(1).split()
        defined = set(re.findall(r"^step_(\w+)\(\)", "\n".join(p.read_text() for p in SHELL), re.M))
        self.assertEqual(set(steps), defined)

    def test_images_pinned_by_digest(self):
        images = re.findall(r"image:\s*(\S+)", (ROOT / "gateway/compose.yml").read_text())
        self.assertTrue(images)
        for image in images:
            self.assertRegex(image, r":[\w.-]+@sha256:[0-9a-f]{64}$")

    def test_claude_code_pinned_and_checked(self):
        lib = (ROOT / "installer/lib.sh").read_text()
        self.assertRegex(lib, r"(?m)^CLAUDE_CODE_VERSION=\d+\.\d+\.\d+$")
        self.assertRegex(lib, r"(?m)^CLAUDE_CODE_SHA512=[0-9a-f]{128}$")
        step = (ROOT / "installer/steps/40-access.sh").read_text()
        self.assertIn('echo "$CLAUDE_CODE_SHA512  $tgz" | sha512sum -c', step)
        self.assertNotRegex(step, r"curl[^\n]*\|\s*(ba)?sh")

    def test_no_host_ports_in_gateway(self):
        self.assertNotIn("ports:", (ROOT / "gateway/compose.yml").read_text())

    def test_docker_binds_loopback(self):
        self.assertEqual(json.loads((ROOT / "host/docker-daemon.json").read_text())["ip"], "127.0.0.1")

    def test_firewall_default_drop_without_flushing_other_tables(self):
        rules = "\n".join(line for line in (ROOT / "host/nftables.conf").read_text().splitlines()
                          if not line.lstrip().startswith("#"))
        self.assertIn("policy drop;", rules)
        self.assertNotIn("flush ruleset", rules)
        self.assertNotRegex(rules, r"tcp dport")

    def test_caddy_refuses_admin_surfaces(self):
        caddy = (ROOT / "gateway/Caddyfile").read_text()
        refused = re.search(r"@refused path (.*)", caddy).group(1).split()
        for path in ("/pg/*", "/auth/v1/admin/*", "/realtime/v1/api/tenants/*", "/mcp/*", "/api/mcp/*"):
            self.assertIn(path, refused)
        self.assertRegex(caddy, r"\n\t\trespond 404\n\t}")

    def test_apt_keys_pinned_by_fingerprint(self):
        lib = (ROOT / "installer/lib.sh").read_text()
        for name in ("T2_KEY_FPR", "DOCKER_KEY_FPR", "TAILSCALE_KEY_FPR"):
            self.assertRegex(lib, name + r"=[0-9A-F]{40}\n")
        self.assertRegex(lib, r"SUPABASE_COMMIT=[0-9a-f]{40}\n")

    def test_t2_repo_limited_to_t2_packages(self):
        pref = (ROOT / "host/t2.pref").read_text()
        self.assertEqual(pref.count("Pin-Priority: -1"), 2)

    def test_published_images_never_bundle_apple_firmware(self):
        for workflow in (ROOT / ".github/workflows").glob("*.yml"):
            self.assertNotIn("MACSERVER_WIFI_FIRMWARE", workflow.read_text(), workflow.name)
        ignored = (ROOT / ".gitignore").read_text().split()
        self.assertIn("*.tar", ignored)
        self.assertIn("*.iso", ignored)
        self.assertFalse(list(ROOT.rglob("brcmfmac*")), "firmware files must never be committed")

    def test_no_secrets_committed(self):
        pattern = re.compile(r"(TUNNEL_TOKEN=\w|eyJ[A-Za-z0-9_-]{20,}\.|BEGIN [A-Z ]*PRIVATE KEY)")
        for path in ROOT.rglob("*"):
            if ".git" in path.parts or not path.is_file() or path.suffix in (".png",):
                continue
            if path.name == "test_repo.py":
                continue
            self.assertIsNone(pattern.search(path.read_text(errors="ignore")), f"possible secret in {path}")


if __name__ == "__main__":
    unittest.main()
