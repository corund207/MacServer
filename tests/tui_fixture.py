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
                  MACSERVER_STATUS=str(BASE / "status.json"), MACSERVER_ROOT=str(BASE))
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


def write(rel, text):
    path = BASE / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text)


def machine(temp_c=62, tick=0):
    """Write the sample /proc and /sys. tick=1 advances the counters by one second."""
    busy = [27, 41, 18, 22]      # busy jiffies per thread in that second (of 100)
    lines = [f"cpu  {sum(busy) * tick} 0 0 {sum(100 - b for b in busy) * tick} 0 0 0 0 0 0"]
    lines += [f"cpu{i} {b * tick} 0 0 {(100 - b) * tick} 0 0 0 0 0 0" for i, b in enumerate(busy)]
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
                          f"wlp3s0: {3_412_000_000 + tick * 1_240_000} 0 0 0 0 0 0 0 {812_000_000 + tick * 86_000} 0\n")
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
    write("sys/class/power_supply/BAT0/type", "Battery\n")
    write("sys/class/power_supply/BAT0/capacity", "80\n")
    write("sys/class/power_supply/BAT0/status", "Charging\n")
    write("sys/class/power_supply/BAT0/power_now", "3200000\n")


def status(scenario):
    if scenario == "setup":
        (BASE / "status.json").unlink(missing_ok=True)
        return
    trouble = scenario == "trouble"
    containers = []
    for name, project, cpu, mib in SERVICES:
        stopped = trouble and name == "supabase-edge-functions"
        containers.append({"name": name, "project": project, "state": "exited" if stopped else "running",
                           "status": "Exited (1) 4 minutes ago" if stopped else "Up 3 days (healthy)",
                           "cpu": None if stopped else cpu, "mem": None if stopped else mib * 1024 ** 2})
    data = {
        "generated_at": time.time(), "setup_done": True,
        "host": {"updates_pending": 4 if trouble else 0, "reboot_required": False},
        "tailscale": {"state": "Running", "name": "macserver.tail4f2a.ts.net", "peers_online": 3},
        "containers": containers,
        "public_domain": "api.example.com", "public_ok": not trouble, "clock_synced": True,
        "public_keys": {"ANON_KEY": "eyJpublic-anon-key"},
        "claude": {"installed": True, "state": "inactive" if trouble else "active",
                   "url": "https://claude.ai/code/session_x", "problem": ""},
    }
    (BASE / "status.json").write_text(json.dumps(data))


def screen(scenario="healthy", cols=160, rows=50, rich=True):
    """Draw the dashboard for a scenario with 20 minutes of made-up history."""
    trouble = scenario == "trouble"
    temp = 93 if trouble else 62
    machine(temp, tick=0)
    status(scenario)
    tui.disk = lambda: (41_200_000_000, 233_000_000_000)
    sampler = tui.Sampler()
    machine(temp, tick=1)
    sampler.prev_t -= 1.0            # exactly one second between the two readings
    sampler.sample()
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
        h["io"].append(50_000 + 400_000 * fast ** 6)
    return tui.draw(tui.Canvas(cols, rows, rich), sampler, kiosk=True)
