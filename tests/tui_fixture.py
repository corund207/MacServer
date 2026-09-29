"""Sample machine for dashboard tests and previews: a fake /proc, /sys and
status.json, so the dashboard can be drawn anywhere without touching the real host.
Importing this module points admin/tui.py at the fake files."""
import atexit
import json
import math
import os
from pathlib import Path
import shutil
import sys
import tempfile
import time

ROOT = Path(__file__).resolve().parents[1]
BASE = Path(tempfile.mkdtemp(prefix="macserver-tui-"))
atexit.register(shutil.rmtree, BASE, True)
os.environ.update(MACSERVER_PROC=str(BASE / "proc"), MACSERVER_SYS=str(BASE / "sys"),
                  MACSERVER_STATUS=str(BASE / "status.json"), MACSERVER_ROOT=str(BASE),
                  MACSERVER_FANS_STATUS=str(BASE / "fans.json"))
sys.path.insert(0, str(ROOT / "admin"))
import tui  # noqa: E402

# (name, compose project, CPU %, memory MiB)
SERVICES = [("supabase-db", "supabase", 3.4, 412), ("supabase-auth", "supabase", 0.3, 38),
            ("supabase-rest", "supabase", 0.8, 61), ("realtime-dev.supabase-realtime", "supabase", 1.9, 188),
            ("supabase-storage", "supabase", 0.2, 94), ("supabase-imgproxy", "supabase", 0.0, 22),
            ("supabase-meta", "supabase", 0.1, 71), ("supabase-edge-functions", "supabase", 0.6, 120),
            ("supabase-pooler", "supabase", 0.9, 150), ("supabase-studio", "supabase", 0.4, 162),
            ("supabase-envoy", "supabase", 0.7, 44), ("supabase-analytics", "supabase", 2.6, 480),
            ("supabase-vector", "supabase", 0.5, 57), ("gateway-caddy-1", "gateway", 0.1, 18),
            ("gateway-cloudflared-1", "gateway", 0.2, 24)]


LOGS = ["2026-09-28T15:15:31Z worker boot: loading functions from /home/deno/functions",
         "2026-09-28T15:15:32Z error: Module not found \"file:///home/deno/functions/main/index.ts\"",
         "2026-09-28T15:15:32Z     at file:///home/deno/functions/main/index.ts:1:1",
         "2026-09-28T15:15:32Z worker exited with code 1"]


def write(rel, text):
    path = BASE / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text)


