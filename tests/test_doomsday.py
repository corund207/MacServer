"""Doomsday protocol: TOTP, detection scoring, state machine and repo wiring."""
import importlib.util
import json
import subprocess
import sys
import tempfile
from pathlib import Path
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "admin"))
spec = importlib.util.spec_from_file_location("doomsday", ROOT / "admin/doomsday.py")
doomsday = importlib.util.module_from_spec(spec)
spec.loader.exec_module(doomsday)

# RFC 6238 SHA-1 test secret ("12345678901234567890", counter 1 -> 94287082).
RFC_SECRET = "GEZDGNBVGY3TQOJQGEZDGNBVGY3TQOJQ"


def flows(**kw):
    data = {"debug": {"flows": {"in": [], "out": []}, "listeners": [],
                      "requests": {"classes": {}}}, "live": {"procs": []}}
    for section in ("in", "out"):
        data["debug"]["flows"][section] = kw.get(section, [])
    if "listeners" in kw:
        data["debug"]["listeners"] = kw["listeners"]
    if "classes" in kw:
        data["debug"]["requests"]["classes"] = kw["classes"]
    if "procs" in kw:
        data["live"]["procs"] = kw["procs"]
    return data


class TotpTests(unittest.TestCase):
    def test_rfc6238_vectors_6_digit(self):
        self.assertEqual(doomsday.totp_code(RFC_SECRET, at=59), "287082")
        self.assertEqual(doomsday.totp_code(RFC_SECRET, at=1111111109), "081804")

    def test_verify_window(self):
        self.assertTrue(doomsday.verify_code(RFC_SECRET, "287082", at=59))
        self.assertTrue(doomsday.verify_code(RFC_SECRET, "287082", at=89))  # next 30 s step
        self.assertFalse(doomsday.verify_code(RFC_SECRET, "287082", at=119))  # two steps on
        self.assertFalse(doomsday.verify_code(RFC_SECRET, "000000", at=59))

    def test_verify_rejects_malformed(self):
        for bad in ("", "12345", "1234567", "abcdef", "12 34"):
            self.assertFalse(doomsday.verify_code(RFC_SECRET, bad, at=59), bad)

    def test_generate_and_roundtrip(self):
        secret = doomsday.generate_secret()
        self.assertRegex(secret, r"^[A-Z2-7]{32}$")
        self.assertTrue(doomsday.verify_code(secret, doomsday.totp_code(secret)))

    def test_normalize_and_url(self):
        self.assertEqual(doomsday.normalize_secret("abcd 2345"), "ABCD2345")
        url = doomsday.otpauth_url("JBSWY3DPEHPK3PXP", "macserver")
        self.assertTrue(url.startswith("otpauth://totp/MacServer:macserver?secret=JBSWY3DPEHPK3PXP"))

    def test_verify_cli_checks_an_unsaved_secret(self):
        secret = doomsday.generate_secret()
        self.assertEqual(doomsday.main(["totp-verify", "--secret", secret, doomsday.totp_code(secret)]), 0)
        self.assertEqual(doomsday.main(["totp-verify", "--secret", secret, "000000x"]), 1)

    def test_setup_shows_qr_and_verifies_before_saving(self):
        script = (Path(__file__).resolve().parent.parent / "host" / "macserver-doomsday.sh").read_text(encoding="utf-8")
        body = script.split("do_setup_totp()")[1].split("do_setup_gh()")[0]
        self.assertIn("qrencode", body)
        self.assertLess(body.index("totp-verify --secret"), body.index('> "$TOTP_FILE.tmp"', body.index("totp-verify --secret")))


