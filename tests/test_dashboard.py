import json
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
    def setUp(self):
        tui.set_theme(False)        # every test starts from the normal colours
    def test_every_scenario_size_and_mode_draws_only_allowed_characters(self):
        for rich, allowed in ((True, RICH_CHARS), (False, CONSOLE_CHARS)):
            for scenario in ("healthy", "warning", "trouble", "setup"):
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
                         "i5-8210Y", "27%", "41%", "62°C", "up to date · 6bc4f51", "2400 rpm", "target 64% · min 60%", "5.2 GB", "41.2 GB",
                         "1.2 MB/s", "86.0 KB/s", "BAT ▲  80%", "3.2W", "realtime", "cloudflared",
                         "press 2 to log in"):
            self.assertIn(expected, text)
        self.assertIn(chr(0x28FF), text)                 # braille graphs are drawn
        self.assertNotIn("public-anon-key", text)       # no keys on the screen, not even public ones

    def test_critical_shows_the_red_incident_view(self):
        c = tui_fixture.screen("trouble", 232, 64)
        text = c.text()
        self.assertEqual(c.alert, "critical")
        for expected in ("▲ CRITICAL · 3 ISSUE(S) ▲", "INCIDENT", "1. service edge-functions is exited",
                         "fix   sudo docker logs --tail 50 supabase-edge-functions", "sudo macserver restart",
                         'error: Module not found', "public API not answering", "running hot (93°C)",
                         "VITALS", "EVENTS", "SYSTEM STATE", "SERVICES  14/15 running",
                         "space: incident / overview"):
            self.assertIn(expected, text)
        self.assertIn("█", text.splitlines()[2])                    # the block-letter CRITICAL banner
        self.assertEqual(c.cells[30][100][2], tui.RED_THEME[0]["bg"])    # the whole screen is red
        tui.set_theme(False)

    def test_space_shows_the_normal_dashboard_during_an_incident(self):
        c = tui_fixture.screen("trouble", 232, 64, overview=True)
        text = c.text()
        for expected in ("SERVICES", "edge-functions", "exited", "14/15 running", "4 pending"):
            self.assertIn(expected, text)
        services = [line for line in text.splitlines() if "supabase " in line and ("running" in line or "exited" in line)]
        self.assertIn("edge-functions", services[0])                # the stopped service first
        self.assertEqual(c.alert, "critical")                       # still red, still flashing
        tui.set_theme(False)

    def test_warning_pulses_the_edge_orange(self):
        c = tui_fixture.screen("warning", 232, 64)
        self.assertEqual(c.alert, "warning")
        self.assertIn("1 NOTICE(S)", c.text())
        self.assertNotIn("INCIDENT", c.text())
        edge = c.cells[20][0][2]
        self.assertNotEqual(edge, tui.C["bg"])
        self.assertGreater(edge[0], edge[2])                        # orange-ish, not blue
        pill_bg = next(cell[2] for cell in c.cells[0] if "N" in cell[0] and cell[2] == tui.C["warn"])
        self.assertEqual(pill_bg, tui.C["warn"])                    # the status pill keeps its colour
        frame = c.ring_ansi(tui.ring_colour("warning", 0.35))
        self.assertIn("\x1b[21;1H", frame)                          # only edge cells are re-sent
        self.assertLess(len(frame), len(c.ansi(full_frame=True)) // 4)
        a, b = tui.ring_colour("critical", 0.0), tui.ring_colour("critical", 0.4)
        self.assertNotEqual(a, b)                                   # critical flashes

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

    def test_kiosk_keys_switch_screens(self):
        self.assertEqual(tui.wanted_screen(b"2"), 2)
        self.assertEqual(tui.wanted_screen(b"\x1b5"), 5)        # Option+5
        self.assertEqual(tui.wanted_screen(b"\x00"), 2)         # Ctrl+2
        for ignored in (b"q", b"\x03", b"1", b"7", b"\x1b[A", b""):
            self.assertIsNone(tui.wanted_screen(ignored))
        original = tui.VT_REQUEST
        try:
            tui.VT_REQUEST = tui_fixture.BASE / "vt"
            tui.request_screen(2)
            self.assertEqual((tui_fixture.BASE / "vt").read_text(), "2\n")
        finally:
            tui.VT_REQUEST = original

    def test_events_from_status_changes(self):
        ev = tui.Events()
        old = {"containers": [{"name": "supabase-db", "state": "running"}, {"name": "supabase-auth", "state": "running"}],
               "tailscale": {"state": "Running", "peers_online": 2}, "public_ok": True, "public_domain": "api.x",
               "macserver_update": {"state": "up-to-date", "latest": "a", "message": "running the newest version"},
               "claude": {"state": "inactive"}, "host": {"updates_pending": 0}}
        new = json.loads(json.dumps(old))
        new["containers"][1]["state"] = "exited"
        new["tailscale"]["peers_online"] = 3
        new["public_ok"] = False
        new["macserver_update"] = {"state": "updated", "latest": "b", "message": "now running b"}
        new["claude"]["state"] = "active"
        new["host"]["updates_pending"] = 2
        tui.status_events(ev, old, new)
        texts = [t for _, _, t in ev.items]
        for expected in ("service auth: running → exited", "3 device(s) online on the tailnet",
                         "public API not answering", "MacServer: now running b", "Claude session started",
                         "2 Debian update(s) waiting"):
            self.assertIn(expected, texts)
        self.assertEqual(dict((t, lvl) for _, lvl, t in ev.items)["service auth: running → exited"], "bad")
        tui.status_events(ev, new, new)                      # nothing changed: nothing logged
        self.assertEqual(len(ev.items), len(texts))

    def test_events_log_edges_once(self):
        ev = tui.Events()
        for on in (True, True, True, False, False, True):
            ev.edge("cpu", on, "warn", "CPU busy", "CPU back to normal")
        self.assertEqual([t for _, _, t in reversed(ev.items)], ["CPU busy", "CPU back to normal", "CPU busy"])

    def test_busy_panels(self):
        text = tui_fixture.screen("trouble", 232, 64, overview=True).text()
        for expected in ("EVENTS", "service edge-functions: running → exited", "TREND", "tailnet",
                         "ctx 12.3k/s", "irq 4.1k/s", "tasks 2/412", "▲ read", "▼ write 307 KB/s"):
            self.assertIn(expected, text)
        lines = [line for line in text.splitlines() if "EVENTS" in line or ":" in line[-60:]]
        self.assertTrue(lines)
        self.assertEqual(tui.fmt_count(12_300), "12.3k")
        self.assertEqual(tui.SAMPLE_S, 0.5)

    def test_readings_are_smoothed(self):
        tui_fixture.machine()
        s = tui.Sampler()
        shown = []
        for i in range(40):                          # a jumpy CPU: 10%, 90%, 10%, 90% ...
            s.now.update(cpu=10.0 if i % 2 else 90.0, cores=[10.0, 90.0], rx=0.0 if i % 2 else 2e6)
            s.smooth_readings()
            shown.append(s.now["cpu"])
        swing = max(shown[-10:]) - min(shown[-10:])
        self.assertLess(swing, 40)                   # raw swing is 80 points
        self.assertAlmostEqual(sum(shown[-10:]) / 10, 50, delta=8)
        self.assertEqual(s.raw["cpu"], 10.0)         # the raw reading is kept too
        for _ in range(12):                          # a real change: shown within a few seconds
            s.now.update(cpu=100.0, cores=[100.0, 100.0], rx=0.0)
            s.smooth_readings()
        self.assertGreater(s.now["cpu"], 95)
        self.assertEqual(tui.ema(10, 20, 0.5), 15)

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