def machine(temp_c=62, tick=0):
    """Write the sample /proc and /sys. tick=1 advances the counters by one second."""
    busy = [27, 41, 18, 22]      # busy jiffies per thread in that second (of 100)
    lines = [f"cpu  {sum(busy) * tick} 0 0 {sum(100 - b for b in busy) * tick} 0 0 0 0 0 0"]
    lines += [f"cpu{i} {b * tick} 0 0 {(100 - b) * tick} 0 0 0 0 0 0" for i, b in enumerate(busy)]
    lines += [f"intr {90_000_000 + tick * 4_100} 0 0", f"ctxt {250_000_000 + tick * 12_300}"]
    write("proc/stat", "\n".join(lines) + "\n")
    info = ""
    for i in range(4):
        info += (f"processor\t: {i}\nmodel name\t: Intel(R) Core(TM) i5-8210Y CPU @ 1.60GHz\n"
                 f"cpu MHz\t\t: {1580 + i * 40}.000\ncore id\t\t: {i // 2}\n\n")
    write("proc/cpuinfo", info)
    write("proc/meminfo", "MemTotal: 7990000 kB\nMemAvailable: 2890000 kB\nBuffers: 120000 kB\n"
                          "Cached: 1610000 kB\nSReclaimable: 90000 kB\nSwapTotal: 3990000 kB\nSwapFree: 3700000 kB\n")
    write("proc/loadavg", "0.62 0.48 0.41 2/412 9876\n")
    write("proc/diskstats", f"259 0 nvme0n1 900 0 {4000000 + tick * 2400} 0 700 0 {9000000 + tick * 600} 0 0 0 0\n")
    write("proc/net/route", "Iface\tDestination\tGateway\nwlp3s0\t00000000\t0101A8C0\n")
    write("proc/net/dev", f"h1\nh2\n    lo: 1 0 0 0 0 0 0 0 1 0\n"
                          f"wlp3s0: {3_412_000_000 + tick * 1_240_000} 0 0 0 0 0 0 0 {812_000_000 + tick * 86_000} 0\n"
                          f"tailscale0: {95_000_000 + tick * 42_000} 0 0 0 0 0 0 0 {61_000_000 + tick * 18_000} 0\n")
    write("proc/net/wireless", "h1\nh2\nwlp3s0: 0000   58.  -52.  -256\n")
    write("proc/uptime", "273600.5 1000.0\n")
    write("sys/class/hwmon/hwmon0/name", "coretemp\n")
    write("sys/class/hwmon/hwmon0/temp1_input", f"{temp_c * 1000}\n")
    write("sys/class/hwmon/hwmon0/temp1_label", "Package id 0\n")
    write("sys/class/hwmon/hwmon0/temp2_input", f"{(temp_c - 2) * 1000}\n")
    write("sys/class/hwmon/hwmon0/temp2_label", "Core 0\n")
    write("sys/class/hwmon/hwmon0/temp3_input", f"{(temp_c - 4) * 1000}\n")
    write("sys/class/hwmon/hwmon0/temp3_label", "Core 1\n")
    write("sys/class/hwmon/hwmon1/name", "applesmc\n")
    write("sys/class/hwmon/hwmon1/fan1_input", "2400\n")
    write("sys/class/hwmon/hwmon1/temp1_input", "41000\n")
    write("sys/class/hwmon/hwmon1/temp1_label", "TC0P\n")
    write("sys/class/hwmon/hwmon2/name", "nvme\n")
    write("sys/class/hwmon/hwmon2/temp1_input", f"{(temp_c - 18) * 1000}\n")
    write("sys/class/hwmon/hwmon2/temp1_label", "Composite\n")
    write("sys/class/hwmon/hwmon2/temp1_crit", "85000\n")
    for pid, name, ticks, rss_pages in ((101, "postgres", 900, 60_000), (102, "postgres", 400, 30_000), (103, "beam.smp", 800, 41_000),
                                        (104, "deno", 500, 25_000), (105, "python3", 100, 8_000), (1, "systemd", 20, 2_000)):
        write(f"proc/{pid}/stat", f"{pid} ({name}) S 1 1 1 0 -1 0 0 0 0 0 {ticks * tick} {tick} 0 0 20 0 1 0 0 0 {rss_pages}\n")
    write("sys/class/power_supply/BAT0/type", "Battery\n")
    write("sys/class/power_supply/BAT0/capacity", "80\n")
    write("sys/class/power_supply/BAT0/status", "Charging\n")
    write("sys/class/power_supply/BAT0/power_now", "3200000\n")