class ScoringTests(unittest.TestCase):
    def setUp(self):
        self.procs, self.users = {"claude", "docker", "tailscaled", "caddy"}, {"root", "owner"}

    def test_quiet_mac_scores_zero(self):
        data = flows(out=[{"who": "this Mac", "dst": "1.1.1.1", "port": 443, "kind": "public", "svc": "https"}],
                     **{"in": [{"src": "tailnet laptop", "port": 443, "kind": "tailnet", "n": 2}]})
        score, reasons = doomsday.score_findings(data, self.procs, self.users)
        self.assertEqual((score, reasons), (0, []))

    def test_tailnet_ssh_and_loopback_never_score(self):
        data = flows(**{"in": [{"src": "tailnet laptop", "port": 22, "kind": "tailnet", "n": 1},
                                      {"src": "127.0.0.1", "port": 8090, "kind": "loopback", "n": 5}]})
        self.assertEqual(doomsday.score_findings(data, self.procs, self.users), (0, []))

    def test_lan_ssh_is_noted_but_cannot_fire_alone(self):
        data = flows(**{"in": [{"src": "LAN 192.168.1.5", "port": 22, "kind": "lan", "n": 1}]})
        score, reasons = doomsday.score_findings(data, self.procs, self.users)
        self.assertEqual(score, 1)
        self.assertTrue(any("SSH" in r for r in reasons))

    def test_tailscale_direct_inbound_ignored(self):
        data = flows(**{"in": [{"src": "LAN 192.168.18.41", "port": 41641, "kind": "lan", "n": 1}]})
        self.assertEqual(doomsday.score_findings(data, self.procs, self.users), (0, []))

    def test_ephemeral_outbound_ignored_but_low_odd_ports_score(self):
        data = flows(out=[{"who": "this Mac", "dst": "64.135.140.29", "port": 35641,
                           "kind": "public", "svc": "udp"}])
        self.assertEqual(doomsday.score_findings(data, self.procs, self.users), (0, []))
        score, _ = doomsday.score_findings(
            flows(out=[{"who": "mystery", "dst": "9.9.9.9", "port": 6667,
                        "kind": "public", "svc": "tcp"}]), self.procs, self.users)
        self.assertGreaterEqual(score, 2)

    def test_allowlisted_proc_listener_ignored(self):
        rows = [{"port": 21117, "addr": "0.0.0.0", "scope": "all", "proc": "hbbr", "svc": ""}]
        procs = self.procs | {"hbbs", "hbbr"}
        self.assertEqual(doomsday.score_findings(flows(listeners=rows), procs, self.users), (0, []))

    def test_rustdesk_plus_tailscale_stays_quiet(self):
        """The real false-positive lockdown: Tailscale direct chatter plus a
        self-hosted RustDesk server must score nothing once allowlisted."""
        data = flows(
            **{"in": [{"src": "LAN 192.168.18.41", "port": 41641, "kind": "lan", "n": 1}]},
            out=[{"who": "this Mac", "dst": "64.135.140.29", "port": 35641,
                  "kind": "public", "svc": "udp"}],
            listeners=[{"port": p, "addr": "0.0.0.0", "scope": "all",
                        "proc": "hbbr" if p == 21118 else "hbbs", "svc": ""}
                       for p in (21115, 21116, 21117, 21118, 21119)])
        procs = self.procs | {"hbbs", "hbbr"}
        self.assertEqual(doomsday.score_findings(data, procs, self.users), (0, []))

    def test_lan_inbound_scores(self):
        data = flows(**{"in": [{"src": "LAN 192.168.1.5", "port": 5432, "kind": "lan", "n": 3}]})
        score, reasons = doomsday.score_findings(data, self.procs, self.users)
        self.assertGreaterEqual(score, 3)
        self.assertTrue(any("5432" in r for r in reasons))

    def test_unexpected_public_outbound_scores(self):
        data = flows(out=[{"who": "mystery", "dst": "9.9.9.9", "port": 6667, "kind": "public", "svc": "tcp"}])
        score, _ = doomsday.score_findings(data, self.procs, self.users)
        self.assertGreaterEqual(score, 2)

    def test_verified_agent_outbound_does_not_score(self):
        data = flows(out=[{"who": "claude", "dst": "api.anthropic.com", "port": 6667, "kind": "public", "svc": "tcp"},
                          {"who": "caddy", "dst": "1.1.1.1", "port": 443, "kind": "public", "svc": "https"}])
        self.assertEqual(doomsday.score_findings(data, self.procs, self.users), (0, []))

    def test_unexpected_listener_scores(self):
        rows = [{"port": 4444, "addr": "0.0.0.0", "scope": "all", "proc": "mystery", "svc": ""}]
        score, reasons = doomsday.score_findings(flows(listeners=rows), self.procs, self.users)
        self.assertGreaterEqual(score, 2)
        self.assertTrue(any("4444" in r for r in reasons))

    def test_expected_listeners_do_not_score(self):
        rows = [{"port": 8090, "addr": "127.0.0.1", "scope": "loopback", "proc": "admin", "svc": "admin"},
                {"port": 41641, "addr": "0.0.0.0", "scope": "all", "proc": "tailscaled", "svc": "tailscale"}]
        self.assertEqual(doomsday.score_findings(flows(listeners=rows), self.procs, self.users), (0, []))

    def test_5xx_spike_scores(self):
        score, _ = doomsday.score_findings(flows(classes={"5xx": 5}), self.procs, self.users)
        self.assertGreaterEqual(score, 2)

    def test_unknown_root_process_scores(self):
        procs = [{"pid": 4242, "name": "xmrig", "user": "root", "cpu": 90.0}]
        score, reasons = doomsday.score_findings(flows(procs=procs), self.procs, self.users)
        self.assertGreaterEqual(score, 3)
        self.assertTrue(any("xmrig" in r for r in reasons))

    def test_trusted_root_process_does_not_score(self):
        procs = [{"pid": 100, "name": "docker", "user": "root", "cpu": 5.0}]
        self.assertEqual(doomsday.score_findings(flows(procs=procs), self.procs, self.users), (0, []))

    def test_multicast_and_broadcast_discovery_is_not_an_outbound_connection(self):
        """Found on the real Mac: Tailscale's port mapping sends SSDP to 239.255.255.250:1900, which scored 2
        as "unexpected outbound". Multicast never leaves the LAN. A real public host on that port still scores."""
        for dst in ("239.255.255.250", "224.0.0.251", "255.255.255.255", "ff02::c"):
            data = flows(out=[{"kind": "public", "who": "this Mac", "dst": dst, "port": 1900}])
            self.assertEqual(doomsday.score_findings(data, self.procs, self.users), (0, []), dst)
        data = flows(out=[{"kind": "public", "who": "this Mac", "dst": "203.0.113.9", "port": 1900}])
        self.assertEqual(doomsday.score_findings(data, self.procs, self.users)[0], 2)

    def test_root_inside_a_container_is_trusted_unless_turned_off(self):
        """Found on the real Mac: a Node app in Docker is root "MainThread" on the host and scored 3."""
        procs = [{"pid": 1412006, "name": "MainThread", "user": "root", "cpu": 2.5, "container": True}]
        self.assertEqual(doomsday.score_findings(flows(procs=procs), self.procs, self.users), (0, []))
        self.assertEqual(doomsday.score_findings(flows(procs=procs), self.procs, self.users, trust_containers=False)[0], 3)
        host_proc = [{"pid": 7, "name": "MainThread", "user": "root", "cpu": 2.5, "container": False}]
        self.assertEqual(doomsday.score_findings(flows(procs=host_proc), self.procs, self.users)[0], 3)
        self.assertEqual(doomsday.score_findings(flows(procs=[{"pid": 7, "name": "xmrig", "user": "root"}]),
                                                 self.procs, self.users)[0], 3)   # no container mark: still unknown

    def test_both_real_mac_findings_together_are_quiet(self):
        data = flows(out=[{"kind": "public", "who": "this Mac", "dst": "239.255.255.250", "port": 1900}],
                     procs=[{"pid": 1412006, "name": "MainThread", "user": "root", "container": True}])
        self.assertEqual(doomsday.score_findings(data, self.procs, self.users), (0, []))

    def test_the_2026_11_07_lockdown_findings_are_quiet(self):
        """The real alert (score 7): "unexpected outbound this Mac -> 1.1.1.1:853 | listener on
        172.18.0.1:8088 (python) reachable from lan | unknown root process: systemd-journal (pid 357)".
        853 is MacServer's own DNS over TLS, systemd-journal is journald, and the listener is now reported
        as "docker" by the collector. The same things somewhere else still score."""
        data = flows(out=[{"kind": "public", "who": "this Mac", "dst": "1.1.1.1", "port": 853}],
                     listeners=[{"port": 8088, "addr": "172.18.0.1", "scope": "docker", "proc": "python", "svc": ""}],
                     procs=[{"pid": 357, "name": "systemd-journal", "user": "root", "cpu": 0.0, "kernel": False}])
        procs, users = doomsday.read_allow({})                    # the shipped defaults, not this suite's set
        self.assertEqual(doomsday.score_findings(data, procs, users), (0, []))
        lan = [{"port": 8088, "addr": "192.168.18.33", "scope": "lan", "proc": "python", "svc": ""}]
        self.assertEqual(doomsday.score_findings(flows(listeners=lan), self.procs, self.users)[0], 2)
        odd = flows(out=[{"kind": "public", "who": "this Mac", "dst": "203.0.113.9", "port": 4444}])
        self.assertEqual(doomsday.score_findings(odd, self.procs, self.users)[0], 2)

    def test_kernel_threads_are_trusted_by_the_kernel_flag_not_by_name(self):
        """On the real Mac irq/9-acpi scored in the first lockdown and kworker/u16:2-kcryptd-25 right after it:
        the collector reports kernel threads without brackets. It marks them from PF_KTHREAD; a root process
        that only names itself like a kernel thread, brackets or not, still scores."""
        kernel = [{"pid": 58, "name": "irq/9-acpi", "user": "root", "cpu": 1.5, "kernel": True},
                  {"pid": 495527, "name": "kworker/u16:2-kcryptd-25", "user": "root", "kernel": True}]
        self.assertEqual(doomsday.score_findings(flows(procs=kernel), self.procs, self.users), (0, []))
        for name in ("[kworker/0:1]", "kworker/0:1", "irq/9-acpi"):
            fake = [{"pid": 4242, "name": name, "user": "root", "cpu": 50.0, "kernel": False}]
            self.assertEqual(doomsday.score_findings(flows(procs=fake), self.procs, self.users)[0], 3, name)
            unmarked = [{"pid": 4242, "name": name, "user": "root", "cpu": 50.0}]   # no mark at all
            self.assertEqual(doomsday.score_findings(flows(procs=unmarked), self.procs, self.users)[0], 3, name)

    def test_the_first_real_lockdown_scores_zero(self):
        """2026-11-04 10:58 UTC, score 13: five cloudflared connections to Cloudflare's tunnel edge on port 7844,
        named by tunnel container or, for the host-network RustDesk tunnel, "this Mac", plus irq/9-acpi.
        The allowlist here has no "cloudflared": the edge itself is what is trusted."""
        out = [{"who": who, "dst": dst, "kind": "public", "port": 7844, "svc": "udp", "n": 1}
               for who, dst in (("macserver-gateway-cloudflared-1", "198.41.192.37"),
                                ("macserver-gateway-cloudflared-1", "198.41.200.23"),
                                ("macserver-gateway-cloudflared-1", "198.41.192.107"),
                                ("this Mac", "198.41.192.77"),
                                ("macserver-apps-cloudflared-1", "198.41.200.233"),
                                ("macserver-apps-cloudflared-1", "2606:4700:a0::7"))]
        procs = [{"pid": 58, "name": "irq/9-acpi", "user": "root", "cpu": 1.5, "kernel": True}]
        self.assertEqual(doomsday.score_findings(flows(out=out, procs=procs), self.procs, self.users), (0, []))

    def test_tunnel_port_elsewhere_and_other_ports_to_cloudflare_still_score(self):
        for dst, port in (("203.0.113.9", 7844), ("198.41.200.23", 7845), ("cloudflared", 7844), ("104.16.0.1", 6667)):
            data = flows(out=[{"who": "mystery", "dst": dst, "kind": "public", "port": port, "svc": "tcp"}])
            self.assertEqual(doomsday.score_findings(data, self.procs, self.users)[0], 2, (dst, port))

    def test_mdns_listener_ignored(self):
        rows = [{"port": 5353, "addr": "0.0.0.0", "scope": "all", "proc": "avahi-daemon", "svc": "mdns"}]
        self.assertEqual(doomsday.score_findings(flows(listeners=rows), self.procs, self.users), (0, []))

    def test_container_processes_auto_trusted(self):
        conf = {"DOOMSDAY_TRUST_CONTAINERS": "true"}
        doomsday.CONF, old = Path("/nonexistent"), doomsday.CONF
        try:
            procs, _ = doomsday.read_allow(conf)
            self.assertIn("containerd-shim-runc-v2", procs)
            self.assertIn("docker-proxy", procs)
            self.assertIn("runc", procs)
        finally:
            doomsday.CONF = old

    def test_host_network_rustdesk_with_config_stays_quiet(self):
        """RustDesk + Tailscale direct + container processes = zero with config."""
        data = flows(
            **{"in": [{"src": "LAN 192.168.18.41", "port": 41641, "kind": "lan", "n": 1}]},
            out=[{"who": "this Mac", "dst": "64.135.140.29", "port": 35641,
                  "kind": "public", "svc": "udp"}],
            listeners=[{"port": p, "addr": "0.0.0.0", "scope": "all",
                        "proc": "hbbr" if p == 21118 else "hbbs", "svc": ""}
                       for p in (21115, 21116, 21117, 21118, 21119)],
            procs=[{"pid": 999, "name": "containerd-shim-runc-v2", "user": "root", "cpu": 0.1}])
        conf = {"DOOMSDAY_TRUST_CONTAINERS": "true", "DOOMSDAY_TRUST_KERNEL_THREADS": "true"}
        doomsday.CONF, old = Path("/nonexistent"), doomsday.CONF
        try:
            procs, users = doomsday.read_allow(conf)
            self.assertEqual(doomsday.score_findings(data, procs, users), (0, []))
        finally:
            doomsday.CONF = old

    def test_hysteresis_needs_consecutive_hits(self):
        state = {}
        self.assertFalse(doomsday.should_isolate(9, 6, 2, state))
        self.assertTrue(doomsday.should_isolate(9, 6, 2, state))
        self.assertFalse(doomsday.should_isolate(0, 6, 2, state))  # quiet resets
        self.assertFalse(doomsday.should_isolate(9, 6, 2, state))


