import importlib.util
import json
from pathlib import Path
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("collect_status", ROOT / "admin/collect_status.py")
collector = importlib.util.module_from_spec(spec)
spec.loader.exec_module(collector)


class ParserTests(unittest.TestCase):
    def test_env_parsing_ignores_comments(self):
        env = collector.parse_env("# ANON_KEY=nope\nANON_KEY=abc=def\nEMPTY=\n")
        self.assertEqual(env["ANON_KEY"], "abc=def")
        self.assertEqual(env["EMPTY"], "")

    def test_memory(self):
        mem = collector.memory("MemTotal:  8000 kB\nMemAvailable: 3000 kB\n")
        self.assertEqual(mem, {"total": 8000 * 1024, "used": 5000 * 1024})

    def test_containers(self):
        line = json.dumps({"Names": "supabase-db", "State": "running", "Status": "Up 2 hours (healthy)",
                           "Labels": "com.docker.compose.project=supabase,x=y"})
        result = collector.containers(line + "\nnot json\n")
        self.assertEqual(result, [{"name": "supabase-db", "state": "running",
                                   "status": "Up 2 hours (healthy)", "project": "supabase"}])

    def test_claude_session(self):
        log = ("\x1b[2J\x1b[1mRemote Control\x1b[0m connecting...\r\n"
               "Session: \x1b[4mhttps://claude.ai/code/session_01AbC-x_9?env=1\x1b[0m\r\n")
        info = collector.claude_session(log, "active", True)
        self.assertEqual(info["url"], "https://claude.ai/code/session_01AbC-x_9?env=1")
        self.assertEqual(info["problem"], "")
        # The log outlives the session: no link once it stopped.
        self.assertEqual(collector.claude_session(log, "inactive", True)["url"], "")
        denied = "Error: You must be logged in to use Remote Control.\n"
        self.assertEqual(collector.claude_session(denied, "failed", True)["problem"], "login")
        self.assertEqual(collector.claude_session("Enable Remote Control? (y/n)", "active", True)["problem"],
                         "consent")
        self.assertEqual(collector.claude_session("", "", False),
                         {"installed": False, "state": "inactive", "url": "", "problem": ""})

    def test_container_stats(self):
        lines = "\n".join([json.dumps({"Name": "supabase-db", "CPUPerc": "3.50%", "MemUsage": "212.4MiB / 7.6GiB"}),
                           "junk", json.dumps({"Name": "supabase-auth", "CPUPerc": "--", "MemUsage": ""})])
        stats = collector.container_stats(lines)
        self.assertEqual(stats["supabase-db"], {"cpu": 3.5, "mem": int(212.4 * 1024 ** 2)})
        self.assertNotIn("supabase-auth", stats)
        merged = collector.with_stats([{"name": "supabase-db"}, {"name": "x"}], stats)
        self.assertEqual(merged[1], {"name": "x", "cpu": None, "mem": None})

    def test_logs_are_redacted(self):
        line = ('auth error: invalid token eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiIxIn0.abc password=hunter2 '
                'Authorization: Bearer sk_live_123 key=' + "A" * 40 + ' postgres://u:p@db:5432/x')
        clean = collector.redact(line)
        for secret in ("eyJhbGci", "hunter2", "sk_live_123", "A" * 40, "u:p@db"):
            self.assertNotIn(secret, clean)
        self.assertIn("auth error: invalid token", clean)
        self.assertIn("password=[hidden]", clean)

    def test_tailscale(self):
        data = {"BackendState": "Running", "Self": {"DNSName": "macserver.tail1.ts.net.",
                                                    "TailscaleIPs": ["100.64.0.1"], "Online": True}}
        self.assertEqual(collector.tailscale(json.dumps(data))["name"], "macserver.tail1.ts.net")
        data["Peer"] = {"a": {"Online": True}, "b": {"Online": False}, "c": {"Online": True}}
        self.assertEqual(collector.tailscale(json.dumps(data))["peers_online"], 2)
        self.assertIsNone(collector.public_ok(""))
        self.assertEqual(collector.tailscale(None), {"state": "unknown"})

    def test_hwmon_and_battery(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            mon = root / "hwmon" / "hwmon0"
            mon.mkdir(parents=True)
            (mon / "name").write_text("coretemp\n")
            (mon / "temp1_input").write_text("61000\n")
            (mon / "temp2_input").write_text("72500\n")
            smc = root / "hwmon" / "hwmon1"
            smc.mkdir()
            (smc / "name").write_text("applesmc\n")
            (smc / "fan1_input").write_text("2100\n")
            self.assertEqual(collector.hwmon(root / "hwmon"), {"cpu_temp_c": 72.5, "fans_rpm": [2100]})

            bat = root / "ps" / "BAT0"
            bat.mkdir(parents=True)
            (bat / "type").write_text("Battery\n")
            (bat / "capacity").write_text("79\n")
            (bat / "status").write_text("Not charging\n")
            (bat / "charge_control_end_threshold").write_text("80\n")
            self.assertEqual(collector.battery(root / "ps"),
                             {"percent": 79, "status": "Not charging", "limit": "80"})
            self.assertIsNone(collector.battery(root / "none"))

    def test_only_public_keys_are_exported(self):
        self.assertEqual(set(collector.PUBLIC_KEYS), {"SUPABASE_PUBLISHABLE_KEY", "ANON_KEY"})


if __name__ == "__main__":
    unittest.main()