def debug(trouble):
    """What the collector's debug section holds: requests, connections, ports, logs, files."""
    now = int(time.time())
    listeners = [
        {"port": 443, "addr": "100.89.16.53", "scope": "tailnet", "proc": "tailscaled", "svc": "https"},
        {"port": 8443, "addr": "100.89.16.53", "scope": "tailnet", "proc": "tailscaled", "svc": "studio"},
        {"port": 8090, "addr": "127.0.0.1", "scope": "loopback", "proc": "python3", "svc": "admin"},
        {"port": 8000, "addr": "127.0.0.1", "scope": "loopback", "proc": "supabase-envoy", "svc": "api"},
        {"port": 53, "addr": "127.0.0.53", "scope": "loopback", "proc": "systemd-resolve", "svc": "dns"}]
    flows_in = [{"src": "tailnet jonahs-iphone", "kind": "tailnet", "port": 443, "svc": "https", "n": 3},
                {"src": "tailnet desktop-nlhe9il", "kind": "tailnet", "port": 8443, "svc": "studio", "n": 1}]
    if trouble:
        listeners = [{"port": 5432, "addr": "0.0.0.0", "scope": "all", "proc": "supabase-pooler", "svc": "postgres"},
                     {"port": 6543, "addr": "0.0.0.0", "scope": "all", "proc": "supabase-pooler", "svc": "pooler"},
                     {"port": 8000, "addr": "0.0.0.0", "scope": "all", "proc": "supabase-envoy", "svc": "api"}] + listeners[:3]
        flows_in.insert(0, {"src": "LAN 192.168.18.20", "kind": "lan", "port": 5432, "svc": "postgres", "n": 2})
    recent = [
        (2, "POST", "/functions/v1/sync-events", 200, 77, "this Mac", "curl"),
        (3, "POST", "/rest/v1/event_teams", 201, 0, "edge-functions", "edge function"),
        (3, "POST", "/rest/v1/teams", 201, 45, "edge-functions", "edge function"),
        (5, "GET", "/rest/v1/events", 200, 152, "tailnet jonahs-iphone", "iOS app"),
        (6, "GET", "/rest/v1/teams", 200, 9312, "tailnet jonahs-iphone", "iOS app"),
        (9, "GET", "/auth/v1/user", 401, 88, "tailnet desktop-nlhe9il", "browser"),
        (14, "GET", "/rest/v1/rankings", 200, 4120, "tailnet jonahs-iphone", "iOS app"),
        (21, "POST", "/rest/v1/rpc/claim_request_slot", 200, 40, "edge-functions", "edge function")]
    if trouble:
        recent[0:0] = [(1, "GET", "/rest/v1/events", 503, 17, "LAN 192.168.18.20", "python"),
                       (1, "POST", "/functions/v1/sync-events", 502, 61, "this Mac", "curl")]
    rows = [{"t": now - ago, "method": m, "path": path, "status": st, "bytes": b, "client": who, "agent": ua}
            for ago, m, path, st, b, who, ua in recent]
    for k in range(24):                       # a busier minute: older requests behind the latest
        ago, m, path, st, b, who, ua = recent[3 + k % 5]
        rows.append({"t": now - 22 - k * 2, "method": m, "path": path, "status": st, "bytes": b, "client": who, "agent": ua})
    errors = [r for r in rows if r["status"] >= 400]
    classes = {"2xx": 42, "3xx": 0, "4xx": 3, "5xx": 2 if trouble else 0}
    return {
        "at": now,
        "requests": {"window_s": 60, "total": sum(classes.values()), "classes": classes,
                     "per_2s": [int(2 + 2 * math.sin(i / 3) + (i % 7 == 0) * 3) for i in range(30)],
                     "recent": rows, "errors": errors,
                     "top_paths": [["GET /rest/v1/teams", 14], ["POST /rest/v1/teams", 9], ["GET /rest/v1/events", 7]],
                     "top_clients": [["tailnet jonahs-iphone (iOS app)", 22], ["edge-functions (edge function)", 18]]},
        "flows": {"out": [{"who": "edge-functions", "dst": "104.18.2.161", "kind": "public", "port": 443, "svc": "https", "n": 2},
                          {"who": "tailscaled", "dst": "199.165.136.100", "kind": "public", "port": 443, "svc": "https", "n": 3},
                          {"who": "tailscaled", "dst": "tailnet desktop-nlhe9il", "kind": "tailnet", "port": 41641, "svc": "tailscale", "n": 1},
                          {"who": "this Mac", "dst": "LAN 192.168.18.1", "kind": "lan", "port": 53, "svc": "dns", "n": 2}],
                  "in": flows_in, "tracked": 59, "states": {"ESTABLISHED": 12, "TIME_WAIT": 9}},
        "listeners": listeners,
        "net": {"gateway": "192.168.18.1", "gateway_ms": 2.1, "internet_ms": 18.4, "dns_ms": 12.0,
                "internet_fails": 0, "dns_fails": 0, "checked_at": now},
        "inspect": ({"supabase-edge-functions": {"exit": 1, "oom": False, "restarts": 3, "started": "2026-09-28T15:15:30",
                                                  "finished": "2026-09-28T15:15:32", "error": "", "health": "",
                                                  "health_output": ""}} if trouble else {}),
        "system": {"failed_units": [], "oom_kills": 0, "thermal_throttle": 14,
                   "journal": ["2026-09-28T21:24:07-0400 macserver systemd[1]: macserver-fans.service: Main process exited, code=exited, status=1/FAILURE",
                               "2026-09-28T21:30:11-0400 macserver dockerd[815]: level=error msg=\"failed to allocate gateway\""],
                   "kernel": ["[   19.140325] ieee80211 phy0: brcmf_p2p_set_firmware: failed to update device address ret -52"]},
        "service_errors": ({"edge-functions": ["error: Module not found \"file:///home/deno/functions/main/index.ts\"",
                                                "worker exited with code 1 (restarting in 5s)"],
                            "rest": ["PGRST002: Could not query the database because of a schema cache error",
                                     "connection to server failed: timeout expired"],
                            "db": ["FATAL:  remaining connection slots are reserved for non-replication superuser connections"]}
                           if trouble else {}),
        "files": [{"path": "/opt/macserver/supabase/docker-compose.yml", "note": "Supabase services (managed: do not edit)", "age_s": 86400 * 3, "size": 21_000},
                  {"path": "/opt/macserver/supabase/.env", "note": "Supabase settings and secrets (never print it)", "age_s": 300, "size": 12_300},
                  {"path": "/opt/macserver/supabase/volumes/functions", "note": "Edge Functions source", "age_s": 240, "size": None},
                  {"path": "/etc/nftables.conf", "note": "firewall rules", "age_s": 86400 * 2, "size": 2_600},
                  {"path": "/var/log/macserver-firstboot.log", "note": "installer log", "age_s": 86400, "size": 88_000},
                  {"path": "/var/backups/macserver", "note": "database backups", "age_s": 5400, "size": None}],
        "backups": {"count": 7, "newest_age_s": 5400, "newest_size": 48_000},
    }


