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

    def test_tailscale(self):
        data = {"BackendState": "Running", "Self": {"DNSName": "macserver.tail1.ts.net.",
                                                    "TailscaleIPs": ["100.64.0.1"], "Online": True}}
        self.assertEqual(collector.tailscale(json.dumps(data))["name"], "macserver.tail1.ts.net")
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
