import importlib.util
import json
from pathlib import Path
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
BASE = Path(tempfile.mkdtemp(prefix="macserver-fans-"))
spec = importlib.util.spec_from_file_location("fan_control", ROOT / "admin/fan_control.py")
fc = importlib.util.module_from_spec(spec)
spec.loader.exec_module(fc)
# Point the controller at a fake Mac (without changing the environment other tests use).
fc.SYS, fc.PROC, fc.CONF, fc.OUT = BASE / "sys", BASE / "proc", BASE / "macserver.conf", BASE / "fans.json"


def write(rel, text):
    p = BASE / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(f"{text}\n")


def mac(temp_c=55, busy=10, total=100):
    write("sys/class/hwmon/hwmon0/name", "coretemp")
    write("sys/class/hwmon/hwmon0/temp1_input", temp_c * 1000)
    write("sys/class/hwmon/hwmon1/name", "applesmc")
    write("sys/class/hwmon/hwmon1/fan1_input", 2400)
    write("sys/class/hwmon/hwmon1/fan1_min", 1200)
    write("sys/class/hwmon/hwmon1/fan1_max", 6000)
    write("sys/class/hwmon/hwmon1/fan1_manual", 0)
    write("sys/class/hwmon/hwmon1/fan1_output", 0)
    write("proc/stat", f"cpu {busy} 0 0 {total - busy} 0 0 0 0 0 0")


class FanTests(unittest.TestCase):
    def setUp(self):
        mac()
        (BASE / "macserver.conf").write_text("")

    def test_curve(self):
        self.assertEqual(fc.target_pct(60, 0.0, 45), 60.0)          # idle and cool: the floor
        self.assertEqual(fc.target_pct(60, 0.5, 45), 80.0)          # half load: halfway to 100
        self.assertEqual(fc.target_pct(60, 0.0, 70), 80.0)          # heat alone also counts
        self.assertEqual(fc.target_pct(60, 1.0, 50), 100.0)
        self.assertEqual(fc.target_pct(60, 0.0, 96), 100.0)         # too hot: full speed
        self.assertEqual(fc.target_pct(60, 0.0, 45, previous=90), 87.0)   # falls gently
        self.assertEqual(fc.target_pct(60, 1.0, 45, previous=65), 100.0)  # rises at once

    def test_step_sets_the_fans_and_reports(self):
        smc = fc.smc()
        state = {"cpu": (0, 0)}
        mac(temp_c=45, busy=50, total=100)                           # 50% load since the last reading
        info = fc.step(smc, state)
        self.assertEqual(info["target_pct"], 80.0)
        self.assertEqual((BASE / "sys/class/hwmon/hwmon1/fan1_manual").read_text().strip(), "1")
        self.assertEqual((BASE / "sys/class/hwmon/hwmon1/fan1_output").read_text().strip(), "4800")
        report = json.loads((BASE / "fans.json").read_text())
        self.assertEqual(report["fans"][0]["target_rpm"], 4800)
        self.assertEqual(oct((BASE / "fans.json").stat().st_mode)[-3:], "644")

    def test_settings_and_fallbacks(self):
        smc = fc.smc()
        (BASE / "macserver.conf").write_text("FAN_MIN_PCT=75\n")
        mac(temp_c=45, busy=0, total=100)
        self.assertEqual(fc.step(smc, {"cpu": (0, 0)})["target_pct"], 75.0)
        (BASE / "macserver.conf").write_text("FAN_MIN_PCT=5\n")           # never below 30%
        self.assertEqual(fc.step(smc, {"cpu": (0, 0)})["target_pct"], 30.0)
        (BASE / "macserver.conf").write_text("FAN_MODE=auto\n")
        fc.step(smc, {"cpu": (0, 0)})
        self.assertEqual((BASE / "sys/class/hwmon/hwmon1/fan1_manual").read_text().strip(), "0")
        (BASE / "macserver.conf").write_text("")
        (BASE / "sys/class/hwmon/hwmon0/temp1_input").unlink()           # no temperature: hand back
        info = fc.step(smc, {"cpu": (0, 0)})
        self.assertTrue(info["mode"].startswith("auto"))
        self.assertEqual((BASE / "sys/class/hwmon/hwmon1/fan1_manual").read_text().strip(), "0")


if __name__ == "__main__":
    unittest.main()
