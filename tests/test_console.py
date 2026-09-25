from datetime import datetime, timedelta, timezone
import importlib.util
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("macserver_top", ROOT / "apps/console/macserver_top.py")
top = importlib.util.module_from_spec(spec); spec.loader.exec_module(top)
NOW = datetime(2026, 9, 25, 14, 0, tzinfo=timezone.utc).timestamp()


def status(**overrides):
    value = {"format": 1, "observedAt": datetime.fromtimestamp(NOW - 5, timezone.utc).isoformat(),
             "tailscale": {"state": "healthy", "detail": "Daemon running"},
             "backup": {"state": "healthy", "detail": "Verified", "lastSuccess": "2026-09-25T03:15:00+00:00"},
             "requests": {"state": "healthy", "detail": "60 s", "perMinute": 4, "errorsPerMinute": 0},
             "services": [{"name": "db", "state": "healthy", "detail": "container running"}], "alerts": []}
    value.update(overrides)
    return value


class HostSamplingTests(unittest.TestCase):
    def fixture(self, root, cpu="cpu  100 0 100 800 0 0 0 0\ncpu0 50 0 50 400 0 0 0 0\ncpu1 50 0 50 400 0 0 0 0\n"):
        files = {"proc/stat": cpu,
                 "proc/meminfo": "MemTotal:       8000000 kB\nMemAvailable:   6000000 kB\nCached:  1000 kB\nSwapTotal: 0 kB\nSwapFree: 0 kB\n",
                 "proc/net/dev": "Inter-|\n face |\n    lo: 999 0 0 0 0 0 0 0 999 0\n  wlan0: 1000 0 0 0 0 0 0 0 500 0\nveth12: 7777 0 0 0 0 0 0 0 7777 0\n",
                 "proc/uptime": "3720.5 100.0\n", "proc/loadavg": "0.42 0.38 0.30 1/200 999\n",
                 "sys/class/hwmon/hwmon0/name": "coretemp\n", "sys/class/hwmon/hwmon0/temp1_input": "52000\n",
                 "sys/class/hwmon/hwmon0/temp1_label": "Package id 0\n", "sys/class/hwmon/hwmon0/temp2_input": "61000\n",
                 "sys/class/power_supply/ADP1/type": "Mains\n", "sys/class/power_supply/ADP1/online": "1\n",
                 "sys/class/power_supply/BAT0/type": "Battery\n", "sys/class/power_supply/BAT0/capacity": "97\n",
                 "sys/class/power_supply/BAT0/status": "Charging\n"}
        for name, content in files.items():
            path = Path(root) / name; path.parent.mkdir(parents=True, exist_ok=True); path.write_text(content)

    def test_cpu_memory_network_temperature_and_power(self):
        with tempfile.TemporaryDirectory() as root:
            self.fixture(root)
            sampler = top.Sampler(root, disk=lambda: (1000, 250))
            first = sampler.sample(100.0)
            self.assertIsNone(first["cpu"])
            self.fixture(root, "cpu  150 0 150 900 0 0 0 0\ncpu0 100 0 50 450 0 0 0 0\ncpu1 50 0 100 450 0 0 0 0\n")
            (Path(root) / "proc/net/dev").write_text("a\nb\n  wlan0: 3000 0 0 0 0 0 0 0 1500 0\n")
            snapshot = sampler.sample(102.0)
            self.assertAlmostEqual(snapshot["cpu"], 50.0)
            self.assertEqual(set(snapshot["cores"]), {"cpu0", "cpu1"})
            self.assertEqual(snapshot["memory"]["used"], 2000000 * 1024)
            self.assertEqual((snapshot["rx"], snapshot["tx"]), (1000.0, 500.0))  # lo and veth excluded
            self.assertEqual(snapshot["temperature"], 52.0)  # package sensor preferred
            self.assertEqual(snapshot["power"], {"ac": True, "battery": 97, "status": "Charging"})
            self.assertEqual(snapshot["load"], [0.42, 0.38, 0.30])

    def test_missing_host_files_become_unavailable(self):
        with tempfile.TemporaryDirectory() as root:
            snapshot = top.Sampler(root, disk=lambda: None).sample(1.0)
            self.assertIsNone(snapshot["cpu"]); self.assertIsNone(snapshot["temperature"])
            self.assertEqual(snapshot["power"], {"ac": None, "battery": None, "status": None})