def status(scenario):
    if scenario == "setup":
        (BASE / "status.json").unlink(missing_ok=True)
        return
    trouble = scenario == "trouble"
    warning = scenario == "warning"
    containers = []
    for name, project, cpu, mib in SERVICES:
        stopped = trouble and name == "supabase-edge-functions"
        containers.append({"name": name, "project": project, "state": "exited" if stopped else "running",
                           "status": "Exited (1) 4 minutes ago" if stopped else "Up 3 days (healthy)",
                           "cpu": None if stopped else cpu, "mem": None if stopped else mib * 1024 ** 2})
        if stopped:
            containers[-1]["logs"] = LOGS
    data = {
        "generated_at": time.time(), "setup_done": True,
        "host": {"updates_pending": 4 if trouble or warning else 0, "reboot_required": warning},
        "tailscale": {"state": "Running", "name": "macserver.tail4f2a.ts.net", "peers_online": 3},
        "containers": containers,
        "public_domain": "api.example.com", "public_ok": not trouble, "clock_synced": True,
        "macserver_update": {"state": "up-to-date", "message": "running the newest version",
                             "current": "6bc4f51aa0", "latest": "6bc4f51aa0", "auto": "on"},
        "public_keys": {"ANON_KEY": "eyJpublic-anon-key"},
        "claude": {"installed": True, "state": "inactive" if trouble else "active",
                   "url": "https://claude.ai/code/session_x", "problem": ""},
        "debug": debug(trouble),
    }
    (BASE / "status.json").write_text(json.dumps(data))


