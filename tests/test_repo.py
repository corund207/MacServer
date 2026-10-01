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
        # The daemon setting does not cover Compose networks: Supabase's published ports
        # are pinned to 127.0.0.1 by an override (every port it publishes).
        steps = (ROOT / "installer/steps/30-services.sh").read_text()
        override = steps.split("docker-compose.macserver.yml\" <<'EOF'", 1)[1].split("\nEOF", 1)[0]
        ports = re.findall(r"^\s+- (\S+)$", override, re.M)
        self.assertEqual(len(ports), 3)
        self.assertTrue(all(p.startswith("127.0.0.1:") for p in ports), ports)

    def test_firewall_default_drop_without_flushing_other_tables(self):
        rules = "\n".join(line for line in (ROOT / "host/nftables.conf").read_text().splitlines()
                          if not line.lstrip().startswith("#"))
        self.assertIn("policy drop;", rules)
        self.assertNotIn("flush ruleset", rules)
        self.assertNotRegex(rules, r"tcp dport")
        # Docker forwards published ports around the input chain: new connections from
        # the LAN must be dropped in a forward chain that runs before Docker's.
        self.assertRegex(rules, r"hook forward priority filter - 1")
        for iface in ("wl*", "en*"):
            self.assertIn(f'ct state new iifname "{iface}" drop', rules)

    def test_dns_stays_on_this_mac(self):
        conf = (ROOT / "host/resolved.conf").read_text()
        for setting in ("LLMNR=no", "MulticastDNS=no", "DNSOverTLS=opportunistic"):
            self.assertRegex(conf, rf"(?m)^{setting}$")
        # tailscaled's own lookups cannot reach Tailscale's resolver: never let it take
        # over /etc/resolv.conf (it then cannot fetch the admin page's certificate).
        steps = (ROOT / "installer/steps/30-services.sh").read_text()
        calls = re.findall(r"^\s*tailscale (?:up|set) .*$", steps, re.M)
        self.assertEqual(len(calls), 3)
        for call in calls:
            self.assertIn("--accept-dns=false", call)
        # The host step switches DNS before the dashboard, whose line ends the VM test.
        host = (ROOT / "installer/steps/20-t2.sh").read_text().split("step_host() {", 1)[1].split("\n}", 1)[0]
        self.assertLess(host.index("install_dns"), host.index("install_console"))

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

    def test_idle_mode_is_reversible_and_leaves_the_network_alone(self):
        import re
        # The controller only ever touches local power settings; networking,
        # the firewall and the public route are out of its reach.
        text = (ROOT / "admin/idle.py").read_text()
        runs = re.findall(r"subprocess\.run\(\[(.*?)\]", text)
        self.assertTrue(runs)
        for argv in runs:
            self.assertTrue(argv.strip().startswith('"docker"'), argv)
        for pattern in (r"\btailscale\s+\w+", r"\bnft(ables)?\b", r"\biptables\b",
                        r"\bufw\b", r"\bsystemctl\b", r"cloudflared"):
            self.assertIsNone(re.search(pattern, text), pattern)
        for wanted in ("scaling_governor", "backlight", "docker", "pause"):
            self.assertIn(wanted, text)
        # Installed by the host step (so updates re-apply it), on a 30 s timer.
        host = (ROOT / "installer/steps/20-t2.sh").read_text()
        self.assertIn("install_idle", host)
        self.assertIn("macserver-idle.timer", host)
        tools = (ROOT / "installer/steps/40-access.sh").read_text()
        self.assertIn("admin/idle.py", tools)
        timer = (ROOT / "host/macserver-idle.timer").read_text()
        self.assertIn("OnUnitActiveSec=30", timer)
        service = (ROOT / "host/macserver-idle.service").read_text()
        self.assertIn("/usr/local/lib/macserver/idle --check", service)
        self.assertNotIn("ProtectKernelTunables", service)  # it must be able to write /sys
        cli = (ROOT / "macserver").read_text()
        self.assertIn("idle [status|auto|on|off]", cli)
        self.assertIn("cmd_idle", cli)
        collector = (ROOT / "admin/collect_status.py").read_text()
        self.assertIn("def idle_activity", collector)
        self.assertIn("def idle_track", collector)

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