class EvidenceTests(unittest.TestCase):
    def write(self, directory, value):
        path = Path(directory) / "status.json"
        path.write_text(value if isinstance(value, str) else json.dumps(value))
        return str(path)

    def test_fresh_snapshot_is_normalised_and_cleaned(self):
        with tempfile.TemporaryDirectory() as directory:
            value = status(services=[{"name": "db", "state": "healthy", "detail": "ok\x1b[31m"},
                                     {"name": "rogue", "state": "healthy"}, {"name": "auth", "state": "on fire"}],
                           alerts=[{"severity": "critical", "title": "Down\x07", "detail": "auth"}])
            evidence = top.read_status(self.write(directory, value), NOW)
            self.assertTrue(evidence["observed"]); self.assertFalse(evidence["stale"])
            self.assertEqual([s["name"] for s in evidence["services"]], ["PostgreSQL", "Auth"])
            self.assertEqual(evidence["services"][1]["state"], "unavailable")
            self.assertNotIn("\x1b", json.dumps(evidence)); self.assertNotIn("\x07", json.dumps(evidence))

    def test_stale_missing_invalid_and_future_snapshots(self):
        with tempfile.TemporaryDirectory() as directory:
            stale = status(observedAt=datetime.fromtimestamp(NOW - 120, timezone.utc).isoformat())
            self.assertTrue(top.read_status(self.write(directory, stale), NOW)["stale"])
            self.assertEqual(top.verdict(top.read_status(self.write(directory, stale), NOW)), "degraded")
            for bad in ("not json", status(format=2), status(observedAt="2026-09-25T14:00:00"),
                        status(observedAt=datetime.fromtimestamp(NOW + 60, timezone.utc).isoformat()), [1, 2]):
                self.assertFalse(top.read_status(self.write(directory, bad), NOW)["observed"], bad)
            self.assertFalse(top.read_status(str(Path(directory) / "missing.json"), NOW)["observed"])
            self.assertFalse(top.read_status(self.write(directory, "x" * (top.MAX_STATUS + 1)), NOW)["observed"])

    @unittest.skipUnless(hasattr(os, "O_NOFOLLOW"), "symlink refusal is a Linux/POSIX property")
    def test_symlinked_snapshot_is_refused(self):
        with tempfile.TemporaryDirectory() as directory:
            target = self.write(directory, status())
            link = Path(directory) / "link.json"; link.symlink_to(target)
            self.assertFalse(top.read_status(str(link), NOW)["observed"])

    def test_verdict_takes_the_worst_observed_state(self):
        self.assertEqual(top.verdict(top.unavailable("none")), "unavailable")
        evidence = top.read_status.__globals__["DEMO_EVIDENCE"]
        self.assertEqual(top.verdict(evidence), "failed")
        healthy = {**evidence, "services": [s for s in evidence["services"] if s["state"] == "healthy"],
                   "backup": {**evidence["backup"], "state": "healthy"}}
        self.assertEqual(top.verdict(healthy), "healthy")


class RenderTests(unittest.TestCase):
    def frame(self, width, height, glyphs="unicode", evidence=None):
        sampler = top.DemoSampler(); snapshot = sampler.sample(NOW)
        return top.render(width, height, snapshot, evidence or top.DEMO_EVIDENCE, sampler, NOW, glyphs, True, "macserver")

    def test_every_size_fills_the_screen_exactly(self):
        for width, height in ((80, 24), (120, 36), (160, 50), (213, 67)):
            text = self.frame(width, height).text().split("\n")
            self.assertEqual(len(text), height)
            self.assertTrue(all(len(line) == width for line in text), (width, height))
            joined = "\n".join(text)
            for expected in ("MacServer", "ACTION REQUIRED", "cpu", "mem", "net", "services", "attention", "DEMO DATA", "no controls"):
                self.assertIn(expected, joined, (width, height, expected))

    def test_console_glyphs_avoid_characters_missing_from_vt_fonts(self):
        text = self.frame(120, 36, "console").text()
        self.assertFalse(any(0x2800 <= ord(c) <= 0x28FF for c in text))  # braille
        self.assertFalse(set(text) & set("╭╮╰╯●■·"))

    def test_small_screens_explain_instead_of_overlapping(self):
        self.assertIn("at least 80x24", self.frame(60, 20).text())

    def test_missing_evidence_is_unverified_not_healthy(self):
        text = self.frame(160, 50, evidence=top.unavailable("No collector snapshot")).text()
        self.assertIn("UNVERIFIED", text); self.assertIn("No collector evidence", text)
        self.assertNotIn("NOMINAL", text)

    def test_graphs_are_bounded(self):
        rows = top.graph([0, 50, 100, None, 150, -5], 3, 2, "braille")
        self.assertEqual([len(r) for r in rows], [3, 3])
        self.assertEqual(top.graph([100], 1, 1, "blocks"), ["█"])
        self.assertEqual(top.graph([None], 1, 1, "blocks"), [" "])


class TerminalTests(unittest.TestCase):
    @unittest.skipUnless(sys.platform.startswith("linux"), "the curses smoke test needs a Linux pty")
    def test_curses_loop_draws_in_a_real_terminal_until_stopped(self):
        import fcntl, pty, select, signal, struct, termios, time
        pid, fd = pty.fork()
        if pid == 0:
            fcntl.ioctl(0, termios.TIOCSWINSZ, struct.pack("HHHH", 40, 120, 0, 0))
            os.environ.update(TERM="xterm-256color", LANG="C.UTF-8")
            os.execv(sys.executable, [sys.executable, str(ROOT / "apps/console/macserver_top.py"), "--demo", "--kiosk", "--interval", "0.5"])
        output, deadline = b"", time.time() + 4
        while time.time() < deadline:
            if select.select([fd], [], [], 0.2)[0]:
                try:
                    output += os.read(fd, 65536)
                except OSError:
                    break
        os.write(fd, b"q")  # Kiosk mode ignores keys, including q and Ctrl+C.
        time.sleep(0.8)
        self.assertEqual(os.waitpid(pid, os.WNOHANG), (0, 0), output[-2000:])
        os.kill(pid, signal.SIGTERM)
        _, code = os.waitpid(pid, 0)
        self.assertTrue(os.WIFSIGNALED(code))
        self.assertIn(b"MacServer", output); self.assertNotIn(b"Traceback", output)


if __name__ == "__main__":
    unittest.main()
