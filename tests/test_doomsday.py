"""Doomsday protocol: TOTP, detection scoring, state machine and repo wiring."""
import importlib.util
import json
import sys
import tempfile
import time
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


if __name__ == "__main__":
    unittest.main()