def make_sampler(scenario="healthy", overview=False, page=None):
    """Draw the dashboard for a scenario with 20 minutes of made-up history.
    healthy | warning (notices only) | trouble (critical: incident view) | setup.
    overview=True shows the normal dashboard during an incident (as Space does).
    page=NAME (incident, network, requests, system, logs) picks the incident page and holds it."""
    trouble = scenario == "trouble"
    temp = 93 if trouble else 62
    (BASE / "fans.json").write_text(json.dumps({"mode": "curve", "min_pct": 60, "target_pct": 64.0 if not trouble else 100.0,
                                                  "fans": [{"fan": "1", "rpm": 2400, "max_rpm": 6000}]}))
    machine(temp, tick=0)
    status(scenario)
    tui.disk = lambda: (41_200_000_000, 233_000_000_000)
    sampler = tui.Sampler()
    machine(temp, tick=1)
    sampler.prev_t -= 1.0            # about one second between the two readings
    sampler.sample()
    # Rates depend on the measured gap; pin them so tests never depend on timing.
    sampler.now.update(rx=1_240_000, tx=86_000, rd=1_228_800, wr=307_200, ts_rx=42_000, ts_tx=18_000,
                       ctxt=12_300, intr=4_100)
    # Per-service CPU history (the collector reports every 2 s) and a few events.
    for name, _, cpu, _ in SERVICES:
        sampler.svc_hist[name].extend(max(0.0, cpu * (0.6 + 0.8 * ((math.sin(i / 3 + len(name)) + 1) / 2)))
                                      for i in range(60))
    now = time.time()
    ev = sampler.events
    ev.items.clear()
    log = [(3400, "info", "dashboard started"), (3395, "info", "watching 15 services (15 running)"),
           (2710, "ok", "MacServer: now running e6db647 (Wait for the package lock)"),
           (1985, "info", "4 device(s) online on the tailnet"),
           (1320, "warn", "CPU busy: 91%"), (1260, "ok", "CPU back to normal"),
           (840, "info", "download burst: 6.3 MB/s"), (610, "info", "Claude session started"),
           (95, "info", "3 device(s) online on the tailnet")]
    if trouble:
        log += [(240, "bad", "service edge-functions: running → exited"), (30, "warn", "running hot: 93°C"),
                (20, "bad", "public API not answering")]
    for ago, level, text in sorted(log, reverse=True):     # oldest first, as they happen
        ev.add(level, text, now - ago)
    if scenario == "setup":
        sampler.firstboot = "activating"
    h = sampler.hist
    for k in list(h):
        h[k].clear()
    for i in range(tui.HISTORY):
        wave = (math.sin(i / 23) + 1) / 2
        fast = (math.sin(i / 5.3) + 1) / 2
        burst = 1 if 900 < i < 960 else 0
        h["cpu"].append(10 + 22 * wave + 12 * fast * wave + 45 * burst)
        for core in range(4):
            h[f"core{core}"].append(min(100, 6 + 30 * ((math.sin(i / (7 + core * 3)) + 1) / 2) + 50 * burst))
        h["mem"].append(60 + 4 * math.sin(i / 90))
        h["temp"].append(52 + 10 * wave + (25 if trouble and i > 1100 else 0))
        h["rx"].append(30_000 + 1_100_000 * wave ** 4 + (3_600_000 if 1040 < i < 1070 else 0) + 90_000 * fast)
        h["tx"].append(12_000 + 240_000 * (1 - wave) ** 3 + 40_000 * fast)
        h["rd"].append(20_000 + 900_000 * fast ** 6 + (2_400_000 if 1500 < i < 1530 else 0))
        h["wr"].append(60_000 + 300_000 * ((math.sin(i / 11) + 1) / 2) ** 4)
    for i in range(120):
        h["fan"].append(2300 + 600 * ((math.sin(i / 13) + 1) / 2))
    sampler.force_overview = overview
    sampler.procs = {"cpu": [{"name": "postgres", "cpu": 34.5, "rss": 380_000_000, "n": 9},
                             {"name": "beam.smp", "cpu": 12.1, "rss": 168_000_000, "n": 1},
                             {"name": "deno", "cpu": 6.4, "rss": 98_000_000, "n": 4},
                             {"name": "python3", "cpu": 1.2, "rss": 33_000_000, "n": 2}],
                     "mem": [{"name": "postgres", "cpu": 34.5, "rss": 380_000_000, "n": 9},
                             {"name": "studio", "cpu": 0.4, "rss": 281_000_000, "n": 1},
                             {"name": "beam.smp", "cpu": 12.1, "rss": 168_000_000, "n": 1},
                             {"name": "deno", "cpu": 6.4, "rss": 98_000_000, "n": 4}]}
    if page:
        sampler.page, sampler.paused, sampler.last_items = tui.PAGES.index(page), True, 99
    sampler.crit_since = time.time() - 754
    return sampler


def screen(scenario="healthy", cols=160, rows=50, rich=True, overview=False, page=None, sampler=None):
    """Draw the dashboard for a scenario (see make_sampler); pass a sampler to draw one you changed."""
    sampler = sampler or make_sampler(scenario, overview, page)
    c = tui.Canvas(cols, rows, rich)
    tui.draw(c, sampler, kiosk=True)
    return c
