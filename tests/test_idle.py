"""Idle mode: detection already lives in the collector; this tests the controller
(admin/idle.py) that applies and restores the reversible low-power settings."""
import importlib.util
import json
from pathlib import Path
import time
import unittest

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("macserver_idle", ROOT / "admin/idle.py")
idle = importlib.util.module_from_spec(spec)
spec.loader.exec_module(idle)


def write(path, text):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text)
    return path


class SettingsTests(unittest.TestCase):
    def test_defaults(self):
        mode, after, governor, brightness, pause = idle.idle_settings({})
        self.assertEqual((mode, after, governor, brightness, pause), ("auto", 900, "powersave", 30, []))

    def test_mode_normalized_and_after_clamped(self):
        self.assertEqual(idle.idle_settings({"IDLE_MODE": "OFF"})[0], "off")
        self.assertEqual(idle.idle_settings({"IDLE_MODE": "bogus"})[0], "auto")
        self.assertEqual(idle.idle_settings({"IDLE_AFTER_S": "10"})[1], 60)
        self.assertEqual(idle.idle_settings({"IDLE_AFTER_S": "99999"})[1], 86400)
        self.assertEqual(idle.idle_settings({"IDLE_AFTER_S": "junk"})[1], 900)

    def test_brightness_and_pause_parsing(self):
        _, _, _, brightness, _ = idle.idle_settings({"IDLE_BRIGHTNESS_PCT": ""})
        self.assertIsNone(brightness)  # empty: leave the screen alone
        _, _, _, brightness, _ = idle.idle_settings({"IDLE_BRIGHTNESS_PCT": "200"})
        self.assertEqual(brightness, 100)
        _, _, governor, _, pause = idle.idle_settings(
            {"IDLE_GOVERNOR": "", "IDLE_PAUSE_CONTAINERS": "worker-1, bad;name, ,cache"})
        self.assertEqual(governor, "")
        self.assertEqual(pause, ["worker-1", "cache"])  # shell metacharacters are dropped


class WantIdleTests(unittest.TestCase):
    def test_mode_switch(self):
        self.assertFalse(idle.want_idle({"idle": {"idle": True}}, "off"))
        self.assertTrue(idle.want_idle({"idle": {"idle": False}}, "on"))
        self.assertTrue(idle.want_idle({"idle": {"idle": True}}, "auto"))
        self.assertFalse(idle.want_idle({"idle": {"idle": False}}, "auto"))
        self.assertFalse(idle.want_idle(None, "auto"))  # stale/missing: stay awake
        self.assertFalse(idle.want_idle({}, "auto"))

    def test_stale_status_is_rejected(self):
        import tempfile
        with tempfile.TemporaryDirectory() as tmp:
            status = Path(tmp) / "status.json"
            status.write_text(json.dumps({"generated_at": time.time() - 1000, "idle": {"idle": True}}))
            self.assertIsNone(idle.load_status(status))
            status.write_text(json.dumps({"generated_at": time.time(), "idle": {"idle": True}}))
            self.assertTrue(idle.load_status(status)["idle"]["idle"])


class SysfsTests(unittest.TestCase):
    def fake_sys(self, tmp):
        root = Path(tmp)
        gov = root / "devices/system/cpu/cpufreq/policy0/scaling_governor"
        write(gov, "performance\n")
        write(root / "devices/system/cpu/cpufreq/policy0/scaling_available_governors",
              "performance powersave\n")
        write(root / "class/backlight/intel_backlight/max_brightness", "1000\n")
        write(root / "class/backlight/intel_backlight/brightness", "800\n")
        return root

    def test_governors_apply_and_restore(self):
        import tempfile
        with tempfile.TemporaryDirectory() as tmp:
            root = self.fake_sys(tmp)
            saved = idle.apply_governors("powersave", root)
            self.assertEqual(saved, {str(root / "devices/system/cpu/cpufreq/policy0/scaling_governor"):
                                     "performance"})
            gov = root / "devices/system/cpu/cpufreq/policy0/scaling_governor"
            self.assertEqual(gov.read_text().strip(), "powersave")
            idle.restore_governors(saved, root)
            self.assertEqual(gov.read_text().strip(), "performance")

    def test_unknown_governor_is_left_alone(self):
        import tempfile
        with tempfile.TemporaryDirectory() as tmp:
            root = self.fake_sys(tmp)
            gov = root / "devices/system/cpu/cpufreq/policy0/scaling_governor"
            self.assertEqual(idle.apply_governors("ondemand", root), {})
            self.assertEqual(gov.read_text().strip(), "performance")

    def test_backlight_dims_and_restores(self):
        import tempfile
        with tempfile.TemporaryDirectory() as tmp:
            root = self.fake_sys(tmp)
            bright = root / "class/backlight/intel_backlight/brightness"
            saved = idle.apply_backlight(30, root)
            self.assertEqual(saved, {str(bright): 800})
            self.assertEqual(bright.read_text().strip(), "300")
            idle.restore_backlight(saved)
            self.assertEqual(bright.read_text().strip(), "800")


