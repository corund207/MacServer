import os
from pathlib import Path
import string
import subprocess
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parent))
import tui_fixture  # noqa: E402  (points the dashboard at sample /proc, /sys, status.json)

tui = tui_fixture.tui
FONT = Path("/usr/share/consolefonts/Lat15-Terminus32x16.psf.gz")
ALLOWED = set(string.printable) - set("\t\r\x0b\x0c") | set(tui.GLYPHS)
SIZES = [(160, 50), (120, 40), (100, 30), (80, 24), (40, 12)]


class DashboardTests(unittest.TestCase):
    def test_every_scenario_and_size_draws_only_font_characters(self):
        for scenario in ("healthy", "trouble", "setup"):
            for cols, rows in SIZES:
                with self.subTest(scenario=scenario, size=(cols, rows)):
                    text = tui_fixture.screen(scenario, cols, rows).text()
                    self.assertLessEqual(set(text) - {"\n"}, ALLOWED)
                    self.assertTrue(all(len(line) <= cols for line in text.splitlines()))

    def test_healthy(self):
        text = tui_fixture.screen("healthy").text()
        self.assertIn("ALL SYSTEMS NORMAL", text)
        self.assertIn("15/15 running", text)
        self.assertIn("https://macserver.tail4f2a.ts.net/", text)
        self.assertIn("27%", text)                      # CPU from the sample /proc/stat
        self.assertIn("41.2 GB / 233 GB", text)
        self.assertIn("Ctrl + Option + F2", text)
        self.assertNotIn("public-anon-key", text)       # no keys on the screen, not even public ones

    def test_trouble_is_loud(self):
        text = tui_fixture.screen("trouble").text()
        self.assertIn("ISSUE(S)", text)
        self.assertIn("edge-functions (exited)", text)
        self.assertIn("DOWN", text)
        self.assertIn("running hot (93°C)", text)
        self.assertIn("4 pending: sudo macserver update", text)

    def test_setup_running_is_not_an_error(self):
        text = tui_fixture.screen("setup").text()
        self.assertIn("Setup is running", text)
        self.assertNotIn("ISSUE", text)
        self.assertIn("not set up yet", text)

    def test_area_chart(self):
        c = tui.Canvas(3, 2)
        tui.column_chart(c, 0, 0, 3, 2, [0, 75, 100], 100, tui.GOOD)
        self.assertEqual(c.text().splitlines(), [" ▒█", " ░░"])   # edge on top, light fill below
        c = tui.Canvas(2, 2)
        tui.column_chart(c, 0, 0, 2, 2, [50], 100, tui.GOOD)
        self.assertEqual(c.text().splitlines(), ["·", "·█"])      # no history yet: dotted

    def test_clip_and_scale(self):
        self.assertEqual(tui.clip("abcdef", 4), "abc…")
        self.assertEqual(tui.nice_top([3_440_000], 10_000), 5_000_000)
        self.assertEqual(tui.fmt_bytes(1_240_000, True), "1.2 MB/s")

    def test_once_prints_a_summary(self):
        tui_fixture.machine()
        tui_fixture.status("healthy")
        out = subprocess.run([sys.executable, str(tui_fixture.ROOT / "admin/tui.py"), "--once"],
                             capture_output=True, text=True, env=os.environ, timeout=30, check=True).stdout
        self.assertIn("MACSERVER", out)
        self.assertIn("sudo macserver help", out)
        self.assertNotIn("\x1b[", out)                   # plain text when not on a terminal

    @unittest.skipUnless(FONT.exists(), "console font not installed (apt install console-setup-linux)")
    def test_glyphs_exist_in_the_mac_console_font(self):
        import tui_preview
        _, table, _, _ = tui_preview.load_font(str(FONT))
        self.assertEqual([ch for ch in tui.GLYPHS if ch not in table], [])


if __name__ == "__main__":
    unittest.main()
