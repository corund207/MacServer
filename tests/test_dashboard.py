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
ASCII = set(string.printable) - set("\t\r\n\x0b\x0c")
CONSOLE_CHARS = ASCII | set(tui.GLYPHS)
RICH_CHARS = CONSOLE_CHARS | set(tui.RICH_EXTRA) | {chr(c) for c in range(0x2800, 0x2900)}
SIZES = [(232, 64), (180, 50), (140, 40), (100, 30), (80, 24), (40, 12)]


class DashboardTests(unittest.TestCase):
    def test_every_scenario_size_and_mode_draws_only_allowed_characters(self):
        for rich, allowed in ((True, RICH_CHARS), (False, CONSOLE_CHARS)):
            for scenario in ("healthy", "trouble", "setup"):
                for cols, rows in SIZES:
                    with self.subTest(rich=rich, scenario=scenario, size=(cols, rows)):
                        c = tui_fixture.screen(scenario, cols, rows, rich=rich)
                        text = c.text()
                        self.assertLessEqual(set(text) - {"\n"}, allowed)
                        self.assertEqual(len(c.cells), rows)
                        self.assertTrue(all(len(r) == cols for r in c.cells))

    def test_healthy(self):
        text = tui_fixture.screen("healthy", 232, 64).text()
        for expected in ("ALL SYSTEMS NORMAL", "15/15 running", "https://macserver.tail4f2a.ts.net/",
                         "i5-8210Y", "27%", "41%", "62°C", "fan 2400 rpm", "5.2 GB", "41.2 GB",
                         "1.2 MB/s", "86.0 KB/s", "BAT ▲  80%", "3.2W", "realtime", "cloudflared",
                         "Ctrl+Option+F2"):
            self.assertIn(expected, text)
        self.assertIn(chr(0x28FF), text)                 # braille graphs are drawn
        self.assertNotIn("public-anon-key", text)       # no keys on the screen, not even public ones

    def test_trouble_is_loud(self):
        text = tui_fixture.screen("trouble", 232, 64).text()
        for expected in ("ISSUE(S)", "edge-functions", "exited", "DOWN", "93°C",
                         "4 pending", "14/15 running"):
            self.assertIn(expected, text)
        # The stopped service is listed first.
        services = [line for line in text.splitlines() if "supabase " in line and ("running" in line or "exited" in line)]
        self.assertIn("edge-functions", services[0])

    def test_setup_running_is_not_an_error(self):
        text = tui_fixture.screen("setup", 180, 50).text()
        self.assertIn("Setup is running", text)
        self.assertNotIn("ISSUE", text)
        self.assertIn("not set up yet", text)

    def test_braille_graph(self):
        c = tui.Canvas(2, 1, rich=True)
        tui.graph(c, 0, 0, 2, 1, [0, 25, 50, 100], 100, "cpu")
        # Two samples per cell, four dots per column: 0|1 dot, then 2|4 dots.
        left2 = 0x40 | 0x04                    # dots 7 and 3
        right4 = 0x80 | 0x20 | 0x10 | 0x08     # dots 8, 6, 5 and 4
        self.assertEqual(c.text(), chr(0x2800 + 0x80) + chr(0x2800 + (left2 | right4)))

    def test_console_graph(self):
        c = tui.Canvas(3, 2, rich=False)
        tui.graph(c, 0, 0, 3, 2, [0, 75, 100], 100, "cpu")
        self.assertEqual(c.text().splitlines(), [" ▒█", " ██"])

    def test_console_colours(self):
        self.assertEqual(tui.nearest16((63, 185, 80)), 10)     # green stays green
        self.assertNotIn(tui.nearest16((150, 170, 100)), (0, 7, 8, 15))
        c = tui.Canvas(3, 1, rich=False)
        c.put(0, 0, "abc", tui.C["cpu"])
        self.assertIn("\x1b[0;1;32m", c.ansi())
        self.assertNotIn("38;2", c.ansi())

    def test_helpers(self):
        self.assertEqual(tui.clip("abcdef", 4), "abc…")
        self.assertEqual(tui.nice_top([3_440_000], 10_000), 5_000_000)
        self.assertEqual(tui.fmt_bytes(1_240_000, True), "1.2 MB/s")
        self.assertEqual(tui.grad("cpu", 0), tui.GRADIENTS["cpu"][0])

    def test_once_prints_a_summary(self):
        tui_fixture.machine()
        tui_fixture.status("healthy")
        out = subprocess.run([sys.executable, str(tui_fixture.ROOT / "admin/tui.py"), "--once"],
                             capture_output=True, text=True, env=os.environ, timeout=30, check=True).stdout
        self.assertIn("MACSERVER", out)
        self.assertIn("sudo macserver help", out)
        self.assertNotIn("\x1b[", out)                   # plain text when not on a terminal

    @unittest.skipUnless(FONT.exists(), "console font not installed (apt install console-setup-linux)")
    def test_console_glyphs_exist_in_the_mac_console_font(self):
        import tui_preview
        _, table, _, _ = tui_preview.load_font(str(FONT))
        self.assertEqual([ch for ch in tui.GLYPHS if ch not in table], [])


if __name__ == "__main__":
    unittest.main()