class SettingsTests(unittest.TestCase):
    def test_defaults_are_on(self):
        mode, threshold, consecutive = doomsday.doomsday_settings({})
        self.assertEqual((mode, threshold, consecutive), ("on", 6, 2))

    def test_invalid_values_fall_back(self):
        mode, threshold, consecutive = doomsday.doomsday_settings(
            {"DOOMSDAY_MODE": "maybe", "DOOMSDAY_SCORE": "x", "DOOMSDAY_CONSECUTIVE": "0"})
        self.assertEqual(mode, "on")
        self.assertEqual(threshold, 6)
        self.assertEqual(consecutive, 1)  # clamped, not crashing

    def test_allow_includes_owner_and_extras(self):
        procs, users = doomsday.read_allow({"ADMIN_USER": "owner",
                                           "DOOMSDAY_TRUSTED_PROCS": "myagent",
                                           "DOOMSDAY_TRUSTED_USERS": "friend"})
        self.assertIn("myagent", procs)
        self.assertIn("claude", procs)
        self.assertIn("owner", users)
        self.assertIn("friend", users)


class AlertTests(unittest.TestCase):
    def test_payload_is_strings_and_redacted(self):
        payload = doomsday.build_alert("macserver", ["192.168.1.2", "100.1.2.3"],
                                       ["inbound lan to 5432", "token=supersecretvalue"], 9, "auto", now=1700000000)
        for value in payload.values():
            self.assertIsInstance(value, str)
        blob = " ".join(payload.values())
        self.assertNotIn("supersecretvalue", blob)
        self.assertIn("[hidden]", payload["details"])
        self.assertIn("192.168.1.2", payload["ip"])

    def test_jwt_redacted(self):
        jwt = "eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiIxIn0.SflKxwRJSMeKKF2QT4fwpMeJf36POk6yJV_adQssw5c"
        payload = doomsday.build_alert("h", ["1.2.3.4"], [jwt], 9, now=1)
        self.assertNotIn("eyJhbGci", payload["details"])


