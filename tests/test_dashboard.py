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
        # a port the firewall admits on purpose, or drops, is not "reachable from the local network"
        self.assertEqual(titles(lambda d, s: d["listeners"].extend([
            {"port": 21115, "addr": "0.0.0.0", "scope": "open", "proc": "hbbs", "svc": "rustdesk"},
            {"port": 21118, "addr": "0.0.0.0", "scope": "blocked", "proc": "hbbs", "svc": "rustdesk web"}])), [])
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

    def test_small_screens_never_spill_or_crash_on_missing_data(self):
        for scenario in ("trouble", "healthy", "warning"):
            for missing in (("debug",), ("system",), ("live",), ("system", "live", "debug")):
                s = tui_fixture.make_sampler(scenario)
                for key in missing:
                    s.status.pop(key, None)                            # an older collector: no such section at all
                for cols, rows in SIZES:
                    with self.subTest(scenario=scenario, missing=missing, size=(cols, rows)):
                        c = tui_fixture.screen(scenario, cols, rows, sampler=s)
                        self.assertEqual(len(c.cells), rows)
                        self.assertTrue(all(len(r) == cols for r in c.cells))
        s = tui_fixture.make_sampler("healthy")
        s.status.pop("system")
        text = tui_fixture.screen("healthy", 232, 64, sampler=s).text()
        for expected in ("MEMORY", "5.2 GB", "analytics", "CONNECTIONS", "41.2 GB"):    # what it can still show
            self.assertIn(expected, text)

    def test_processes_are_read_from_proc(self):
        tui_fixture.machine(70, tick=1)
        first = tui.read_procs()
        self.assertEqual(first[101], ("postgres", 901, 60_000 * tui.PAGE_BYTES))
        tui_fixture.machine(70, tick=3)
        groups = tui.group_procs(tui.read_procs(), first, 2.0)
        top = groups["cpu"][0]
        self.assertEqual((top["name"], top["n"]), ("postgres", 2))    # two postgres processes are added together
        self.assertGreater(top["cpu"], groups["cpu"][1]["cpu"])
        self.assertEqual(groups["mem"][0]["name"], "postgres")
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
                         "CPU", "MEMORY", "DISK", "NETWORK", "THERMAL", "4 threads · 1.64 GHz", "0.62  0.48  0.41",
                         "5.2 GB of 8.2 GB", "41.2 GB of 233 GB", "1h ago", "fan 2400 rpm", "▼ 1.2 MB/s", "▲ 86.0 KB/s",
                         "up to date · 6bc4f51", "BAT ▲  80%", "3.2W",
                         "SERVICES", "NAME", "STATE", "SUPABASE", "13/13", "GATEWAY", "realtime", "cloudflared", "3d",
                         "DEVICES", "TAILNET", "jonahs-iphone", "100.1.1.2", "iOS", "direct", "relay nyc", "30d ago",
                         "LOCAL NETWORK", "router (Nokia)", "192.168.18.1", "Living Room Roku Streaming Stick+",
                         "CONNECTIONS", "EVENTS", "press 2 to log in"):
            self.assertIn(expected, text)
        self.assertNotIn("NEEDS ATTENTION", text)        # nothing wrong: no issues panel
        self.assertIn(chr(0x28FF), text)                 # braille graphs are drawn
        self.assertNotIn("public-anon-key", text)       # no keys on the screen, not even public ones
        for gone in ("OVERVIEW", "next page", "n/b", "page held"):
            self.assertNotIn(gone, text)                 # one page: nothing turns, no keys to learn
        c = tui_fixture.screen("healthy", 232, 64)
        self.assertEqual(c.cells[30][120][2], (0, 0, 0))  # a black background

    def test_metric_panels_fit_any_size(self):
        for cols, rows in SIZES:
            for m in tui.metrics(tui_fixture.make_sampler("healthy")):
                with self.subTest(size=(cols, rows), metric=m["title"]):
                    small = tui.Canvas(cols, rows)
                    tui.metric_panel(small, 0, 0, cols, rows, m)          # any size: no spill, no crash
                    self.assertEqual(len(small.cells), rows)
                    self.assertTrue(all(len(r) == cols for r in small.cells))

    def test_devices_on_the_tailnet_and_the_local_network(self):
        self.assertEqual(tui.gateway("wlp3s0"), "192.168.18.1")
        lan = tui.lan_neighbours("wlp3s0")
        self.assertEqual([ip for ip, _ in lan], ["192.168.18.1", "192.168.18.9", "192.168.18.20", "192.168.18.34"])
        s = tui_fixture.make_sampler("healthy")
        lines = tui.device_lines(s)
        self.assertEqual([e[1] for e in lines if e[0] == "head"], ["tailnet", "local network"])
        devices = {e[1][0]: e for e in lines if e[0] == "dev"}
        self.assertEqual(devices["desktop-nlhe9il"][2], "off")            # an offline device is greyed
        self.assertEqual(devices["router (Nokia)"][1][2], "router")
        roku = devices["Living Room Roku Streaming Stick+"]                          # the full name the device gave
        self.assertEqual(roku[1][2:4], ("TV streamer", "Roku OS 16.0.4"))
        self.assertEqual((roku[1][5], roku[2]), ("Roku 3810RW · Roku", "ok"))
        phone = devices["phone, tablet or laptop · ec:c8"]
        self.assertEqual(phone[1][2:5], ("phone, tablet or laptop", "iOS or Android", "2h ago"))
        self.assertEqual((phone[1][5], phone[2]), ("NEW private address", "warn"))   # new and not marked yours: amber
        self.assertIn("1 new", [e[2] for e in lines if e[0] == "head"][1])
        text = tui_fixture.screen("healthy", 232, 64).text()
        for expected in ("Living Room Roku Streaming Stick+", "Amazon Echo", "Fire OS", "SYSTEM", "SEEN"):
            self.assertIn(expected, text)
        wide = tui_fixture.screen("healthy", 300, 64).text()                 # room for the model and maker too
        for expected in ("DETAIL", "Roku 3810RW · Roku", "NEW private address"):
            self.assertIn(expected, wide)
        s.status["system"]["network"].pop("neighbours")                   # no scan yet: the ARP table alone
        bare = {e[1][0]: e for e in tui.device_lines(s) if e[0] == "dev"}
        self.assertEqual(bare["76:da:88:94:ec:c8"][1][2], "private MAC")
        s.status["system"].pop("tailscale")
        s.status["system"]["network"]["neighbours"] = []
        s.now["lan"] = []
        c = tui.Canvas(80, 6)
        tui.devices_panel(c, s, 0, 0, 80, 6)
        self.assertIn("No devices yet", c.text())

    def test_network_watch_alerts_are_incidents(self):
        s = tui_fixture.make_sampler("healthy")
        watch = s.status["system"]["network"]["lan_watch"]
        watch["alerts"] = [{"level": "bad", "title": "another device is answering as your router", "why": "spoofing",
                            "fix": ["macserver devices", "sudo macserver devices accept-router"]},
                           {"level": "warn", "title": "new device on your network: aa:bb:cc:00:00:01", "why": "Galaxy-S24",
                            "fix": ["sudo macserver devices trust 192.168.18.77"]}]
        found = {i["title"]: i for i in tui.incidents(s)}
        self.assertEqual(found["another device is answering as your router"]["level"], "bad")
        self.assertEqual(found["new device on your network: aa:bb:cc:00:00:01"]["fix"], ["sudo macserver devices trust 192.168.18.77"])
        watch["alerts"], watch["stale"] = [], True
        self.assertIn("the network watch is not running", [i["title"] for i in tui.incidents(s)])

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
        # A real cooling fault (a hot CPU, a fan that is not turning) still warns, even with throttling muted.
        s = self.muted_sampler()
        s.now.update(temp=97, fans=[0])
        c = tui_fixture.screen("healthy", 232, 64, sampler=s)
        self.assertIn("NOTICE", c.text())
        # Critical problems are never muted.
        s = self.muted_sampler()
        self.assertFalse(tui.muted_notice(s, "bad", "the Internet is unreachable"))

    def test_a_notice_says_what_it_is_on_the_screen_and_in_the_terminal(self):
        """The owner saw "1 NOTICE(S)" with nothing on the screen saying what it was (a pending reboot):
        the panel under the top bar spells it out, and `macserver issues` prints the same list."""
        for cols, rows in ((232, 64), (100, 30), (80, 24)):
            with self.subTest(size=(cols, rows)):
                text = tui_fixture.screen("warning", cols, rows).text()
                top = text.splitlines()[:4]
                self.assertIn("1 NOTICE(S)", top[0])
                self.assertIn("NEEDS ATTENTION", top[1])
                self.assertIn("restart needed for updates", top[2])
                self.assertIn("fix  sudo reboot", top[3])
        self.assertIn("details and fixes: macserver issues", tui_fixture.screen("warning", 232, 64).text())
        self.assertNotIn("macserver issues", tui_fixture.screen("healthy", 232, 64).text())
        small = tui_fixture.screen("trouble", 100, 30).text()
        self.assertIn("service edge-functions is exited", small)
        self.assertIn("more: macserver issues", small)                  # what does not fit is counted

        s = tui_fixture.make_sampler("warning")
        report = tui.issues_report(s)
        for expected in ("1 notice(s)", "1. NOTICE  restart needed for updates", "why   the kernel or a core library",
                         "fix   sudo reboot", "look  /run/reboot-required"):
            self.assertIn(expected, report)
        self.assertEqual(tui.issues_report(s, brief=True).splitlines(),
                         ["1 notice(s)", "1. NOTICE  restart needed for updates", "details and fixes: macserver issues"])
        self.assertEqual(tui.issues_report(tui_fixture.make_sampler("healthy")),
                         "All systems normal: the screen shows no notices or alerts.")
        trouble = tui.issues_report(tui_fixture.make_sampler("trouble"), brief=True).splitlines()
        self.assertTrue(trouble[0].startswith("2 critical · "))
        self.assertTrue(trouble[1].startswith("1. CRITICAL  "))
        # Muted notices stay out of the terminal list exactly as they stay out of the pill.
        self.assertIn("All systems normal", tui.issues_report(self.muted_sampler()))

    def test_heat_alone_is_never_a_notice_only_a_stopped_fan_is(self):
        """This Mac sits in the 90s and touches the CPU's 100 °C limit many times a minute with
        the fan at full speed. That must never raise a notice, turn the screen amber or read
        "warm"/"hot". Only a warm CPU whose fan is not turning is a fault."""
        for temp in (62, 80, 90, 95, 100):
            with self.subTest(temp=temp):
                s = tui_fixture.make_sampler("healthy")
                s.now.update(temp=temp, fans=[8071])
                self.assertEqual(tui.assess(s), [])
                c = tui_fixture.screen("healthy", 232, 64, sampler=s)
                self.assertEqual(c.alert, "ok")
                self.assertIn("ALL SYSTEMS NORMAL", c.text())
                for word in ("warm", "hot", "HOT", "NOT TURNING"):
                    self.assertNotIn(word, c.text())
        # No fan reading at all (some Macs expose none) is not a fault either, nor is a cool CPU with a stopped fan.
        for temp, fans in ((97, []), (97, None), (60, [0]), (None, [0])):
            s = tui_fixture.make_sampler("healthy")
            s.now.update(temp=temp, fans=fans)
            self.assertEqual(tui.assess(s), [], (temp, fans))
        s = tui_fixture.make_sampler("healthy")
        s.now.update(temp=90, fans=[0])
        self.assertEqual(tui.assess(s), [("warn", "the fan is not turning (90°C)")])
        # The event log carries the same fault, once, and says when the fan is back.
        ev = tui.Events()
        for temp, fans, expected in ((97, [8071], 0), (97, [0], 1), (97, [0], 1), (97, [2400], 2)):
            stalled = tui.cooling_fault(temp, fans)
            ev.edge("cooling", stalled is not None, "bad", f"cooling fault: {stalled}", "fan turning again")
            self.assertEqual(len(ev.items), expected)
        # Temperature numbers stay on a calm colour scale: 100 °C is not alarm red.
        self.assertEqual([tui.temp_pos(t) for t in (30, 100)], [0.0, 0.8])
        red = tui.GRADIENTS["temp"][-1]
        self.assertNotEqual(tui.grad("temp", tui.temp_pos(100)), red)

    # --- the home pages --------------------------------------------------------------------------------------

    def test_metrics_keep_the_first_panels_on_narrow_screens(self):
        wide = tui_fixture.screen("healthy", 232, 64).text().splitlines()[1]
        for title in ("CPU", "MEMORY", "DISK", "NETWORK", "THERMAL"):
            self.assertIn(title, wide)                                # five metrics across 232 columns
        narrow = tui_fixture.screen("healthy", 100, 30).text().splitlines()[1]
        for title in ("CPU", "MEMORY"):
            self.assertIn(title, narrow)
        self.assertNotIn("THERMAL", narrow)                           # the least important go first
        self.assertEqual([k["title"] for k in tui.metrics(tui_fixture.make_sampler("healthy"))],
                         ["cpu", "memory", "disk", "network", "thermal"])
        # the temperature is a reading, red only for a stopped fan
        hot = tui_fixture.make_sampler("healthy")
        hot.now.update(temp=100, fans=[8000])
        self.assertNotEqual(next(k for k in tui.metrics(hot) if k["title"] == "thermal")["vcolour"], tui.C["bad"])
        hot.now.update(fans=[0])
        self.assertEqual(next(k for k in tui.metrics(hot) if k["title"] == "thermal")["vcolour"], tui.C["bad"])
        # the disk panel says when the newest backup was made, and warns when the drive needs attention
        s = tui_fixture.make_sampler("healthy")
        s.status["debug"]["backups"] = {"count": 3, "newest_age_s": 3 * 86400, "drive": {"present": False,
                                        "error": "the backup drive is not plugged in"}}
        disk = next(k for k in tui.metrics(s) if k["title"] == "disk")
        self.assertIn(("last backup", "needs attention", tui.C["warn"]), disk["stats"])

    def test_critical_puts_the_issues_on_top_and_flashes_the_edge(self):
        c = tui_fixture.screen("trouble", 232, 64)
        text = c.text()
        self.assertEqual(c.alert, "critical")
        for expected in ("▲ CRITICAL · 5 ISSUE(S) ▲", "NEEDS ATTENTION", "service edge-functions is exited",
                         "1) sudo docker logs --tail 50 supabase-edge-functions", "3) sudo macserver restart",
                         "public API not answering", "the fan is not turning (97°C)",
                         "3 port(s) reachable from the local network",
                         "the API answered 2 request(s) with a server error",
                         "CPU", "THERMAL", "FAN NOT TURNING", "EVENTS", "CONNECTIONS", "14/15 running"):
            self.assertIn(expected, text)
        lines = text.splitlines()
        self.assertIn("NEEDS ATTENTION", lines[1])                  # the issues come first
        header = next(i for i, line in enumerate(lines) if "SUPABASE" in line and "12/13" in line)
        self.assertIn("edge-functions", lines[header + 1])          # the stopped service leads its app
        self.assertIn("exited", lines[header + 1])
        for gone in ("INCIDENT", "space:", "n/b", "next page"):
            self.assertNotIn(gone, text)
        self.assertEqual(c.cells[30][100][2], (0, 0, 0))            # the screen stays black; the edge flashes red

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

    def test_debian_packages_waiting_are_information_not_a_failed_update(self):
        """The owner saw "Updates 6 pending" as a warning right after MacServer updated itself: the row is
        about Debian's packages, says which install by themselves, and is not a warning."""
        self.assertEqual(tui.debian_row({}), ("ok", "Debian", "packages up to date"))
        self.assertEqual(tui.debian_row({"updates_pending": 3, "updates_security": 3}),
                         ("info", "Debian", "3 security fix(es) waiting · installed automatically each morning"))
        self.assertEqual(tui.debian_row({"updates_pending": 6, "updates_security": 3}),
                         ("info", "Debian", "6 package(s) waiting (3 security, automatic) · sudo macserver update"))
        text = tui_fixture.screen("warning", 232, 64).text()
        self.assertIn("INFO", text)
        self.assertNotIn("Updates", text)

    def test_public_api_row_follows_the_gateway_containers(self):
        """The row said "off · tailnet only" while a Cloudflare gateway served traffic, because the
        hostname was never written to the config. Without PUBLIC_DOMAIN it follows the containers."""
        def public_row(**fields):
            sampler = tui_fixture.make_sampler("healthy")
            sampler.status.update(public_domain="", public_ok=None, public_route=None)
            sampler.status.update(fields)
            return [(lvl, value) for lvl, label, value in tui.connections(sampler) if label == "Public API"][0]
        self.assertEqual(public_row(), ("off", "off · tailnet only"))
        self.assertEqual(public_row(public_route={"caddy": "running", "tunnel": "running", "up": True}),
                         ("ok", "on · Caddy and Cloudflare tunnel running"))
        self.assertEqual(public_row(public_route={"caddy": "running", "tunnel": "exited", "up": False}),
                         ("bad", "down · tunnel exited, Caddy running"))
        self.assertEqual(public_row(public_domain="api.x", public_ok=True)[1], "api.x · answering")

    def test_usb_backup_drive_problems(self):
        """A drive that is simply unplugged is fine; a failed or missed backup is an issue."""
        def drive(**fields):
            return {"count": 5, "newest_age_s": 3600, "newest_size": 100, "drive": {"present": False, "error": "", **fields}}
        self.assertEqual(tui.backup_problem(None), "")
        self.assertEqual(tui.backup_problem({"count": 0}), "")                           # no drive: the old gentle rule
        self.assertEqual(tui.backup_problem(drive()), "")                                # unplugged since a good backup
        self.assertIn("failed", tui.backup_problem(drive(error="the backup drive is not plugged in")))
        self.assertIn("no backup", tui.backup_problem({"count": 0, "drive": {"present": True, "error": ""}}))
        self.assertIn("40 hours", tui.backup_problem({**drive(), "newest_age_s": 40 * 3600}))
        sampler = tui_fixture.make_sampler("healthy")
        sampler.status["debug"]["backups"] = {**drive(error="the database dump failed"), "count": 3}
        titles = [(i["level"], i["title"]) for i in tui.incidents(sampler)]
        self.assertIn(("warn", "the USB backup drive needs attention"), titles)

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
                         "2 Debian package update(s) waiting"):
            self.assertIn(expected, texts)
        self.assertEqual(dict((t, lvl) for _, lvl, t in ev.items)["service auth: running → exited"], "bad")
        tui.status_events(ev, new, new)                      # nothing changed: nothing logged
        self.assertEqual(len(ev.items), len(texts))

    def test_events_log_edges_once(self):
        ev = tui.Events()
        for on in (True, True, True, False, False, True):
            ev.edge("cpu", on, "warn", "CPU busy", "CPU back to normal")
        self.assertEqual([t for _, _, t in reversed(ev.items)], ["CPU busy", "CPU back to normal", "CPU busy"])

    def test_events_and_services_on_the_page(self):
        text = tui_fixture.screen("trouble", 232, 64).text()
        for expected in ("EVENTS", "service edge-functions: running → exited", "12 logged"):
            self.assertIn(expected, text)
        self.assertEqual(tui.SAMPLE_S, 2.0)     # the screen samples as often as the collector writes, not in a 1 ms loop
        s = tui_fixture.make_sampler("healthy")
        s.status["containers"] = ([{"name": f"macserver-apps-app{n}-1", "project": "macserver-apps", "state": "running"}
                                   for n in range(12)] +
                                  [{"name": f"tools-t{n}-1", "project": "tools", "state": "running"} for n in range(5)])
        blocks = tui.service_blocks(s)
        self.assertEqual(blocks[0][0], ("app", "apps", "12/12", "ok"))   # the compose prefix is dropped
        cols = tui.pack_columns(blocks, 2, 13)
        self.assertEqual([col[0][1] for col in cols], ["apps", "tools"])  # columns break between apps
        split = tui.pack_columns(blocks, 3, 8)
        self.assertEqual(split[1][0], blocks[0][0])                      # a long app goes on under its title again
        self.assertIsNone(tui.pack_columns(blocks, 1, 8))
        c = tui.Canvas(60, 6)
        tui.services_panel(c, s, 0, 0, 60, 6)
        self.assertIn("more: sudo macserver status", c.text())          # too many for the room: says so

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

    @unittest.skipUnless(hasattr(os, "statvfs"), "samples the real disk like --once (POSIX only)")
    def test_issues_flag_prints_the_report(self):
        tui_fixture.machine()
        tui_fixture.status("warning")
        run = [sys.executable, str(tui_fixture.ROOT / "admin/tui.py"), "--issues"]
        out = subprocess.run(run, capture_output=True, text=True, env=os.environ, timeout=30, check=True).stdout
        self.assertIn("1. NOTICE  restart needed for updates", out)
        self.assertIn("fix   sudo reboot", out)
        brief = subprocess.run(run + ["--brief"], capture_output=True, text=True, env=os.environ, timeout=30,
                               check=True).stdout
        self.assertIn("details and fixes: macserver issues", brief)
        self.assertNotIn("fix   sudo reboot", brief)

    @unittest.skipUnless(FONT.exists(), "console font not installed (apt install console-setup-linux)")
    def test_console_glyphs_exist_in_the_mac_console_font(self):
        import tui_preview
        _, table, _, _ = tui_preview.load_font(str(FONT))
        self.assertEqual([ch for ch in tui.GLYPHS if ch not in table], [])


if __name__ == "__main__":
    unittest.main()