class CheckTests(unittest.TestCase):
    def setUp(self):
        self._run_docker = idle.run_docker

    def tearDown(self):
        idle.run_docker = self._run_docker

    def run_check(self, tmp, status_idle, conf_text, already_applied=False):
        root = Path(tmp)
        sysroot = root / "sys"
        (sysroot / "devices/system/cpu/cpufreq/policy0").mkdir(parents=True, exist_ok=True)
        write(sysroot / "devices/system/cpu/cpufreq/policy0/scaling_governor", "performance\n")
        write(sysroot / "devices/system/cpu/cpufreq/policy0/scaling_available_governors",
              "performance powersave\n")
        conf = root / "macserver.conf"
        conf.write_text(conf_text)
        status = root / "status.json"
        status.write_text(json.dumps({"generated_at": time.time(),
                                      "idle": {"mode": "auto", "idle": status_idle,
                                               "since": int(time.time()) - 9999 if status_idle else None,
                                               "after_s": 900}}))
        state_path = root / "idle-state.json"
        if already_applied:
            state_path.write_text(json.dumps({"applied": True, "governors": {}, "backlight": {}}))
        calls = []
        idle.run_docker = lambda *a, **k: calls.append(a) or ""  # no containers named: nothing to pause
        self.assertEqual(idle.check(conf, status, state_path, sysroot), 0)
        try:
            state = json.loads(state_path.read_text())
        except OSError:
            state = {}
        return state, calls

    def test_enters_and_leaves_low_power(self):
        import tempfile
        with tempfile.TemporaryDirectory() as tmp:
            gov = Path(tmp) / "sys/devices/system/cpu/cpufreq/policy0/scaling_governor"
            state, _ = self.run_check(tmp, True, "IDLE_MODE=auto\nIDLE_AFTER_S=900\n")
            self.assertTrue(state.get("applied"))
            self.assertEqual(gov.read_text().strip(), "powersave")
            state, _ = self.run_check(tmp, False, "IDLE_MODE=auto\n")
            self.assertFalse(state.get("applied", False))
            self.assertEqual(gov.read_text().strip(), "performance")

    def test_off_never_applies(self):
        import tempfile
        with tempfile.TemporaryDirectory() as tmp:
            state, _ = self.run_check(tmp, True, "IDLE_MODE=off\n")
            self.assertFalse(state.get("applied", False))

    def test_pause_list_is_honoured(self):
        import tempfile
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            sysroot = root / "sys"
            sysroot.mkdir()
            conf = root / "macserver.conf"
            conf.write_text("IDLE_MODE=auto\nIDLE_PAUSE_CONTAINERS=worker-1\n")
            status = root / "status.json"
            status.write_text(json.dumps({"generated_at": time.time(),
                                          "idle": {"mode": "auto", "idle": True,
                                                   "since": 1, "after_s": 900}}))
            state_path = root / "idle-state.json"
            paused, unpaused = [], []

            def fake(*args, **kwargs):
                if args[:1] == ("ps",):
                    return "worker-1\nother\n"
                if args[:1] == ("pause",):
                    paused.append(args[1])
                    return ""
                if args[:1] == ("unpause",):
                    unpaused.append(args[1])
                    return ""
                return ""

            idle.run_docker = fake
            self.assertEqual(idle.check(conf, status, state_path, sysroot), 0)
            self.assertEqual(paused, ["worker-1"])
            self.assertEqual(json.loads(state_path.read_text())["paused"], ["worker-1"])
            status.write_text(json.dumps({"generated_at": time.time(),
                                          "idle": {"mode": "auto", "idle": False,
                                                   "since": None, "after_s": 900}}))
            self.assertEqual(idle.check(conf, status, state_path, sysroot), 0)
            self.assertEqual(unpaused, ["worker-1"])


class SafetyTests(unittest.TestCase):
    def test_controller_never_touches_network_or_firewall(self):
        import re
        text = (ROOT / "admin/idle.py").read_text()
        # The only program it ever starts is docker, and only to pause/unpause.
        runs = re.findall(r"subprocess\.run\(\[(.*?)\]", text)
        self.assertTrue(runs)
        for argv in runs:
            self.assertTrue(argv.strip().startswith('"docker"'), argv)
        self.assertNotIn("shell=True", text)
        self.assertNotIn("os.system", text)
        # No Tailscale / firewall / tunnel / service commands anywhere.
        for pattern in (r"\btailscale\s+\w+", r"\bnft(ables)?\b", r"\biptables\b",
                        r"\bufw\b", r"\bsystemctl\b", r"cloudflared", r"\bcaddy\b"):
            self.assertIsNone(re.search(pattern, text), pattern)


if __name__ == "__main__":
    unittest.main()