class CheckTests(unittest.TestCase):
    def write_status(self, directory, data):
        path = Path(directory) / "status.json"
        path.write_text(json.dumps(data))
        return path

    def test_off_mode_never_fires(self):
        with tempfile.TemporaryDirectory() as tmp:
            conf = Path(tmp) / "conf"
            conf.write_text("DOOMSDAY_MODE=off\n")
            doomsday.CONF, old = conf, doomsday.CONF
            try:
                path = self.write_status(tmp, flows(**{"in": [{"src": "x", "port": 1, "kind": "lan", "n": 9}]}))
                self.assertEqual(doomsday.check(source=path, state={}), (0, [], False))
            finally:
                doomsday.CONF = old

    def test_missing_status_file_is_safe(self):
        self.assertEqual(doomsday.check(source="/nonexistent/status.json", state={}), (0, [], False))

    def test_isolated_state_does_not_recheck(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = self.write_status(tmp, flows(**{"in": [{"src": "x", "port": 1, "kind": "lan", "n": 9}]}))
            self.assertEqual(doomsday.check(source=path, state={"state": "doomsday"}), (0, [], False))

    def test_attack_fires_on_second_consecutive_check(self):
        with tempfile.TemporaryDirectory() as tmp:
            conf = Path(tmp) / "conf"
            conf.write_text("DOOMSDAY_MODE=on\nDOOMSDAY_SCORE=6\nDOOMSDAY_CONSECUTIVE=2\n")
            doomsday.CONF, old = conf, doomsday.CONF
            try:
                attack = flows(**{"in": [{"src": "LAN 192.168.1.5", "port": 5432, "kind": "lan", "n": 3}]},
                               out=[{"who": "mystery", "dst": "9.9.9.9", "port": 6667,
                                     "kind": "public", "svc": "tcp"},
                                    {"who": "mystery", "dst": "9.9.9.9", "port": 6697,
                                     "kind": "public", "svc": "tcp"}])
                path = self.write_status(tmp, attack)
                state = {}
                _, _, fire1 = doomsday.check(source=path, state=state)
                self.assertFalse(fire1)
                _, _, fire2 = doomsday.check(source=path, state=state)
                self.assertTrue(fire2)
            finally:
                doomsday.CONF = old


class RepoWiringTests(unittest.TestCase):
    def test_step_registered_and_defined(self):
        install = (ROOT / "install.sh").read_text()
        self.assertIn("doomsday", install)
        defined = set()
        for script in (ROOT / "installer/steps").glob("*.sh"):
            for m in __import__("re").finditer(r"^step_(\w+)\(\)", script.read_text(), __import__("re").M):
                defined.add(m.group(1))
        self.assertIn("doomsday", defined)

    def test_self_update_reapplies_doomsday(self):
        self.assertIn("doomsday", (ROOT / "installer/self-update.sh").read_text())

    def test_cli_dispatches_doomsday(self):
        cli = (ROOT / "macserver").read_text()
        self.assertIn("doomsday", cli)
        self.assertIn("cmd_doomsday", cli)

    def test_units_and_minute_timer(self):
        self.assertTrue((ROOT / "host/macserver-doomsday.service").exists())
        timer = (ROOT / "host/macserver-doomsday.timer").read_text()
        self.assertIn("OnUnitActiveSec=60", timer)
        service = (ROOT / "host/macserver-doomsday.service").read_text()
        self.assertIn("doomsday check", service)

    def test_lockdown_firewall_cuts_everything_but_lan_ssh(self):
        rules = (ROOT / "host/nftables-doomsday.conf").read_text()
        body = "\n".join(l for l in rules.splitlines() if not l.lstrip().startswith("#"))
        self.assertNotIn("flush ruleset", body)
        self.assertGreaterEqual(body.count("policy drop;"), 3)  # input, forward, output
        self.assertIn("tcp dport 22", body)
        self.assertIn("192.168.0.0/16", body)
        self.assertNotIn("41641", body)  # Tailscale gets nothing in lockdown

    def test_alert_workflow_sends_email(self):
        workflow = (ROOT / ".github/workflows/doomsday-alert.yml").read_text()
        self.assertIn("workflow_dispatch", workflow)
        self.assertIn("dawidd6/action-send-mail@v3", workflow)
        self.assertIn("secrets.SMTP_PASSWORD", workflow)
        self.assertNotIn("smtp.gmail.com\"\n  password: plain", workflow)

    def test_collector_reports_doomsday(self):
        collector = (ROOT / "admin/collect_status.py").read_text()
        self.assertIn("def doomsday_state", collector)
        self.assertIn('"doomsday"', collector)

    def test_dashboard_shows_lockdown(self):
        tui = (ROOT / "admin/tui.py").read_text()
        self.assertIn("DOOMSDAY LOCKDOWN", tui)
        self.assertIn("doomsday DEBUG", tui)

    def test_no_secrets_in_doomsday_files(self):
        import re
        pattern = re.compile(r"(ghp_|github_pat_|-----BEGIN [A-Z ]*PRIVATE KEY|eyJ[A-Za-z0-9_-]{20,}\.)")
        for name in ("admin/doomsday.py", "host/macserver-doomsday.sh",
                     "installer/steps/50-doomsday.sh", ".github/workflows/doomsday-alert.yml"):
            self.assertIsNone(pattern.search((ROOT / name).read_text(errors="ignore")), name)


class LockdownDisplayTests(unittest.TestCase):
    """What the Mac shows while isolated must be what happened. The old banner and screen carried an
    invented event log ("17:43:31 INTRUSION DETECTED ... port 41641"), "12/12 terminated", "SCORE 20/100"
    and "LAN SSH" on a Mac with no SSH server."""
    STATE = {"since": 1_700_000_000, "trigger": "auto", "score": 9, "threshold": 6, "notified": "204",
             "reasons": ["inbound LAN connection from 192.168.1.5 to port 22 (sshd)", "listener on 0.0.0.0:9999 (nc)"]}
    INVENTED = ("17:43:31", "12/12", "/100", "87%", "21115-21119", "port 41641", "CONSECUTIVE HITS")

    def test_banner_carries_the_real_facts(self):
        text = doomsday.banner(self.STATE, "missing")
        for wanted in ("score 9 (isolates at 6 or more)", "trigger: AUTO", "inbound LAN connection from 192.168.1.5",
                       "listener on 0.0.0.0:9999", "(HTTP 204)", "sudo macserver doomsday restore"):
            self.assertIn(wanted, text)
        for invented in self.INVENTED:
            self.assertNotIn(invented, text)

    def test_banner_is_a_clean_box(self):
        lines = [l for l in doomsday.banner(self.STATE, "active").splitlines() if l[:1] in "╔║╚"]
        self.assertTrue(all(len(l) == doomsday.BANNER_W for l in lines), [len(l) for l in lines])

    def test_banner_does_not_promise_ssh_that_is_not_installed(self):
        self.assertIn("no SSH server is installed", doomsday.banner(self.STATE, "missing"))
        self.assertNotIn("192.168.x.x", doomsday.banner(self.STATE, "missing"))
        self.assertIn("SSH from the local network", doomsday.banner(self.STATE, "active"))
        self.assertIn("SSH from the local network", doomsday.banner(self.STATE, "inactive"))   # started on isolation

    def test_banner_says_when_no_alert_went_out(self):
        self.assertIn("no GitHub token", doomsday.banner({**self.STATE, "notified": ""}, "missing"))
        self.assertIn("NOT accepted (HTTP 404)", doomsday.banner({**self.STATE, "notified": "404"}, "missing"))

    def test_banner_survives_missing_data(self):
        text = doomsday.banner({}, "missing")
        self.assertIn("unknown time", text)
        self.assertIn("no detail was recorded", text)
        self.assertIn("you isolated it by hand", doomsday.banner({"trigger": "manual"}, "missing"))

    def test_cli_writes_the_banner_from_the_state_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            state = Path(tmp) / "doomsday.json"
            state.write_text(json.dumps(self.STATE))
            header = Path(tmp) / "logo"
            header.write_text("LOGO LINE\n", encoding="utf-8")
            done = subprocess.run([sys.executable, "-I", str(ROOT / "admin/doomsday.py"), "banner", "--state", str(state),
                                   "--ssh", "missing", "--header", str(header)], capture_output=True, timeout=30)
            self.assertEqual(done.returncode, 0, done.stderr)
            text = done.stdout.decode("utf-8")
            self.assertTrue(text.startswith("LOGO LINE\n╔"))
            self.assertIn("score 9", text)

    def test_the_static_logo_file_holds_no_made_up_events(self):
        logo = (ROOT / "host/doomsday-issue").read_text(encoding="utf-8")
        for invented in self.INVENTED + ("EVENT LOG", "SSH"):
            self.assertNotIn(invented, logo)
        self.assertIn("██", logo)

    def test_the_isolation_script_publishes_and_uses_the_real_state(self):
        script = (ROOT / "host/macserver-doomsday.sh").read_text(encoding="utf-8")
        self.assertIn('"$DOOMSDAY_PY" banner', script)
        self.assertIn("notified, threshold: $threshold, ssh: $ssh", script)
        self.assertNotIn("Emergency SSH: LAN only", script)
        collector = (ROOT / "admin/collect_status.py").read_text(encoding="utf-8")
        for key in ('"threshold"', '"notified"', '"ssh"'):
            self.assertIn(key, collector)
        alert = (ROOT / ".github/workflows/doomsday-alert.yml").read_text(encoding="utf-8")
        self.assertNotIn("reachable ONLY from the local network over SSH", alert)

    def test_the_lockdown_screen_has_no_invented_rows(self):
        screen = (ROOT / "admin/tui.py").read_text(encoding="utf-8")
        start = screen.index("def page_doomsday")
        page = screen[start:screen.index("\ndef page_logs", start)]
        for invented in self.INVENTED + ("hits = 2",):
            self.assertNotIn(invented, page)


if __name__ == "__main__":
    unittest.main()
