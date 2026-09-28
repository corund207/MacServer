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

SUPABASE = ["supabase-analytics", "supabase-auth", "supabase-db", "supabase-edge-functions",
            "supabase-imgproxy", "supabase-kong", "supabase-meta", "supabase-pooler",
            "realtime-dev.supabase-realtime", "supabase-rest", "supabase-storage", "supabase-studio",
            "supabase-vector", "macserver-caddy", "macserver-cloudflared"]


def write(rel, text):
    path = BASE / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text)


def machine(temp_c=62, cpu_busy=100, idle=800):
    write("proc/stat", f"cpu  {cpu_busy} 0 50 {idle} 10 0 0 0 0 0\n")
    write("proc/meminfo", "MemTotal: 8000000 kB\nMemAvailable: 2900000 kB\n")
    write("proc/net/route", "Iface\tDestination\tGateway\nwlp3s0\t00000000\t0101A8C0\n")
    write("proc/net/dev", "h1\nh2\n    lo: 1 0 0 0 0 0 0 0 1 0\nwlp3s0: 5000000 0 0 0 0 0 0 0 900000 0\n")
    write("proc/net/wireless", "h1\nh2\nwlp3s0: 0000   58.  -52.  -256\n")
    write("proc/uptime", "273600.5 1000.0\n")
    write("sys/class/hwmon/hwmon0/name", "coretemp\n")
    write("sys/class/hwmon/hwmon0/temp1_input", f"{temp_c * 1000}\n")
    write("sys/class/hwmon/hwmon1/name", "applesmc\n")
    write("sys/class/hwmon/hwmon1/fan1_input", "2400\n")
    write("sys/class/power_supply/BAT0/type", "Battery\n")
    write("sys/class/power_supply/BAT0/capacity", "80\n")
    write("sys/class/power_supply/BAT0/status", "Charging\n")


def status(scenario):
    if scenario == "setup":
        (BASE / "status.json").unlink(missing_ok=True)
        return
    trouble = scenario == "trouble"
    containers = [{"name": n, "state": "exited" if trouble and n == "supabase-edge-functions" else "running",
                   "status": "Up 3 days"} for n in SUPABASE]
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


def screen(scenario="healthy", cols=160, rows=50):
    """Draw the dashboard for a scenario with ~40 minutes of made-up history."""
    trouble = scenario == "trouble"
    machine(temp_c=93 if trouble else 62)
    status(scenario)
    tui.disk = lambda: (41_200_000_000, 233_000_000_000)
    sampler = tui.Sampler()
    machine(temp_c=93 if trouble else 62, cpu_busy=127, idle=873)   # 27 of 100 jiffies busy
    sampler.sample()
    sampler.now.update(rx=1_240_000, tx=86_000)
    if scenario == "setup":
        sampler.firstboot = "activating"
    for i in range(240):
        wave = (math.sin(i / 9) + 1) / 2
        slow = (math.sin(i / 40) + 1) / 2
        sampler.hist["cpu"].append(8 + 30 * wave + (45 if 150 < i < 165 else 0))
        sampler.hist["mem"].append(58 + 8 * slow)
        sampler.hist["temp"].append(52 + 12 * wave + (20 if trouble and i > 225 else 0))
        sampler.hist["rx"].append(40_000 + 900_000 * wave ** 3 + (2_500_000 if 200 < i < 210 else 0))
        sampler.hist["tx"].append(15_000 + 200_000 * (1 - wave) ** 2)
        sampler.hist["bat"].append(min(100, 62 + i * 0.08))
    return tui.draw(tui.Canvas(cols, rows), sampler, kiosk=True)
