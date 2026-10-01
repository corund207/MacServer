import json
import os
import time
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

    def test_every_incident_page_size_and_mode_draws_only_allowed_characters(self):
        for rich, allowed in ((True, RICH_CHARS), (False, CONSOLE_CHARS)):
            for page in tui.PAGES:
                for cols, rows in SIZES:
                    with self.subTest(rich=rich, page=page, size=(cols, rows)):
                        c = tui_fixture.screen("trouble", cols, rows, rich=rich, page=page)
                        self.assertLessEqual(set(c.text()) - {"\n"}, allowed)
                        self.assertEqual(len(c.cells), rows)
                        self.assertTrue(all(len(r) == cols for r in c.cells))
                        self.assertEqual(c.alert, "critical")

    def test_every_page_shows_its_debug_data(self):
        wanted = {
            "incident": ("look", "info", "fix", "log", "/etc/docker/daemon.json", "1.64 GHz", "coretemp", "Composite",
                         "postgres x9", "34.5%"),
            "network": ("LISTENING PORTS", "ALL NETS", ":5432", "supabase-pooler", "3 exposed", "TAILNET", "LOCAL",
                        "INBOUND CONNECTIONS", "LAN 192.168.18.20", "tailnet jonahs-iphone",
                        "OUTBOUND CONNECTIONS", "edge-functions", "104.18.2.161", "CONNECTIVITY", "Router", "18 ms",
                        "registry-1.docker.io", "ALL NETS / LAN = answers on the Wi-Fi/LAN too."),
            "requests": ("INBOUND REQUESTS", "/rest/v1/events", "503", "LAN 192.168.18.20 (python)", "TRAFFIC",
                         "TOP REQUESTS", "TOP CALLERS", "RECENT ERRORS (15 min)", "5xx", "iOS app"),
            "system": ("CPU", "MEMORY", "THERMAL", "throttle events since boot: 14", "BUSIEST PROGRAMS", "beam.smp"),
            "logs": ("SYSTEM LOGS", "FAILED UNITS", "JOURNAL", "KERNEL", "SERVICE ERRORS", "PGRST002", "FATAL:",
                     "WHERE TO LOOK", "docker-compose.yml", "backups: 7 kept", "COMMANDS", "sudo macserver status",
                     "sudo ss -tlnp"),
        }
        for page, expected in wanted.items():
            text = tui_fixture.screen("trouble", 232, 64, page=page).text()
            for word in expected:
                with self.subTest(page=page, word=word):
                    self.assertIn(word, text)
            self.assertNotIn("public-anon-key", text)
            self.assertNotIn("eyJ", text)

    def test_pages_rotate_hold_and_jump_to_new_problems(self):
        s = tui_fixture.make_sampler("trouble")
        s.paused = False
        s.page, s.page_t, s.last_items = 0, 100.0, 5
        s.advance_page(100.0 + tui.ROTATE_S - 0.1, 5)
        self.assertEqual(s.page, 0)                                   # not yet
        s.advance_page(100.0 + tui.ROTATE_S + 0.1, 5)
        self.assertEqual(s.page, 1)
        for i in range(2, 6):
            s.advance_page(100.0 + tui.ROTATE_S * i + 0.5, 5)
        self.assertEqual(s.page, 0)                                   # wrapped round the five pages
        s.page, s.page_t = 3, 100.0
        s.paused = True
        s.advance_page(1000.0, 5)
        self.assertEqual(s.page, 3)                                   # held with p
        s.paused = False
        s.page_t = 1000.0
        s.advance_page(1001.0, 6)                                     # a new problem: back to the incident page
        self.assertEqual(s.page, 0)
        s.turn_page(-1)
        self.assertEqual(s.page, len(tui.PAGES) - 1)
        s.turn_page(1)
        self.assertEqual(s.page, 0)
        c = tui_fixture.screen("trouble", 232, 64, page="requests")
        self.assertIn("page held (p)", c.text())                      # the tab strip says so

    def test_debug_findings_become_incidents(self):
        def titles(mutate):
            s = tui_fixture.make_sampler("healthy")
            mutate(s.status["debug"], s)
            return [(i["level"], i["title"]) for i in tui.incidents(s)]

        self.assertEqual(titles(lambda d, s: None), [])                # a healthy Mac shows no incident
        exposed = titles(lambda d, s: d["listeners"].append(
            {"port": 5432, "addr": "0.0.0.0", "scope": "all", "proc": "supabase-pooler", "svc": "postgres"}))
        self.assertEqual(exposed, [("warn", "1 port(s) reachable from the local network")])
        tailnet_only = titles(lambda d, s: d["listeners"].append(
            {"port": 22, "addr": "100.89.16.53", "scope": "tailnet", "proc": "tailscaled", "svc": "ssh"}))
        self.assertEqual(tailnet_only, [])
        self.assertEqual(titles(lambda d, s: d["system"].update(failed_units=["macserver-fans.service"])),
                         [("warn", "1 systemd unit(s) failed")])
        self.assertEqual(titles(lambda d, s: d["requests"]["classes"].update({"5xx": 3}) or d["requests"]["errors"].insert(
            0, {"t": 1, "method": "GET", "path": "/rest/v1/x", "status": 500, "bytes": 0, "client": "this Mac", "agent": "curl"})),
                         [("warn", "the API answered 3 request(s) with a server error in the last minute")])
        one = titles(lambda d, s: d["net"].update(internet_ms=None, internet_fails=1))
        self.assertEqual(one, [])                                      # one missed check is not an outage
        down = titles(lambda d, s: d["net"].update(internet_ms=None, internet_fails=2))
        self.assertEqual(down, [("bad", "the Internet is unreachable")])
        self.assertEqual(titles(lambda d, s: d["net"].update(dns_ms=None, dns_fails=2)),
                         [("warn", "DNS is not resolving names")])
        oom = tui_fixture.make_sampler("healthy")
        oom.counter_changed["oom_kills"] = time.time()
        oom.status["debug"]["system"]["oom_kills"] = 2
        self.assertIn(("warn", "the kernel killed a process: out of memory"), [(i["level"], i["title"]) for i in tui.incidents(oom)])
        oom.counter_changed["oom_kills"] = time.time() - 700          # ...and the warning fades after ten minutes
        self.assertEqual(tui.incidents(oom), [])

    def test_service_incidents_carry_facts_files_and_steps(self):
        s = tui_fixture.make_sampler("trouble")
        s.status["debug"]["inspect"]["supabase-edge-functions"].update(oom=True, exit=137, health_output="")
        s.now["mem"] = {"total": 8_000_000_000, "used": 7_600_000_000}
        item = next(i for i in tui.incidents(s) if i["title"] == "service edge-functions is exited")
        self.assertEqual(item["level"], "bad")
        self.assertIn("exit code 137 · killed: OUT OF MEMORY · restarted 3 time(s)", item["facts"])
        self.assertTrue(any("machine under pressure: memory 95% used" in f for f in item["facts"]))
        self.assertTrue(any("volumes/functions" in f for f in item["files"]))
        self.assertEqual(item["fix"][-1], "sudo macserver restart")
        self.assertTrue(any("docker inspect supabase-edge-functions" in f for f in item["fix"]))
        # When the database is down as well, every other service says to fix that first.
        for c in s.status["containers"]:
            if c["name"] in ("supabase-db",):
                c["state"] = "exited"
        rest = next(i for i in tui.incidents(s) if i["title"] == "service edge-functions is exited")
        self.assertIn("the database is down too", rest["fix"][0])

    def test_small_screens_never_spill_or_crash_on_missing_debug_data(self):
        for scenario in ("trouble", "healthy"):
            for page in tui.PAGES:
                s = tui_fixture.make_sampler("trouble", page=page)
                s.status.pop("debug", None)                            # an older collector: no debug section at all
                for cols, rows in SIZES:
                    with self.subTest(scenario=scenario, page=page, size=(cols, rows)):
                        c = tui_fixture.screen("trouble", cols, rows, sampler=s)
                        self.assertEqual(len(c.cells), rows)
        s = tui_fixture.make_sampler("trouble", page="network")
        s.status["debug"] = {"error": "boom"}                          # a collector whose debug part failed
        self.assertIn("LISTENING PORTS", tui_fixture.screen("trouble", 232, 64, sampler=s).text())

    def test_processes_and_sensors_are_read_from_proc_and_sys(self):
        tui_fixture.machine(70, tick=1)
        first = tui.read_procs()
        self.assertEqual(first[101], ("postgres", 901, 60_000 * tui.PAGE_BYTES))
        tui_fixture.machine(70, tick=3)
        groups = tui.group_procs(tui.read_procs(), first, 2.0)
        top = groups["cpu"][0]
        self.assertEqual((top["name"], top["n"]), ("postgres", 2))    # two postgres processes are added together
        self.assertGreater(top["cpu"], groups["cpu"][1]["cpu"])
        self.assertEqual(groups["mem"][0]["name"], "postgres")
        sensors = {(r["chip"], r["label"]): r for r in tui.thermal_readings()}
        self.assertEqual(sensors[("coretemp", "Package id 0")]["temp"], 70)
        self.assertEqual(sensors[("nvme", "Composite")]["limit"], 85)
        self.assertEqual(sensors[("nvme", "Composite")]["temp"], 52)
        self.assertEqual(tui.fmt_ago(5), "5s")
        self.assertEqual(tui.fmt_ago(600), "10m")
        self.assertEqual(tui.fmt_ago(86400), "24h")

    def test_layout_helpers(self):
        self.assertEqual(tui.stack(2, 10, [1, 1]), [(2, 5), (7, 5)])
        self.assertEqual(sum(h for _, h in tui.stack(0, 37, [3, 2, 2])), 37)
        self.assertEqual(tui.columns(4, 100, [60, 40]), [(4, 60), (64, 40)])
        self.assertEqual(tui.fit(0, 20, [4, 6, 3], grow=2), [(0, 4), (4, 6), (10, 10)])   # spare rows go to one panel
        rows = tui.fit(0, 10, [8, 8])                                                   # too little room: shrink together
        self.assertEqual(sum(h for _, h in rows), 10)
        self.assertEqual(tui.stack(0, 0, [1, 1]), [(0, 0), (0, 0)])
        c = tui.Canvas(20, 1)
        end = tui.fields(c, 0, 0, 12, ("abcdef", tui.C["text"], 4), ("wxyz", tui.C["text"], 4), ("never", tui.C["text"], 9))
        self.assertEqual(c.text().rstrip(), "abc…wxyznev…")            # clipped to the column, never past `right`
        self.assertEqual(end, 12)

    def test_healthy(self):
        text = tui_fixture.screen("healthy", 232, 64).text()
        for expected in ("ALL SYSTEMS NORMAL", "15/15 running", "https://macserver.tail4f2a.ts.net/",
                         "i5-8210Y", "27%", "41%", "62°C", "up to date · 6bc4f51", "2400 rpm", "target 64% · min 60%", "5.2 GB", "41.2 GB",
                         "1.2 MB/s", "86.0 KB/s", "BAT ▲  80%", "3.2W", "realtime", "cloudflared",
                         "press 2 to log in"):
            self.assertIn(expected, text)
        self.assertIn(chr(0x28FF), text)                 # braille graphs are drawn
        self.assertNotIn("public-anon-key", text)       # no keys on the screen, not even public ones

    def test_idle_row_and_badge(self):
        text = tui_fixture.screen("healthy", 232, 64).text()
        self.assertIn("Idle", text)
        self.assertIn("low-power after 15m quiet", text)
        s = tui_fixture.make_sampler("healthy")
        s.status["idle"] = {"mode": "auto", "idle": True, "since": time.time() - 3600,
                            "after_s": 900, "reasons": []}
        idle_text = tui_fixture.screen("healthy", 232, 64, sampler=s).text()
        self.assertIn("IDLE", idle_text)
        self.assertIn("low-power", idle_text)

    def muted_sampler(self):
        """A healthy Mac showing exactly the two known-benign notices: the Claude
        session that failed for lack of sign-in, and a recent heat-throttle burst."""
        s = tui_fixture.make_sampler("healthy")
        s.status["debug"]["system"]["failed_units"] = ["macserver-claude.service"]
        s.status["claude"] = {"installed": True, "state": "failed", "url": "", "problem": "login"}
        s.status["debug"]["system"]["thermal_throttle"] = 82108
        s.counter_changed["thermal_throttle"] = time.time()
        return s

    def test_muted_notices_leave_the_warning_label(self):
        s = self.muted_sampler()
        titles = tui.assess(s)
        self.assertEqual(len(titles), 2)   # both notices still exist as incidents
        c = tui_fixture.screen("healthy", 232, 64, sampler=s)
        self.assertEqual(c.alert, "ok")
        self.assertIn("ALL SYSTEMS NORMAL", c.text())
        self.assertNotIn("NOTICE", c.text())

    def test_muting_is_narrow(self):
        # Another unit failing beside Claude: the label stays.
        s = self.muted_sampler()
        s.status["debug"]["system"]["failed_units"].append("macserver-fans.service")
        c = tui_fixture.screen("healthy", 232, 64, sampler=s)
        self.assertEqual(c.alert, "warning")
        self.assertIn("1 NOTICE(S)", c.text())
        # Claude failing for a real reason (not sign-in): the label stays.
        s = self.muted_sampler()
        s.status["claude"]["problem"] = ""
        c = tui_fixture.screen("healthy", 232, 64, sampler=s)
        self.assertIn("1 NOTICE(S)", c.text())
        # A genuine overheat still warns, even with throttling muted.
        s = self.muted_sampler()
        s.now["temp"] = 93
        c = tui_fixture.screen("healthy", 232, 64, sampler=s)
        self.assertIn("NOTICE", c.text())
        # Critical problems are never muted.
        s = self.muted_sampler()
        self.assertFalse(tui.muted_notice(s, "bad", "the Internet is unreachable"))

    def test_critical_shows_the_red_incident_view(self):
        c = tui_fixture.screen("trouble", 232, 64)
        text = c.text()
        self.assertEqual(c.alert, "critical")
        for expected in ("▲ CRITICAL · 5 ISSUE(S) ▲", "INCIDENT", "1. service edge-functions is exited",
                         "1) sudo docker logs --tail 50 supabase-edge-functions", "3) sudo macserver restart",
                         "exit code 1 · not killed for memory · restarted 3 time(s)",
                         "/opt/macserver/supabase/volumes/functions/",
                         'error: Module not found', "public API not answering", "running hot (93°C)",
                         "3 port(s) reachable from the local network", "LAN 192.168.18.20 → :5432",
                         "the API answered 2 request(s) with a server error",
                         "VITALS", "EVENTS", "THERMAL", "BUSIEST PROGRAMS", "SYSTEM STATE", "14/15 running",
                         "T+00:12:34", "INCIDENT   NETWORK   REQUESTS   SYSTEM   LOGS",
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
        self.assertEqual(tui.SAMPLE_S, 0.001)

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
