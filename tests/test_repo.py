"""Repository invariants that protect the security model."""
import json
from pathlib import Path
import re
import shutil
import subprocess
import unittest

ROOT = Path(__file__).resolve().parents[1]
SHELL = [ROOT / "install.sh", ROOT / "macserver", ROOT / "installer/lib.sh",
         *sorted((ROOT / "installer/steps").glob("*.sh"))]


class RepoTests(unittest.TestCase):
    def test_heat_alone_never_raises_a_notice_on_the_web_page(self):
        """This Mac touches its 100 C limit all day with the fan at full speed: that is normal.
        The page may show temperatures but never turn one into a warning (the screen's only
        cooling alarm is a stopped fan, tests/test_dashboard.py)."""
        app = (ROOT / "admin/static/app.js").read_text(encoding="utf-8")
        self.assertEqual(re.findall(r"temp\w*\s*[<>]=?\s*\d", app), [])
        self.assertNotIn("running hot", app)

    def test_python_modules_that_installed_scripts_import_are_installed_too(self):
        """The collector and the dashboard are installed as single files, so a sibling module they
        import (units.py) must be installed beside them or both stop with ModuleNotFoundError. CI's
        VM test only waits for a log line, so it cannot notice."""
        siblings = {p.stem for p in (ROOT / "admin").glob("*.py")}
        installer = "\n".join(p.read_text(encoding="utf-8") for p in SHELL)
        for script in ("collect_status", "tui", "idle", "fan_control", "doomsday", "terminal", "server"):
            text = (ROOT / f"admin/{script}.py").read_text(encoding="utf-8")
            for module in re.findall(r"^(?:from|import) (\w+)", text, re.M):
                if module in siblings and module != script:
                    self.assertIn(f"admin/{module}.py", installer, f"admin/{script}.py imports {module}, which no step installs")

    def test_no_admin_module_defines_the_same_function_twice(self):
        """A second `def` of a name silently replaces the first, so every test still passes while
        half the code is dead. (A bad splice once duplicated a thousand lines of the dashboard.)"""
        for path in sorted((ROOT / "admin").glob("*.py")):
            names = re.findall(r"^def (\w+)\(", path.read_text(encoding="utf-8"), re.M)
            twice = sorted({n for n in names if names.count(n) > 1})
            self.assertEqual(twice, [], f"{path.name} defines these more than once")

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

    def test_rustdesk_compose(self):
        compose = (ROOT / "rustdesk/compose.yml").read_text()
        self.assertRegex(compose, r"image:\s*rustdesk/rustdesk-server:[\w.-]+@sha256:[0-9a-f]{64}\n")
        self.assertEqual(compose.count("network_mode: host"), 1)  # shared via the anchor
        self.assertIn("restart: unless-stopped", compose)
        self.assertIn("./data:/root", compose)
        self.assertIn("-r", compose)
        self.assertIn(":21117", compose)
        self.assertNotIn("ALWAYS_USE_RELAY", compose)  # direct connections stay direct
        self.assertNotIn("id_ed25519", compose)
        self.assertNotRegex(compose, r"(?m)^\s+ports:")  # host networking: ports are not mapped

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
        # The only ports admitted from outside (besides Tailscale's UDP 41641) are the
        # self-hosted RustDesk server's: TCP 21115-21117 and UDP 21116, nothing else.
        self.assertEqual(re.findall(r"tcp dport (\{[^}]*\}|\S+)", rules), ["{ 21115, 21116, 21117 }"])
        self.assertEqual(re.findall(r"udp dport (\S+)", rules), ["41641", "21116", "68", "546"])  # + DHCP replies
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

    @unittest.skipUnless(shutil.which("bash"), "needs bash")
    def test_subcommands_reach_their_handler_with_every_argument(self):
        """main() already drops the command name, so a second shift in a branch ate the first
        argument: `macserver doomsday setup-gh` ran `doomsday` with nothing (it printed the status
        and never asked for the token) and `macserver notify test` only printed the status. Run
        the real main() with stub handlers and check each command gets all of its arguments."""
        cli = (ROOT / "macserver").read_text(encoding="utf-8")
        main = cli[cli.index("\nmain() {"):cli.rindex('\nmain "$@"')]
        stubs = "\n".join(
            f'{fn}() {{ echo "{fn}:$*"; }}'
            for fn in ("cmd_idle", "cmd_doomsday", "cmd_notify", "cmd_doctor", "cmd_reboot",
                       "need_root", "usage", "warn"))
        cases = {
            "doomsday setup-gh": "cmd_doomsday:setup-gh",
            "doomsday unlock --code 123456": "cmd_doomsday:unlock --code 123456",
            "notify test": "cmd_notify:test",
            "notify off": "cmd_notify:off",
            "idle auto": "cmd_idle:auto",
            "doctor": "cmd_doctor:",
        }
        for args, want in cases.items():
            with self.subTest(args=args):
                script = "set -u\n" + stubs + main + "\nmain " + args + "\n"
                done = subprocess.run(["bash", "-c", script], capture_output=True, text=True,
                                      timeout=20)
                self.assertEqual(done.stdout.strip(), want, done.stderr)

    def test_macserver_command_reachable_over_ssh(self):
        # /usr/local/sbin is not on unprivileged users' PATH, so SSH logins
        # would get "command not found". install_tools links the command into
        # /usr/local/bin; privileged subcommands still refuse via need_root.
        tools = (ROOT / "installer/steps/40-access.sh").read_text()
        self.assertIn("ln -sfn /usr/local/sbin/macserver /usr/local/bin/macserver", tools)
        cli = (ROOT / "macserver").read_text()
        for cmd in ("dashboard) exec", "status) cmd_status", "doctor) cmd_doctor"):
            self.assertIn(cmd, cli)
        self.assertNotIn("dashboard) need_root", cli)

    def test_workflow_steps_never_combine_run_and_uses(self):
        # A step with both `run:` and `uses:` makes GitHub reject the whole
        # workflow file (no jobs run; CI and installer-image fail). This broke
        # every notify job in ci.yml and installer-image.yml unnoticed.
        for workflow in sorted((ROOT / ".github/workflows").glob("*.yml")):
            steps, current = [], None
            for line in workflow.read_text().splitlines():
                if re.match(r"      - (\w)", line):
                    current = {"name": line.strip()[:60], "keys": set()}
                    steps.append(current)
                elif current is not None:
                    key = re.match(r"        (\w[\w-]*):", line)
                    if key:
                        current["keys"].add(key.group(1))
                    elif re.match(r"    \S", line):
                        current = None
            for step in steps:
                self.assertFalse({"run", "uses"} <= step["keys"],
                                 f"{workflow.name}: step combines run and uses: {step['name']}")

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
