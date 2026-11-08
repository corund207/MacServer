#!/usr/bin/env python3
"""MacServer dashboard: the always-on screen of the Mac, in the style of btop.

    dashboard            live, full screen; q quits
    dashboard --once     print a summary once (shown at login)
    dashboard --issues   what the screen's notices and alerts are, and how to fix each
                         (`macserver issues`; --brief: one line each, for `macserver status`)
    dashboard --kiosk    always-on mode for tty1: ignores every key, never exits

Two looks from one layout:
  rich     in a modern terminal (the Mac runs `foot` in the `cage` kiosk): 24-bit
           colour gradients and braille graphs (2x4 dots per character).
  console  on the bare Linux console (TERM=linux, the fallback): 16 colours and
           only characters in the Lat15-Terminus font (tests check this).

Read-only and unprivileged: live numbers come from /proc and /sys every second;
everything else from /run/macserver/status.json (written every 2 s by the root
collector; it holds no secrets).
"""
import collections
import json
import math
import os
from pathlib import Path
import re
import select
import signal
import subprocess
import sys
import time

from units import fmt_bytes

STATUS = Path(os.environ.get("MACSERVER_STATUS", "/run/macserver/status.json"))
FANS = Path(os.environ.get("MACSERVER_FANS_STATUS", "/run/macserver/fans.json"))
PROC = Path(os.environ.get("MACSERVER_PROC", "/proc"))
SYS = Path(os.environ.get("MACSERVER_SYS", "/sys"))
DOCS = "github.com/corund207/MacServer"
STUDIO_PORT = 8443
SAMPLE_S = 2.0        # numbers are read this often, as often as the collector writes status.json
DRAW_S = 1.0          # the screen is redrawn this often, so the clock and the page countdown tick every second
HISTORY = 600   # samples kept per graph (20 minutes at 2 s intervals)
# Smoothing per reading (share of each new sample taken): lower = calmer.
SMOOTH = {"cpu": 0.35, "temp": 0.25, "mhz": 0.3, "rx": 0.3, "tx": 0.3, "ts_rx": 0.3, "ts_tx": 0.3,
          "rd": 0.3, "wr": 0.3, "ctxt": 0.3, "intr": 0.3}


def ema(prev, value, alpha):
    return prev + alpha * (value - prev)


# Heat is never a notice by itself. This MacBook Air sits in the 90s under ordinary load and
# touches the CPU's own 100 °C limit many times a minute, with the fan at full speed; its
# heat-throttle counter is in the hundreds of thousands. That is how this machine works, so
# no temperature raises a notice, an amber glow or a "warm" label. Only a broken cooling
# system does: a hot CPU whose fan is not turning (see cooling_fault).
COOLING_CHECK_C = 85.0     # a stopped fan only matters once the CPU is this warm
FAN_STALLED_RPM = 300      # the T2 fan idles at ~1200 rpm, so under this it is not turning


def cooling_fault(temp, fans):
    """A sentence when the fan is not turning on a warm CPU, else None. Never about heat alone:
    no fan reading at all is not a fault either (some Macs expose none), the panel says so."""
    if temp is None or temp < COOLING_CHECK_C or not fans:
        return None
    if max(fans) < FAN_STALLED_RPM:
        return f"fan reads {max(fans)} rpm while the CPU is at {temp:.0f}°C"
    return None


def temp_pos(t):
    """Where a temperature sits on the "temp" colour scale: calm blue to pink across the whole
    range. It never reaches alarm red, because on this machine 100 °C is a normal reading."""
    return min(max((t - 30) / 70, 0.0), 1.0) * 0.8

# Characters drawn in console mode besides printable ASCII: all exist in the
# Lat15-Terminus console font (tests/test_dashboard.py checks it against the font).
GLYPHS = "─│╭╮╰╯├┤┬┴█▒░■●✗·°↑↓→…▲▼"
# Rich mode may also use these (the terminal draws braille itself).
RICH_EXTRA = "◆⏵"


# --- theme ---------------------------------------------------------------------------

def rgb(h):
    return int(h[1:3], 16), int(h[3:5], 16), int(h[5:7], 16)


C = {k: rgb(v) for k, v in {
    "bg": "#0b0e14", "panel": "#0f131b", "text": "#c9d1d9", "bright": "#f0f6fc", "dim": "#7d8590",
    "faint": "#2d333b", "line": "#262c36",
    "cpu": "#3fb950", "mem": "#e3b341", "net": "#a371f7", "conn": "#00d4ff", "svc": "#58a6ff", "warn": "#d29922",
    "ok": "#3fb950", "bad": "#f85149", "off": "#6e7681", "accent": "#79c0ff", "pill_fg": "#0b0e14",
}.items()}

GRADIENTS = {k: [rgb(x) for x in v] for k, v in {
    "cpu": ["#2ea043", "#3fb950", "#d29922", "#f85149"],
    "mem": ["#bb8009", "#e3b341", "#f0883e", "#f85149"],
    "temp": ["#388bfd", "#a371f7", "#f778ba", "#f85149"],
    "down": ["#6e40c9", "#a371f7", "#d2a8ff"],
    "up": ["#1b7c83", "#39c5cf", "#a5f3fc"],
    "bat": ["#f85149", "#d29922", "#3fb950"],
    "disk": ["#1f6feb", "#58a6ff", "#a5d6ff"],
}.items()}

STATE_COLOUR = {"ok": C["ok"], "warn": C["warn"], "bad": C["bad"], "off": C["off"]}
STATE_TEXT = {"ok": "OK", "warn": "WARN", "bad": "DOWN", "off": "OFF"}


def _red(colour):
    """The same brightness, in red: the whole screen turns red during an incident."""
    lum = 0.3 * colour[0] + 0.59 * colour[1] + 0.11 * colour[2]
    return min(255, round(35 + lum * 0.95)), round(lum * 0.16), round(lum * 0.2)


NORMAL_THEME = (dict(C), {k: list(v) for k, v in GRADIENTS.items()})
RED_THEME = ({**{k: _red(v) for k, v in C.items()}, "bg": (20, 6, 8), "panel": (36, 9, 12),
              "bad": (255, 64, 64), "warn": (255, 150, 60), "ok": (150, 60, 60), "pill_fg": (20, 6, 8),
              "bright": (255, 220, 220), "text": (235, 170, 170)},
             {k: [(110, 18, 24), (200, 40, 46), (255, 95, 90)] for k in GRADIENTS})
ALERT_ORANGE = (255, 140, 0)


def set_theme(critical):
    """Switch every colour between the normal and the red theme (per frame)."""
    global C, GRADIENTS, STATE_COLOUR, EVENT_COLOUR
    C, GRADIENTS = (RED_THEME if critical else NORMAL_THEME)
    C, GRADIENTS = dict(C), dict(GRADIENTS)
    STATE_COLOUR = {"ok": C["ok"], "warn": C["warn"], "bad": C["bad"], "off": C["off"]}
    EVENT_COLOUR = {"ok": C["ok"], "warn": C["warn"], "bad": C["bad"], "info": C["accent"]}


def ring_colour(level, t):
    """Screen-edge colour: warnings pulse orange; critical alerts flash red."""
    if level == "warning":
        k = (1 + math.sin(t * 2 * math.pi / 1.4)) / 2
        return mix(C["bg"], ALERT_ORANGE, 0.2 + 0.8 * k)
    if level == "critical":
        return (255, 36, 36) if int(t * 2.5) % 2 == 0 else (80, 8, 12)
    return None


def mix(a, b, t):
    """Blend colour a towards b by t (0 = a, 1 = b)."""
    return tuple(round(a[k] + (b[k] - a[k]) * t) for k in range(3))


def grad(name, t):
    stops = GRADIENTS[name]
    t = min(max(t, 0.0), 1.0) * (len(stops) - 1)
    i = min(int(t), len(stops) - 2)
    f = t - i
    a, b = stops[i], stops[i + 1]
    return tuple(round(a[k] + (b[k] - a[k]) * f) for k in range(3))


# The 16 Linux console colours, for console mode.
CONSOLE_PALETTE = [(0, 0, 0), (170, 0, 0), (0, 170, 0), (170, 85, 0), (0, 0, 170), (170, 0, 170),
                   (0, 170, 170), (170, 170, 170), (85, 85, 85), (255, 85, 85), (85, 255, 85),
                   (255, 255, 85), (85, 85, 255), (255, 85, 255), (85, 255, 255), (255, 255, 255)]


def nearest16(colour, background=False):
    """Console colour index for an RGB colour (backgrounds: the 8 dark ones only)."""
    candidates = range(8) if background else range(16)
    r, g, b = colour
    if max(colour) - min(colour) > 60:     # a real colour: never round it to grey, black or white
        candidates = [i for i in candidates if i not in (0, 7, 8, 15)]
    return min(candidates, key=lambda i: (CONSOLE_PALETTE[i][0] - r) ** 2 * 3 + (CONSOLE_PALETTE[i][1] - g) ** 2 * 4
               + (CONSOLE_PALETTE[i][2] - b) ** 2 * 2)


# --- reading the machine -------------------------------------------------------------

def read(path, default=""):
    try:
        return Path(path).read_text()
    except OSError:
        return default


def cpu_times():
    """{"cpu": (busy, total), "cpu0": ..., ...} jiffies from /proc/stat."""
    out = {}
    for line in read(PROC / "stat", "cpu 0 0 0 0").splitlines():
        if not line.startswith("cpu"):
            continue
        name, *fields = line.split()
        f = [int(x) for x in fields]
        idle = f[3] + (f[4] if len(f) > 4 else 0)
        out[name] = (sum(f) - idle, sum(f))
    return out


def cpu_info():
    """(model, average MHz, {thread: core id})."""
    model, mhz, cores, thread = "", [], {}, None
    for line in read(PROC / "cpuinfo").splitlines():
        key, _, value = line.partition(":")
        key, value = key.strip(), value.strip()
        if key == "processor":
            thread = int(value)
        elif key == "model name" and not model:
            model = re.sub(r"\s+", " ", re.sub(r"\((R|TM)\)|CPU|@.*", "", value)).strip()
        elif key == "cpu MHz":
            mhz.append(float(value))
        elif key == "core id" and thread is not None:
            cores[thread] = int(value)
    return model or "CPU", (sum(mhz) / len(mhz) if mhz else None), cores


def memory():
    info = {}
    for line in read(PROC / "meminfo").splitlines():
        name, _, rest = line.partition(":")
        if rest.split():
            info[name] = int(rest.split()[0]) * 1024
    total = info.get("MemTotal", 0)
    avail = info.get("MemAvailable", total)
    cached = info.get("Cached", 0) + info.get("Buffers", 0) + info.get("SReclaimable", 0)
    return {"total": total, "used": total - avail, "available": avail, "cached": cached,
            "swap_total": info.get("SwapTotal", 0), "swap_used": info.get("SwapTotal", 0) - info.get("SwapFree", 0)}


def disk():
    try:
        st = os.statvfs(os.environ.get("MACSERVER_ROOT", "/"))
    except OSError:
        return 0, 0
    total = st.f_blocks * st.f_frsize
    return total - st.f_bavail * st.f_frsize, total


def disk_io():
    """(bytes read, bytes written) since boot for the main disk."""
    for line in read(PROC / "diskstats").splitlines():
        f = line.split()
        if len(f) > 9 and re.fullmatch(r"nvme\d+n\d+|sd[a-z]|vd[a-z]|mmcblk\d+", f[2]):
            return int(f[5]) * 512, int(f[9]) * 512
    return 0, 0


def sensors():
    """(hottest CPU temp, {core id: temp}, [fan rpm])."""
    temps, core_temps, fans = collections.defaultdict(list), {}, []
    for mon in sorted((SYS / "class/hwmon").glob("hwmon*")):
        name = read(mon / "name").strip()
        for t in sorted(mon.glob("temp*_input")):
            try:
                value = int(read(t, "0")) / 1000
            except ValueError:
                continue
            if not 0 < value < 125:
                continue
            temps[name].append(value)
            label = read(t.with_name(t.name.replace("_input", "_label"))).strip()
            m = re.fullmatch(r"Core (\d+)", label)
            if name == "coretemp" and m:
                core_temps[int(m.group(1))] = value
        for f in sorted(mon.glob("fan*_input")):
            try:
                fans.append(int(read(f, "0")))
            except ValueError:
                pass
    if not fans:   # T2 Macs: the fans are on the SMC's ACPI device, not in hwmon
        for f in sorted(SYS.glob("devices/pci*/*/*/*/APP0001:00/fan*_input")):
            try:
                fans.append(int(read(f, "0")))
            except ValueError:
                pass
    for name in ("coretemp", "k10temp", "acpitz", "applesmc"):
        if temps.get(name):
            return max(temps[name]), core_temps, fans
    return None, core_temps, fans


def battery():
    """(percent, status, watts) or (None, None, None)."""
    for supply in sorted((SYS / "class/power_supply").glob("*")):
        if read(supply / "type").strip() != "Battery":
            continue
        cap = read(supply / "capacity").strip()
        watts = None
        try:
            if (supply / "power_now").exists():
                watts = int(read(supply / "power_now", "0")) / 1e6
            elif (supply / "current_now").exists():
                watts = int(read(supply / "current_now", "0")) * int(read(supply / "voltage_now", "0")) / 1e12
        except ValueError:
            pass
        return (int(cap) if cap.isdigit() else None), read(supply / "status").strip() or "?", watts
    return None, None, None


def default_iface():
    for line in read(PROC / "net/route").splitlines()[1:]:
        parts = line.split()
        if len(parts) > 2 and parts[1] == "00000000":
            return parts[0]
    return ""


def net_bytes(iface):
    for line in read(PROC / "net/dev").splitlines()[2:]:
        name, _, rest = line.partition(":")
        if name.strip() == iface:
            f = rest.split()
            return int(f[0]), int(f[8])
    return 0, 0


def wifi_signal(iface):
    for line in read(PROC / "net/wireless").splitlines()[2:]:
        name, _, rest = line.partition(":")
        if name.strip() == iface:
            try:
                return int(float(rest.split()[2]))
            except (IndexError, ValueError):
                return None
    return None


def uptime_s():
    try:
        return int(float(read(PROC / "uptime", "0").split()[0]))
    except (IndexError, ValueError):
        return 0


def loadavg():
    try:
        return [float(x) for x in read(PROC / "loadavg", "0 0 0").split()[:3]]
    except ValueError:
        return [0.0, 0.0, 0.0]


def load_status():
    try:
        data = json.loads(STATUS.read_text())
    except (OSError, ValueError):
        return None
    data["age_s"] = time.time() - data.get("generated_at", 0)
    return data


def firstboot_state():
    try:
        return subprocess.run(["systemctl", "show", "-p", "ActiveState", "--value", "macserver-firstboot.service"],
                              capture_output=True, text=True, timeout=5).stdout.strip()
    except (OSError, subprocess.TimeoutExpired):
        return ""


def proc_counters():
    """(context switches, interrupts, tasks running, tasks total) from /proc."""
    ctxt = intr = 0
    for line in read(PROC / "stat").splitlines():
        if line.startswith("ctxt "):
            ctxt = int(line.split()[1])
        elif line.startswith("intr "):
            intr = int(line.split()[1])
    running = total = 0
    m = re.search(r"(\d+)/(\d+)", read(PROC / "loadavg"))
    if m:
        running, total = int(m.group(1)), int(m.group(2))
    return ctxt, intr, running, total


class Events:
    """What happened lately, newest first: services, updates, devices, spikes."""

    def __init__(self):
        self.items = collections.deque(maxlen=200)
        self.flags = set()

    def add(self, level, text, when=None):
        self.items.appendleft((when or time.time(), level, text))

    def edge(self, key, on, level, text, off_text=None):
        """Log once when a condition starts (and optionally when it ends)."""
        if on and key not in self.flags:
            self.flags.add(key)
            self.add(level, text)
        elif not on and key in self.flags:
            self.flags.discard(key)
            if off_text:
                self.add("ok", off_text)


def status_events(events, old, new):
    """Compare two status snapshots and log the differences."""
    if not new:
        return
    if not old:
        up = sum(1 for c in new.get("containers", []) if c.get("state") == "running")
        events.add("info", f"watching {len(new.get('containers', []))} services ({up} running)")
        return
    before = {c["name"]: c.get("state") for c in old.get("containers", [])}
    for c in new.get("containers", []):
        name, state = c["name"], c.get("state")
        short = name.split(".")[-1].replace("supabase-", "")
        if name not in before:
            events.add("info", f"service {short} appeared ({state})")
        elif before[name] != state:
            events.add("ok" if state == "running" else "bad", f"service {short}: {before[name]} → {state}")
    ts_old, ts_new = old.get("tailscale") or {}, new.get("tailscale") or {}
    if ts_old.get("state") != ts_new.get("state"):
        events.add("ok" if ts_new.get("state") == "Running" else "bad", f"Tailscale {ts_new.get('state', '?')}")
    if ts_old.get("peers_online") != ts_new.get("peers_online") and ts_new.get("peers_online") is not None:
        events.add("info", f"{ts_new['peers_online']} device(s) online on the tailnet")
    if old.get("public_ok") != new.get("public_ok") and new.get("public_domain"):
        events.add("ok" if new.get("public_ok") else "bad",
                   f"public API {'answering' if new.get('public_ok') else 'not answering'}")
    mu_old, mu_new = old.get("macserver_update") or {}, new.get("macserver_update") or {}
    if mu_new and (mu_old.get("state"), mu_old.get("latest")) != (mu_new.get("state"), mu_new.get("latest")):
        level = {"updated": "ok", "up-to-date": "ok", "rolled-back": "bad", "skipped": "warn",
                 "refused": "warn", "error": "warn"}.get(mu_new.get("state"), "info")
        events.add(level, f"MacServer: {mu_new.get('message', mu_new.get('state'))}")
    cl_old, cl_new = (old.get("claude") or {}).get("state"), (new.get("claude") or {}).get("state")
    if cl_old != cl_new and cl_new:
        events.add("info", "Claude session started" if cl_new == "active" else "Claude session ended")
    u_old = (old.get("host") or {}).get("updates_pending", 0)
    u_new = (new.get("host") or {}).get("updates_pending", 0)
    if u_new > u_old:
        events.add("warn", f"{u_new} Debian update(s) waiting")


class Sampler:
    """Live numbers twice a second, with history for the graphs, and events."""

    def __init__(self):
        self.model, _, self.core_of = cpu_info()
        self.prev_cpu = cpu_times()
        self.iface = default_iface()
        self.prev_net = net_bytes(self.iface)
        self.prev_ts = net_bytes("tailscale0")
        self.prev_io = disk_io()
        self.prev_counters = proc_counters()
        self.prev_t = time.monotonic()
        self.hist = collections.defaultdict(lambda: collections.deque(maxlen=HISTORY))
        self.svc_hist = collections.defaultdict(lambda: collections.deque(maxlen=1800))   # 1 hour at one sample per 2 s
        self.now = {"cpu": 0.0, "cores": [], "rx": 0.0, "tx": 0.0, "rd": 0.0, "wr": 0.0,
                    "ts_rx": 0.0, "ts_tx": 0.0, "ctxt": 0.0, "intr": 0.0}
        self.status = None
        self.status_read = -1e9
        self.status_stamp = None
        self.smooth = {}                # smoothed readings (see smooth_readings)
        self.raw = {}
        self.firstboot = ""
        self.force_overview = False     # Space during an incident shows the normal dashboard
        self.fans = None
        self.procs = {"cpu": [], "mem": []}     # busiest programs (read every 2 s)
        self.prev_procs, self.prev_procs_t = {}, time.monotonic()
        self.thermal = []                       # every temperature sensor
        self.page, self.page_t, self.paused, self.last_items = 1, time.monotonic(), False, 0   # incident pages (0=doomsday, 1=incident)
        self.home, self.home_t = 0, time.monotonic()    # the normal pages (HOME_PAGES) and when this one came up
        self.status_hist = collections.defaultdict(lambda: collections.deque(maxlen=HISTORY))   # series read from status.json
        self.crit_since = None                  # when the current incident began (for the T+ clock)
        self.counter_base, self.counter_last, self.counter_changed = {}, {}, {}
        self.events = Events()
        self.events.add("info", "dashboard started")

    def smooth_readings(self):
        """Exponential moving average over what is shown and graphed: each reading moves
        the value part of the way (SMOOTH) towards it, so numbers stay readable and graphs
        smooth, while a real change still shows within a second or two."""
        self.raw = dict(self.now)
        for key, alpha in SMOOTH.items():
            value = self.now.get(key)
            if value is None:
                continue
            prev = self.smooth.get(key)
            self.smooth[key] = value if prev is None else ema(prev, value, alpha)
            self.now[key] = self.smooth[key]
        cores, prev = self.now.get("cores") or [], self.smooth.get("cores")
        if prev and len(prev) == len(cores):
            cores = [ema(p, v, SMOOTH["cpu"]) for p, v in zip(prev, cores)]
        self.smooth["cores"] = self.now["cores"] = cores

    def sample(self):
        t = time.monotonic()
        dt = max(t - self.prev_t, 0.001)
        cur = cpu_times()

        def pct(name):
            b, tot = cur.get(name, (0, 0))
            pb, pt = self.prev_cpu.get(name, (0, 0))
            return 100.0 * (b - pb) / (tot - pt) if tot > pt else 0.0
        self.now["cpu"] = pct("cpu")
        threads = sorted((n for n in cur if n != "cpu"), key=lambda n: int(n[3:]))
        self.now["cores"] = [pct(n) for n in threads]
        self.prev_cpu = cur
        iface = default_iface()
        rx, tx = net_bytes(iface)
        if iface == self.iface and rx >= self.prev_net[0] and tx >= self.prev_net[1]:
            self.now["rx"], self.now["tx"] = (rx - self.prev_net[0]) / dt, (tx - self.prev_net[1]) / dt
        self.iface, self.prev_net = iface, (rx, tx)
        self.now["rx_total"], self.now["tx_total"] = rx, tx
        ts_rx, ts_tx = net_bytes("tailscale0")
        if ts_rx >= self.prev_ts[0] and ts_tx >= self.prev_ts[1]:
            self.now["ts_rx"], self.now["ts_tx"] = (ts_rx - self.prev_ts[0]) / dt, (ts_tx - self.prev_ts[1]) / dt
        self.prev_ts = (ts_rx, ts_tx)
        rd, wr = disk_io()
        if rd >= self.prev_io[0] and wr >= self.prev_io[1]:
            self.now["rd"], self.now["wr"] = (rd - self.prev_io[0]) / dt, (wr - self.prev_io[1]) / dt
        self.prev_io = (rd, wr)
        ctxt, intr, running, tasks = proc_counters()
        if ctxt >= self.prev_counters[0] and intr >= self.prev_counters[1]:
            self.now["ctxt"], self.now["intr"] = (ctxt - self.prev_counters[0]) / dt, (intr - self.prev_counters[1]) / dt
        self.prev_counters = (ctxt, intr, running, tasks)
        self.now["running"], self.now["tasks"] = running, tasks
        self.prev_t = t
        _, self.now["mhz"], _ = cpu_info()
        self.now["mem"] = memory()
        self.now["disk"] = disk()
        self.now["temp"], self.now["core_temps"], self.now["fans"] = sensors()
        self.now["battery"] = battery()
        self.now["signal"] = wifi_signal(iface)
        self.now["uptime"] = uptime_s()
        self.now["load"] = loadavg()
        self.hist["load"].append(self.now["load"][0])
        self.smooth_readings()
        m = self.now["mem"]
        h = self.hist
        h["cpu"].append(self.now["cpu"])
        for i, v in enumerate(self.now["cores"]):
            h[f"core{i}"].append(v)
        h["mem"].append(100.0 * m["used"] / m["total"] if m["total"] else 0)
        h["rx"].append(self.now["rx"])
        h["tx"].append(self.now["tx"])
        h["rd"].append(self.now["rd"])
        h["wr"].append(self.now["wr"])
        if self.now["temp"] is not None:
            h["temp"].append(self.now["temp"])
        ev, temp = self.events, self.now["temp"]
        ev.edge("cpu", self.now["cpu"] >= 85 or ("cpu" in ev.flags and self.now["cpu"] >= 60), "warn",
                f"CPU busy: {self.now['cpu']:.0f}%", "CPU back to normal")
        stalled = cooling_fault(temp, self.now.get("fans"))
        ev.edge("cooling", stalled is not None, "bad", f"cooling fault: {stalled}", "fan turning again")
        ev.edge("net", self.now["rx"] >= 5e6, "info", f"download burst: {fmt_bytes(self.now['rx'], True)}")
        ev.edge("offline", not iface, "bad", "network connection lost", "network connected")
        try:                                     # what the fan controller is doing
            self.fans = json.loads(FANS.read_text())
        except (OSError, ValueError):
            self.fans = None
        fans_rpm = self.now.get("fans") or []
        if fans_rpm:
            h["fan"].append(fans_rpm[0])
        if t - self.status_read >= 2:
            new = load_status()
            self.firstboot = firstboot_state()
            self.status_read = t
            stamp = (new or {}).get("generated_at")
            if stamp != self.status_stamp:
                status_events(self.events, self.status, new)
                for c in (new or {}).get("containers", []):
                    if c.get("cpu") is not None:
                        self.svc_hist[c["name"]].append(c["cpu"])
                self.status_hist["svc_cpu"].append(sum(c.get("cpu") or 0 for c in (new or {}).get("containers", [])))
                self.status_stamp = stamp
            self.status = new
            self.update_slow_readings(t)

    def update_slow_readings(self, t):
        """Processes, every sensor and the kernel's OOM / heat counters: every 2 s is plenty."""
        cur = read_procs()
        self.procs = group_procs(cur, self.prev_procs, max(t - self.prev_procs_t, 0.001))
        self.prev_procs, self.prev_procs_t = cur, t
        self.thermal = thermal_readings()
        system = ((self.status or {}).get("debug") or {}).get("system") or {}
        for key in ("oom_kills", "thermal_throttle"):
            value = system.get(key)
            if value is None:
                continue
            self.counter_base.setdefault(key, value)
            if value > self.counter_last.get(key, self.counter_base[key]):
                self.counter_changed[key] = time.time()
            self.counter_last[key] = value

    def advance_page(self, t, item_count):
        """Rotate the incident pages by themselves; a new problem jumps back to the first."""
        if item_count > self.last_items:
            self.page, self.page_t = 1, t  # jump to incident page (1), not doomsday (0)
        self.last_items = item_count
        if not self.paused and t - self.page_t >= ROTATE_S:
            # Rotate through incident pages (1-5), skipping doomsday (0)
            nxt = (self.page + 1) % len(PAGES)
            if nxt == 0:   # skip doomsday page
                nxt = 1
            self.page, self.page_t = nxt, t

    def advance_home(self, t):
        """Turn the normal pages by themselves (the overview stays longest). Not while held, not while an
        incident's normal view is open on Space."""
        name = HOME_PAGES[self.home % len(HOME_PAGES)]
        if not self.paused and not self.force_overview and t - self.home_t >= HOME_DWELL_S[name]:
            self.home, self.home_t = (self.home + 1) % len(HOME_PAGES), t

    def turn_home(self, step):
        self.home, self.home_t = (self.home + step) % len(HOME_PAGES), time.monotonic()

    def turn_page(self, step):
        # Manual page navigation: skip doomsday page (0)
        nxt = (self.page + step) % len(PAGES)
        if nxt == 0:   # skip doomsday page
            nxt = 1 if step > 0 else len(PAGES) - 1
        self.page, self.page_t = nxt, time.monotonic()


# --- canvas --------------------------------------------------------------------------

class Canvas:
    """A grid of cells: (character, foreground RGB, background RGB or None, bold)."""

    def __init__(self, w, h, rich=True):
        self.w, self.h, self.rich = w, h, rich
        self.alert = "ok"
        self.cells = [[(" ", C["text"], None, False)] * w for _ in range(h)]

    def put(self, x, y, text, fg=None, bg=None, bold=False):
        if not 0 <= y < self.h:
            return x + len(str(text))
        fg = fg or C["text"]
        for ch in str(text):
            if not self.rich:
                ch = {"◆": "●", "⏵": "▲"}.get(ch, ch)
            if 0 <= x < self.w:
                # Text keeps the background already there (panels, bars) unless given one.
                self.cells[y][x] = (ch, fg, bg if bg is not None else self.cells[y][x][2], bold)
            x += 1
        return x

    def fill(self, x, y, w, h, bg):
        for yy in range(y, y + h):
            for xx in range(x, x + w):
                if 0 <= yy < self.h and 0 <= xx < self.w:
                    ch, fg, _, bold = self.cells[yy][xx]
                    self.cells[yy][xx] = (ch, fg, bg, bold)

    def edge_cells(self):
        for x in range(self.w):
            yield x, 0
            yield x, self.h - 1
        for y in range(1, self.h - 1):
            yield 0, y
            yield self.w - 1, y

    def ring(self, colour):
        """Colour the outermost cells' background (the alert glow)."""
        if colour is None:
            return
        plain = (None, C["bg"], C["panel"])
        if not hasattr(self, "ring_cells"):          # remember which cells are plain background
            self.ring_cells = [(x, y) for x, y in self.edge_cells() if self.cells[y][x][2] in plain]
        for x, y in self.ring_cells:                 # pills and badges keep their own colour
            ch, fg, _, bold = self.cells[y][x]
            self.cells[y][x] = (ch, fg, colour, bold)

    def ring_ansi(self, colour):
        """Only the edge, for the animation frames between full redraws."""
        self.ring(colour)
        out, last, cursor = [], None, None
        for x, y in sorted(getattr(self, "ring_cells", []), key=lambda p: (p[1], p[0])):
            ch, fg, bg, bold = self.cells[y][x]
            if cursor != (x, y):                    # move only where the run of cells breaks
                out.append(f"\x1b[{y + 1};{x + 1}H")
            key = (fg, bg, bold)
            if key != last:
                out.append(f"\x1b[{self.sgr(fg, bg, bold)}m")
                last = key
            out.append(ch)
            cursor = (x + 1, y)
        return "".join(out) + "\x1b[0m"

    def sgr(self, fg, bg, bold):
        if self.rich:
            code = f"0;{'1;' if bold else ''}38;2;{fg[0]};{fg[1]};{fg[2]}"
            return code + (f";48;2;{bg[0]};{bg[1]};{bg[2]}" if bg else "")
        i = nearest16(fg)
        return f"0;{'1;' if bold or i >= 8 else ''}{30 + i % 8}" + (f";{40 + nearest16(bg, True)}" if bg else "")

    def text(self):
        return "\n".join("".join(c[0] for c in row).rstrip() for row in self.cells)

    def ansi(self, full_frame=False):
        """Escape sequences for the terminal: 24-bit colour (rich) or 16 colours."""
        out = []
        for y, row in enumerate(self.cells):
            line = [f"\x1b[{y + 1};1H"] if full_frame else []
            last = None
            for ch, fg, bg, bold in row:
                key = (fg, bg, bold)
                if key != last:
                    line.append(f"\x1b[{self.sgr(fg, bg, bold)}m")
                    last = key
                line.append(ch)
            out.append("".join(line) + "\x1b[0m")
        return ("" if full_frame else "\n").join(out)


# --- widgets -------------------------------------------------------------------------

def clip(text, width):
    if width <= 0:
        return ""
    return text if len(text) <= width else text[: width - 1] + "…"


def fmt_count(n):
    """1234 -> 1.2k, 1234567 -> 1.2M."""
    for div, unit in ((1e6, "M"), (1e3, "k")):
        if n >= div:
            return f"{n / div:.1f}{unit}"
    return f"{n:.0f}"


def fmt_duration(s):
    d, h, m = s // 86400, s % 86400 // 3600, s % 3600 // 60
    return f"{d}d {h}h" if d else f"{h}h {m}m" if h else f"{m}m"


def nice_top(values, minimum):
    top = max(list(values) + [minimum])
    for exp in range(0, 13):
        for step in (1, 2, 5):
            if step * 10 ** exp >= top:
                return step * 10 ** exp
    return top


def box(c, x, y, w, h, title, colour, right=""):
    """Rounded panel with the title and an optional note set into the top border."""
    if w < 6 or h < 2:
        return
    line = mix(colour, C["bg"], 0.55) if c.rich else colour
    c.put(x, y, "╭" + "─" * (w - 2) + "╮", line)
    for row in range(y + 1, y + h - 1):
        c.put(x, row, "│", line)
        c.put(x + w - 1, row, "│", line)
    c.put(x, y + h - 1, "╰" + "─" * (w - 2) + "╯", line)
    end = c.put(x + 2, y, " ", line)
    end = c.put(end, y, "◆ " if c.rich else "", colour)
    end = c.put(end, y, title.upper(), colour, bold=True)
    c.put(end, y, " ", line)
    if right and len(right) + end + 4 < x + w:
        c.put(x + w - 3 - len(right), y, f" {right} ", C["dim"])


def meter(c, x, y, w, frac, gradient):
    """btop-style meter: a row of ■, coloured along the gradient up to the value."""
    if w <= 0:
        return
    frac = min(max(frac or 0.0, 0.0), 1.0)
    filled = round(frac * w)
    for i in range(w):
        if i < filled:
            c.put(x + i, y, "■", grad(gradient, i / max(w - 1, 1)))
        else:
            c.put(x + i, y, "■" if c.rich else "·", C["faint"] if c.rich else C["off"])


BRAILLE_LEFT = (0x40, 0x04, 0x02, 0x01)    # dots 7, 3, 2, 1: bottom to top
BRAILLE_RIGHT = (0x80, 0x20, 0x10, 0x08)   # dots 8, 6, 5, 4


def graph(c, x, y, w, h, values, top, gradient, down=False):
    """Area graph, newest on the right. Rich: braille, two samples per cell and four
    levels per row. Console: one sample per cell, █ full and ▒ half cells.
    down=True grows from the top edge (for the mirrored upload graph)."""
    if w <= 0 or h <= 0:
        return
    per_cell = 2 if c.rich else 1
    levels = 4 if c.rich else 2
    vals = list(values)[-w * per_cell:]
    vals = [0.0] * (w * per_cell - len(vals)) + vals
    total = h * levels

    def height(v):
        n = round(min(max(v / top if top else 0, 0), 1) * total)
        return 1 if v > 0 and n == 0 else n

    for col in range(w):
        hs = [height(v) for v in vals[col * per_cell:(col + 1) * per_cell]]
        for row in range(h):
            from_edge = row if down else h - 1 - row
            colour = grad(gradient, (from_edge + 0.5) / h)
            if c.rich:
                bits = 0
                for side, dots in ((0, BRAILLE_LEFT), (1, BRAILLE_RIGHT)):
                    fill = min(max(hs[side] - from_edge * 4, 0), 4)
                    order = dots if not down else dots[::-1]
                    for k in range(fill):
                        bits |= order[k]
                ch = chr(0x2800 + bits) if bits else " "
                c.put(x + col, y + row, ch if bits else " ", colour)
            else:
                fill = hs[0] - from_edge * 2
                ch = "█" if fill >= 2 else "▒" if fill == 1 else " "
                c.put(x + col, y + row, ch, colour)


def pill(c, x, y, state, width=6):
    text = STATE_TEXT.get(state, "OFF").center(width)
    fg = C["pill_fg"] if c.rich else C["bright"]      # the console has no dark-on-bright that reads well
    return c.put(x, y, text, fg, STATE_COLOUR.get(state, C["off"]), bold=True)


def dot(c, x, y, state):
    return c.put(x, y, "●", STATE_COLOUR.get(state, C["off"]))


# --- assessment ----------------------------------------------------------------------

def alert_level(problems):
    return "critical" if any(p[0] == "bad" for p in problems) else "warning" if problems else "ok"


def public_route_text(route):
    if route.get("up"):
        return "on · Caddy and Cloudflare tunnel running"
    return f"down · tunnel {route.get('tunnel', '?')}, Caddy {route.get('caddy', '?')}"


def connections(sampler):
    s, now = sampler.status or {}, sampler.now
    ts = s.get("tailscale", {}) if s else {}
    rows = []
    iface = sampler.iface
    if iface:
        sig = now.get("signal")
        kind = "Wi-Fi" if sig is not None or iface.startswith("wl") else "wired / USB"
        rows.append(("ok" if sig is None or sig > -75 else "warn", "Internet",
                     f"{kind} · {iface}" + (f" · {sig} dBm" if sig is not None else "")))
    else:
        rows.append(("bad", "Internet", "not connected"))
    if not s:
        rows.append(("off", "Tailscale", "not set up yet"))
    else:
        state, peers = ts.get("state", "unknown"), ts.get("peers_online")
        rows.append(("ok" if state == "Running" else "bad", "Tailscale",
                     state + (f" · {peers} device(s) online" if peers is not None and state == "Running" else "")))
    dom = s.get("public_domain", "") if s else ""
    route = s.get("public_route") if s else None
    if dom:
        pok = s.get("public_ok")
        rows.append(("ok" if pok else "bad" if pok is False else "warn", "Public API",
                     f"{dom} · {'answering' if pok else 'not answering' if pok is False else 'checking'}"))
    elif route:   # a gateway that runs but whose hostname the Mac was never told
        rows.append(("ok" if route.get("up") else "bad", "Public API", public_route_text(route)))
    else:
        rows.append(("off", "Public API", "off · tailnet only"))
    cl = s.get("claude", {}) if s else {}
    if cl.get("installed"):
        active = cl.get("state") == "active"
        rows.append(("ok" if active else "off", "Claude",
                     "session running · claude.ai/code" if active else "start a session from the admin page"))
    synced = s.get("clock_synced") if s else None
    rows.append(("ok" if synced else "warn" if synced is False else "off", "Clock",
                 "synced" if synced else "not synced" if synced is False else "unknown"))
    upd = s.get("host", {}).get("updates_pending", 0) if s else 0
    rows.append(("warn" if upd else "ok", "Updates", f"{upd} pending · sudo macserver update" if upd else "up to date"))
    mu = s.get("macserver_update") if s else None
    if mu:
        state, version = mu.get("state", ""), (mu.get("current") or "")[:7]
        level = {"up-to-date": "ok", "updated": "ok", "waiting-ci": "off", "available": "off", "updating": "warn",
                 "waiting": "off", "off": "off"}.get(state, "warn")
        text = {"up-to-date": f"up to date · {version}", "updated": f"updated · {version}",
                "waiting-ci": "new version in testing", "available": "new version ready",
                "updating": "installing a new version", "waiting": "after setup", "off": "auto-update off"}
        rows.append((level, "MacServer", text.get(state, mu.get("message", state))))
    idle = s.get("idle") or {}
    mode = idle.get("mode", "auto")
    if mode == "off":
        rows.append(("off", "Idle", "off · sudo macserver idle auto to enable"))
    elif idle.get("idle"):
        since = idle.get("since")
        if isinstance(since, (int, float)) and since > 0:
            ago = max(int(time.time() - since), 0)
            rows.append(("off", "Idle", f"low-power · quiet {fmt_duration(ago)}"))
        else:
            rows.append(("off", "Idle", "low-power"))
    else:
        after = idle.get("after_s", 900)
        try:
            mins = max(int(after) // 60, 1)
        except (TypeError, ValueError):
            mins = 15
        rows.append(("ok", "Idle", f"active · low-power after {mins}m quiet"))
    return rows


# --- panels --------------------------------------------------------------------------

def top_bar(c, sampler, problems):
    w, now, s = c.w, sampler.now, sampler.status or {}
    name = (s.get("tailscale", {}) or {}).get("name") or "macserver"
    dd_state = (s.get("doomsday") or {}).get("state", "normal")
    if dd_state == "doomsday":
        name = "DOOMSDAY " + name  # prefix: lockdown is the first thing to read, even on narrow screens
    elif dd_state == "debug":
        name = "DEBUG " + name
    elif (s.get("idle") or {}).get("idle"):
        name += " · IDLE"
    c.fill(0, 0, w, 1, C["panel"] if c.rich else None)
    x = c.put(1, 0, "◆ ", C["accent"], C["panel"] if c.rich else None)
    x = c.put(x, 0, "MACSERVER", C["bright"], C["panel"] if c.rich else None, bold=True)
    x = c.put(x + 2, 0, name, C["dim"], C["panel"] if c.rich else None)
    worst = "bad" if any(p[0] == "bad" for p in problems) else "warn" if problems else "ok"
    label = {"ok": " ALL SYSTEMS NORMAL ", "warn": f" {len(problems)} NOTICE(S) ",
             "bad": f" ▲ CRITICAL · {len(problems)} ISSUE(S) ▲ "}[worst]
    pct, stat, watts = now.get("battery") or (None, None, None)
    clock = time.strftime("%H:%M:%S")
    up = f"up {fmt_duration(now.get('uptime', 0))}"
    right_w = len(clock) + len(up) + 4 + (30 if pct is not None and w >= 110 else 0)
    cx = max(x + 3, (w - len(label)) // 2)
    if cx + len(label) + right_w + 2 > w:
        cx = max(x + 2, w - right_w - len(label) - 2)
    c.put(cx, 0, label, C["pill_fg"] if c.rich else C["bright"], STATE_COLOUR[worst], bold=True)
    rx = w - right_w
    if pct is not None and w >= 110:
        arrow = "▲" if stat == "Charging" else "▼" if stat == "Discharging" else "■"
        xx = c.put(rx, 0, f"BAT {arrow} ", C["dim"], C["panel"] if c.rich else None)
        xx = c.put(xx, 0, f"{pct:3d}% ", C["bright"], C["panel"] if c.rich else None)
        meter(c, xx, 0, 10, pct / 100, "bat")
        xx += 11
        c.put(xx, 0, f"{watts:4.1f}W" if watts else "", C["dim"], C["panel"] if c.rich else None)
    xx = c.put(w - len(clock) - len(up) - 3, 0, clock, C["bright"], C["panel"] if c.rich else None, bold=True)
    c.put(xx + 2, 0, up, C["dim"], C["panel"] if c.rich else None)
    return worst


def cpu_panel(c, sampler, x, y, w, h):
    now = sampler.now
    temp = now.get("temp")
    freq = now.get("mhz")
    right = f"{sampler.model}" + (f" · {freq / 1000:.1f} GHz" if freq else "")
    box(c, x, y, w, h, "cpu", C["cpu"], clip(right, max(w // 2, 10)))
    cores = now.get("cores") or []
    side_w = min(max(46, w * 30 // 100), 72) if w >= 100 else 0
    gw = w - 2 - (side_w + 1 if side_w else 0)
    gh = h - 2
    graph(c, x + 1, y + 1, gw, gh, sampler.hist["cpu"], 100, "cpu")
    c.put(x + 2, y + 1, f" {now['cpu']:3.0f}% ", C["bright"], bold=True)
    secs = int(gw * (2 if c.rich else 1) * SAMPLE_S)
    c.put(x + 2, y + h - 1, f" last {secs // 60} min " if secs >= 120 else f" last {secs} s ", C["dim"])
    if not side_w:
        return
    sx = x + w - side_w - 1
    for row in range(y + 1, y + h - 1):
        c.put(sx - 1, row, "│", mix(C["cpu"], C["bg"], 0.7) if c.rich else C["off"])
    lines = []
    lines.append(("CPU", now["cpu"], sampler.hist["cpu"], temp))
    core_temps = now.get("core_temps") or {}
    for i, v in enumerate(cores):
        lines.append((f"C{i}", v, sampler.hist[f"core{i}"], core_temps.get(sampler.core_of.get(i, i))))
    # The total (a meter; the big graph shows its history), then each core: a line with
    # meter, % and temperature, its own graph under it, and a gap before the next core.
    meter_w = max(side_w - 22, 6)
    room = gh - 3                              # rows for the lines, above fan/load/tasks
    cores_room = room - 2
    per = max(1, min(5, cores_room // max(len(lines) - 1, 1)))
    row = y + 1
    for n, (label, value, hist, t) in enumerate(lines):
        rows_here = 2 if n == 0 else per
        if row + (1 if n == 0 else min(per, 1)) > y + 1 + room:
            break
        xx = c.put(sx + 1, row, f"{label:<4}", C["cpu"] if label == "CPU" else C["dim"], bold=label == "CPU")
        meter(c, xx, row, meter_w, value / 100, "cpu")
        xx = c.put(xx + meter_w + 1, row, f"{value:3.0f}%", C["bright"])
        if t is not None:
            c.put(xx + 1, row, f"{t:3.0f}°C", grad("temp", temp_pos(t)))
        graph_rows = per - 2 if per >= 3 else per - 1
        if n > 0 and graph_rows > 0 and c.rich:
            graph(c, sx + 5, row + 1, side_w - 6, graph_rows, hist, 100, "cpu")
        row += rows_here
    if row < y + h - 3:
        # Fan: speed against the controller's target (FAN_MIN_PCT floor), with its trend.
        fans = now.get("fans") or []
        fi = sampler.fans or {}
        fan_max = next((f.get("max_rpm") for f in fi.get("fans", []) if f.get("max_rpm")), None) or 6000
        xx = c.put(sx + 1, y + h - 4, "FAN ", C["conn"], bold=True)
        if fans:
            meter(c, xx, y + h - 4, 12, fans[0] / fan_max, "up")
            xx = c.put(xx + 13, y + h - 4, f"{fans[0]:5d} rpm", C["bright"])
            mode = (f"  target {fi['target_pct']:.0f}% · min {fi.get('min_pct', 60):.0f}%" if fi.get("target_pct")
                    else f"  {fi.get('mode', 'auto')}" if fi else "  Mac's own control")
            xx = c.put(xx, y + h - 4, clip(mode, sx + side_w - xx - 12), C["dim"])
            spark = sx + side_w - xx - 2
            if spark >= 6 and c.rich:
                graph(c, xx + 1, y + h - 4, spark, 1, sampler.hist["fan"], fan_max, "up")
        else:
            c.put(xx, y + h - 4, "no fan reading", C["dim"])
        la = now.get("load") or [0, 0, 0]
        info = f"load {la[0]:.2f} {la[1]:.2f} {la[2]:.2f}"
        c.put(sx + 1, y + h - 3, clip(info, side_w - 1), C["dim"])
        freq = now.get("mhz")
        busy = (f"tasks {now.get('running', 0)}/{now.get('tasks', 0)}   ctx {fmt_count(now['ctxt'])}/s   "
                f"irq {fmt_count(now['intr'])}/s" + (f"   {freq / 1000:.2f} GHz" if freq else ""))
        c.put(sx + 1, y + h - 2, clip(busy, side_w - 1), C["dim"])


def mem_panel(c, sampler, x, y, w, h):
    m = sampler.now["mem"]
    box(c, x, y, w, h, "memory", C["mem"], f"{fmt_bytes(m['total'])} total")
    rows = [("Used", m["used"], m["total"], "mem"), ("Available", m["available"], m["total"], "disk"),
            ("Cached", m["cached"], m["total"], "disk")]
    if m["swap_total"]:
        rows.append(("Swap", m["swap_used"], m["swap_total"], "mem"))
    used, total = sampler.now.get("disk", (0, 0))
    rows.append(("Disk /", used, total, "disk"))
    inner = w - 2
    mw = max(inner - 30, 4)
    row = y + 1
    for label, v, tot, g in rows:
        if row >= y + h - 1:
            break
        frac = v / tot if tot else 0
        xx = c.put(x + 2, row, f"{label:<10}", C["dim"])
        xx = c.put(xx, row, f"{fmt_bytes(v):>9} ", C["bright"])
        meter(c, xx, row, mw, frac, g if label != "Disk /" else "mem" if frac > 0.8 else "disk")
        c.put(xx + mw + 1, row, f"{frac * 100:3.0f}%", C["text"])
        row += 1
    gh = y + h - 1 - row
    if gh >= 6:
        # Memory use above; disk reads (up) and writes (down) mirrored below, like btop.
        mh = gh // 2
        graph(c, x + 1, row, inner, mh, sampler.hist["mem"], 100, "mem")
        io_top = nice_top(list(sampler.hist["rd"])[-inner * 2:] + list(sampler.hist["wr"])[-inner * 2:], 1_000_000)
        rh = (gh - mh) // 2
        graph(c, x + 1, row + mh, inner, rh, sampler.hist["rd"], io_top, "disk")
        graph(c, x + 1, row + mh + rh, inner, gh - mh - rh, sampler.hist["wr"], io_top, "mem", down=True)
        xx = c.put(x + 2, row + mh, "▲ read ", C["accent"])
        xx = c.put(xx, row + mh, f"{fmt_bytes(sampler.now['rd'], True):>9}", C["bright"], bold=True)
        c.put(xx + 3, row + mh, f"▼ write {fmt_bytes(sampler.now['wr'], True)}", C["mem"])
        c.put(x + w - 3 - len(fmt_bytes(io_top, True)), row + mh, fmt_bytes(io_top, True), C["dim"])
    elif gh >= 1:
        io = f"disk read {fmt_bytes(sampler.now['rd'], True)}  write {fmt_bytes(sampler.now['wr'], True)}"
        c.put(x + 2, row, clip(io, inner - 2), C["dim"])
        if gh >= 3:
            graph(c, x + 1, row + 1, inner, gh - 1, sampler.hist["mem"], 100, "mem")


def net_panel(c, sampler, x, y, w, h):
    now = sampler.now
    iface = sampler.iface or "offline"
    box(c, x, y, w, h, "network", C["net"], iface)
    inner, gh = w - 2, h - 2
    top_h = gh // 2
    bot_h = gh - top_h
    down_top = nice_top(list(sampler.hist["rx"])[-inner * 2:], 100_000)
    up_top = nice_top(list(sampler.hist["tx"])[-inner * 2:], 100_000)
    graph(c, x + 1, y + 1, inner, top_h, sampler.hist["rx"], down_top, "down")
    graph(c, x + 1, y + 1 + top_h, inner, bot_h, sampler.hist["tx"], up_top, "up", down=True)
    xx = c.put(x + 2, y + 1, "▼ ", C["net"], bold=True)
    xx = c.put(xx, y + 1, f"{fmt_bytes(now['rx'], True):>10}", C["bright"], bold=True)
    c.put(xx + 2, y + 1, f"total {fmt_bytes(now.get('rx_total'))}", C["dim"])
    c.put(x + w - 3 - len(fmt_bytes(down_top, True)), y + 1, fmt_bytes(down_top, True), C["dim"])
    xx = c.put(x + 2, y + h - 2, "▲ ", C["conn"], bold=True)
    xx = c.put(xx, y + h - 2, f"{fmt_bytes(now['tx'], True):>10}", C["bright"], bold=True)
    c.put(xx + 2, y + h - 2, f"total {fmt_bytes(now.get('tx_total'))}", C["dim"])
    c.put(x + w - 3 - len(fmt_bytes(up_top, True)), y + h - 2, fmt_bytes(up_top, True), C["dim"])
    if gh >= 6:   # the tailnet's share of the traffic, on the line between the two graphs
        mid = y + 1 + top_h
        xx = c.put(x + 2, mid, "tailnet ", C["conn"], bold=True)
        c.put(xx, mid, f"▼ {fmt_bytes(now['ts_rx'], True)}  ▲ {fmt_bytes(now['ts_tx'], True)}", C["bright"])


def conn_panel(c, sampler, x, y, w, h):
    rows = connections(sampler)
    s = sampler.status or {}
    box(c, x, y, w, h, "connections", C["conn"])
    row = y + 1
    for state, label, value in rows:
        if row >= y + h - 1:
            break
        xx = pill(c, x + 2, row, state)
        xx = c.put(xx + 2, row, f"{label:<11}", C["dim"])
        c.put(xx, row, clip(value, x + w - 2 - xx), C["bright"])
        row += 1
    name = (s.get("tailscale", {}) or {}).get("name")
    links = [("Admin", f"https://{name}/"), ("Studio", f"https://{name}:{STUDIO_PORT}")] if name else []
    for label, url in links:
        if row + 1 >= y + h - 1:
            break
        row += 1
        xx = c.put(x + 2, row, f"{label:<8}", C["dim"])
        c.put(xx, row, clip(url, x + w - 2 - xx), C["accent"])


EVENT_COLOUR = {"ok": C["ok"], "warn": C["warn"], "bad": C["bad"], "info": C["accent"]}


def events_panel(c, sampler, x, y, w, h):
    items = list(sampler.events.items)
    box(c, x, y, w, h, "events", C["accent"], f"{len(items)} logged")
    for n, (when, level, text) in enumerate(items[: h - 2]):
        row = y + 1 + n
        xx = c.put(x + 2, row, time.strftime("%H:%M:%S", time.localtime(when)), C["dim"])
        xx = c.put(xx + 1, row, "●", EVENT_COLOUR.get(level, C["accent"]))
        c.put(xx + 1, row, clip(text, x + w - 2 - xx - 1), C["bright"] if level in ("bad", "warn") else C["text"])


BIG = {  # 5-row block letters for the incident banner
    "C": [" ███ ", "█    ", "█    ", "█    ", " ███ "], "R": ["████ ", "█   █", "████ ", "█  █ ", "█   █"],
    "I": ["███", " █ ", " █ ", " █ ", "███"], "T": ["█████", "  █  ", "  █  ", "  █  ", "  █  "],
    "A": [" ███ ", "█   █", "█████", "█   █", "█   █"], "L": ["█    ", "█    ", "█    ", "█    ", "█████"],
}


def big_text(c, x, y, word, gradient):
    for letter in word:
        rows = BIG[letter]
        for r, line in enumerate(rows):
            for i, ch in enumerate(line):
                if ch != " ":
                    c.put(x + i, y + r, "█", grad(gradient, 1 - r / 5))
        x += len(rows[0]) + 1
    return x


# --- incident war room ---------------------------------------------------------------------
# When something is critical the screen becomes a debugging console: what is wrong and why,
# the facts and the exact commands, the files to open, and pages for the network, inbound and
# outbound traffic, temperatures, processes and logs. It rotates through the pages by itself.

PAGES = ("doomsday", "incident", "network", "requests", "system", "logs")
ROTATE_S = 8.0
CLK_TCK = os.sysconf("SC_CLK_TCK") if hasattr(os, "sysconf") else 100
PAGE_BYTES = os.sysconf("SC_PAGE_SIZE") if hasattr(os, "sysconf") else 4096
SUPA = "/opt/macserver/supabase"
SERVICE_FILES = {
    "db": [f"{SUPA}/volumes/db  (Postgres data)", f"{SUPA}/.env  (POSTGRES_*; never print it)"],
    "edge-functions": [f"{SUPA}/volumes/functions/  (function source)", f"{SUPA}/.env  (function secrets)"],
    "storage": [f"{SUPA}/volumes/storage/  (uploaded files)"],
    "auth": [f"{SUPA}/.env  (GOTRUE_*, SITE_URL)"],
    "rest": [f"{SUPA}/.env  (PGRST_*, exposed schemas)"],
    "envoy": [f"{SUPA}/docker-compose.yml  (api gateway)"],
    "caddy": ["/opt/macserver/gateway/Caddyfile  (public path filter)", "/opt/macserver/gateway/compose.yml"],
    "cloudflared": ["/opt/macserver/gateway/compose.yml  (tunnel)", "/etc/macserver/macserver.conf  (PUBLIC_DOMAIN)"],
}


def read_procs():
    """{pid: (name, cpu ticks, resident bytes)} for every process (world-readable /proc)."""
    out = {}
    for d in PROC.glob("[0-9]*"):
        text = read(d / "stat")
        i = text.rfind(")")
        if i < 0:
            continue
        rest = text[i + 2:].split()
        try:
            out[int(d.name)] = (text[text.find("(") + 1:i], int(rest[11]) + int(rest[12]), int(rest[21]) * PAGE_BYTES)
        except (ValueError, IndexError):
            continue
    return out


def group_procs(cur, prev, dt):
    """Busiest and biggest programs (processes with the same name are added together)."""
    groups = {}
    for pid, (name, ticks, rss) in cur.items():
        before = prev.get(pid)
        cpu = (ticks - before[1]) / CLK_TCK / dt * 100 if before and ticks >= before[1] and dt > 0 else 0.0
        g = groups.setdefault(name, {"name": name, "cpu": 0.0, "rss": 0, "n": 0})
        g["cpu"] += cpu
        g["rss"] += rss
        g["n"] += 1
    rows = list(groups.values())
    return {"cpu": sorted(rows, key=lambda g: -g["cpu"])[:8], "mem": sorted(rows, key=lambda g: -g["rss"])[:8]}


def thermal_readings():
    """Every temperature sensor: [{chip, label, temp, limit}]."""
    rows = []
    for mon in sorted((SYS / "class/hwmon").glob("hwmon*")):
        chip = read(mon / "name").strip() or mon.name
        for t in sorted(mon.glob("temp*_input")):
            try:
                value = int(read(t, "0")) / 1000
            except ValueError:
                continue
            if not 0 < value < 125:
                continue
            base = t.name.replace("_input", "")
            limit = None
            for suffix in ("_crit", "_max", "_high"):
                try:
                    limit = int(read(t.with_name(base + suffix), "")) / 1000
                    break
                except ValueError:
                    continue
            rows.append({"chip": chip, "label": read(t.with_name(base + "_label")).strip() or base,
                         "temp": value, "limit": limit})
    return rows


def debug_of(sampler):
    return ((sampler.status or {}).get("debug")) or {}


def fmt_ago(seconds):
    seconds = max(int(seconds), 0)
    return f"{seconds}s" if seconds < 90 else f"{seconds // 60}m" if seconds < 5400 else f"{seconds // 3600}h"


def http_colour(code):
    return C["bad"] if code >= 500 else C["warn"] if code >= 400 else C["accent"] if code >= 300 else C["text"]


def line(c, x, y, w, *parts):
    """Coloured text pieces left to right, clipped to w columns. Returns where it stopped."""
    end = x + w
    for part in parts:
        if x >= end:
            break
        x = c.put(x, y, clip(str(part[0]), end - x), part[1], bold=len(part) > 2 and part[2])
    return x


def fields(c, x, y, right, *cols):
    """Fixed-width columns, each (text, colour, width[, bold]); width None takes what is left.
    Nothing is ever drawn at or past `right`, so a narrow panel cannot spill into its neighbour."""
    for col in cols:
        room = right - x
        if room <= 0:
            break
        width = room if col[2] is None else min(col[2], room)
        c.put(x, y, clip(str(col[0]), width).ljust(width), col[1], bold=len(col) > 3 and col[3])
        x += width
    return x


def stack(y, h, weights):
    """Split a column into rows of these relative sizes: [(y, h), ...]."""
    total, out, used = sum(weights), [], 0
    for i, wt in enumerate(weights):
        rows = max(h - used, 0) if i == len(weights) - 1 else max(min(round(h * wt / total), h - used), 0)
        out.append((y + used, rows))
        used += rows
    return out


def fit(y, h, needs, grow=-1):
    """Rows for stacked panels that each want needs[i] rows; the spare rows go to panel `grow`.
    When they cannot all fit, every panel shrinks in proportion."""
    if sum(needs) >= h:
        return stack(y, h, needs)
    heights = list(needs)
    heights[grow] += h - sum(needs)
    out, used = [], 0
    for rows in heights:
        out.append((y + used, rows))
        used += rows
    return out


def columns(x, w, weights):
    """Split a row of the screen into columns of these relative sizes: [(x, w), ...]."""
    return [(x + a, b) for a, b in stack(0, w, weights)]


def bar(c, x, y, w, frac, colour):
    """A plain meter in one colour."""
    frac = min(max(frac, 0.0), 1.0)
    filled = round(frac * w)
    for i in range(w):
        c.put(x + i, y, "■" if c.rich or i < filled else "·", colour if i < filled else (C["faint"] if c.rich else C["off"]))


# --- what is wrong, with the facts and the way to look ------------------------------------------

def service_facts(name, info, now_mem, now_disk):
    facts = []
    if info:
        cause = "killed: OUT OF MEMORY" if info.get("oom") else "not killed for memory"
        facts.append(f"exit code {info.get('exit')} · {cause} · restarted {info.get('restarts') or 0} time(s)")
        if info.get("finished", "").startswith("20"):
            facts.append(f"stopped {info['finished'].replace('T', ' ')} UTC · started {info.get('started', '').replace('T', ' ')} UTC")
        if info.get("error"):
            facts.append(f"docker says: {info['error']}")
        if info.get("health_output"):
            facts.append(f"health check: {info['health_output']}")
    pressure = []
    if now_mem and now_mem.get("total") and now_mem["used"] / now_mem["total"] > 0.88:
        pressure.append(f"memory {100 * now_mem['used'] / now_mem['total']:.0f}% used")
    if now_disk and now_disk[1] and now_disk[0] / now_disk[1] > 0.9:
        pressure.append(f"disk {100 * now_disk[0] / now_disk[1]:.0f}% full")
    if pressure:
        facts.append("machine under pressure: " + " · ".join(pressure) + " (a likely cause)")
    return facts


def incidents(sampler):
    """Everything wrong right now, worst first: level, title, why, facts, steps, files, logs."""
    s, now, out = sampler.status or {}, sampler.now, []
    dbg = s.get("debug") or {}
    inspect = dbg.get("inspect") or {}

    def add(level, title, why="", fix=(), logs=(), facts=(), files=()):
        out.append({"level": level, "title": title, "why": why, "fix": list(fix), "logs": list(logs),
                    "facts": list(facts), "files": list(files)})

    if not s:
        if sampler.firstboot == "activating":
            add("warn", "setup is running", "first-boot setup is still installing MacServer",
                files=["/var/log/macserver-firstboot.log  (installer output)"])
        else:
            add("warn", "no health data yet", "setup has not finished, or the health collector stopped",
                ["press 2, log in, run: sudo /opt/macserver-src/install.sh",
                 "sudo systemctl restart macserver-status.timer",
                 "sudo journalctl -u macserver-status -n 30 --no-pager"],
                files=["/run/macserver/status.json  (should be under 5 s old)", "/usr/local/lib/macserver/collect_status.py"])
    else:
        dd = s.get("doomsday") or {}
        if dd.get("state") in ("doomsday", "debug"):
            full = dd.get("state") == "doomsday"
            since = dd.get("since")
            try:
                when = time.strftime("%H:%M", time.localtime(since)) if since else "?"
            except (TypeError, ValueError, OverflowError):
                when = "?"
            add("bad" if full else "warn",
                "DOOMSDAY LOCKDOWN: this Mac is isolated" if full else "doomsday DEBUG mode: still isolated",
                f"trigger: {dd.get('trigger') or '?'}; score {dd.get('score', 0)}; since {when}. "
                "Network cut, non-essential services stopped, alert sent before isolation.",
                (["sudo macserver doomsday status",
                  "sudo macserver doomsday unlock --code XXXXXX   (DEBUG mode, still isolated)",
                  "sudo macserver doomsday restore --code XXXXXX   (normal, everything auto-restored)"]
                 if full else
                 ["sudo macserver doomsday status",
                  "diagnose, then: sudo macserver doomsday restore --code XXXXXX   (normal, auto-restored)"]),
                facts=[f"trigger: {dd.get('trigger') or '?'}", f"score: {dd.get('score', 0)}"]
                + [f"finding: {r}" for r in (dd.get("reasons") or [])][:6],
                files=["/var/log/macserver-doomsday.log  (what happened, in order)",
                       "/var/lib/macserver/doomsday.json  (lockdown state)",
                       "/var/lib/macserver/nftables.before-doomsday  (firewall to restore)"])
        if not s.get("setup_done"):
            add("warn", "setup has not finished", "some install steps are still to do",
                ["press 2, log in, run: sudo /opt/macserver-src/install.sh"],
                files=["/var/log/macserver-firstboot.log", "/var/lib/macserver/install.state  (steps done)"])
        containers = s.get("containers", [])
        db_down = any(c["name"].endswith("supabase-db") and c.get("state") != "running" for c in containers)
        for c in containers:
            short = c["name"].split(".")[-1].replace("supabase-", "")
            gateway_name = short.replace("gateway-", "").rsplit("-", 1)[0] if short.startswith("gateway-") else short
            files = SERVICE_FILES.get(gateway_name, []) + [f"{SUPA}/docker-compose.yml  (how it is started)"]
            steps = [f"sudo docker logs --tail 50 {c['name']}",
                     f"sudo docker inspect {c['name']} --format '{{{{json .State}}}}'"]
            if c.get("state") != "running":
                if db_down and short != "db":
                    steps.insert(0, "the database is down too: fix the db service first, the rest depend on it")
                add("bad", f"service {short} is {c.get('state', 'down')}", c.get("status", ""),
                    steps + ["sudo macserver restart"], c.get("logs") or [],
                    service_facts(c["name"], inspect.get(c["name"]), now.get("mem"), now.get("disk")), files)
            elif "unhealthy" in c.get("status", ""):
                add("warn", f"service {short} is unhealthy", c.get("status", ""), steps, c.get("logs") or [],
                    service_facts(c["name"], inspect.get(c["name"]), now.get("mem"), now.get("disk")), files)
        net = dbg.get("net") or {}
        state = (s.get("tailscale") or {}).get("state")
        if state not in ("Running", None):
            facts = [f"Tailscale state: {state}"]
            if net.get("gateway"):
                facts.append(f"router {net['gateway']}: " + (f"answers in {net['gateway_ms']:.0f} ms" if net.get("gateway_ms") is not None else "does not answer"))
            if net.get("internet_ms") is not None:
                facts.append("the Internet is reachable, so this is Tailscale itself (login expired? key expired?)")
            elif net:
                facts.append("the Internet is NOT reachable: fix the network first, Tailscale will follow")
            add("bad", "Tailscale is not connected", f"state: {state}; the admin page and SSH are unreachable",
                ["sudo tailscale status", "sudo journalctl -u tailscaled -n 50 --no-pager", "sudo tailscale up --ssh --accept-dns=false",
                 "sudo systemctl restart tailscaled"], facts=facts,
                files=["/etc/nftables.conf  (firewall: only tailscale0 may connect)", "/var/lib/tailscale/  (node state)"])
        if s.get("age_s", 0) > 120:
            add("warn", "health data is old", f"last collected {int(s['age_s'] // 60)} minutes ago",
                ["sudo systemctl restart macserver-status.timer", "sudo journalctl -u macserver-status -n 30 --no-pager"],
                files=["/run/macserver/status.json", "/usr/local/lib/macserver/collect_status.py"])
        if s.get("public_domain") and s.get("public_ok") is False:
            gateway = [f"{c['name']}: {c.get('state')}" for c in containers if c.get("project") in ("macserver-gateway", "gateway")]
            add("bad", "public API not answering", f"https://{s['public_domain']}/auth/v1/health fails",
                ["sudo macserver public on", f"curl -sI https://{s['public_domain']}/auth/v1/health",
                 "sudo docker logs --tail 50 macserver-gateway-cloudflared-1", "check the tunnel in the Cloudflare dashboard"],
                facts=gateway or ["no gateway containers found: the public route is not installed or not running"],
                files=SERVICE_FILES["caddy"] + ["/etc/macserver/macserver.conf  (PUBLIC_DOMAIN)"])
        if (s.get("host") or {}).get("reboot_required"):
            add("warn", "restart needed for updates", "the kernel or a core library was updated",
                ["sudo reboot   (then type the disk passphrase)"], files=["/run/reboot-required"])
        mu = s.get("macserver_update") or {}
        if mu.get("state") in ("rolled-back", "refused"):
            add("warn", "a MacServer update did not install", mu.get("message", ""),
                ["sudo macserver autoupdate status", "sudo tail -50 /var/log/macserver-self-update.log"],
                files=["/var/log/macserver-self-update.log", "/var/lib/macserver/self-update.json"])
        system = dbg.get("system") or {}
        if system.get("failed_units"):
            units = system["failed_units"]
            add("warn", f"{len(units)} systemd unit(s) failed", ", ".join(units),
                [f"sudo systemctl status {units[0]} --no-pager", f"sudo journalctl -u {units[0]} -n 50 --no-pager",
                 f"sudo systemctl reset-failed {units[0]}   (after fixing it)"], facts=system.get("journal", [])[-3:])
        exposed = [r for r in dbg.get("listeners") or [] if r.get("scope") in ("all", "public", "lan")]
        if exposed:
            names = ", ".join(sorted({f":{r['port']} {r['proc']}" for r in exposed}))
            lan_in = [f for f in (dbg.get("flows") or {}).get("in", []) if f.get("kind") in ("lan", "public")]
            facts = [f"{len(lan_in)} LAN/Internet connection(s) into this Mac right now: "
                     + ", ".join(f"{f['src']} → :{f['port']}" for f in lan_in[:3])] if lan_in else []
            add("warn", f"{len(exposed)} port(s) reachable from the local network", names,
                ["sudo ss -tlnp | grep -E '0.0.0.0|\\[::\\]'", "sudo docker ps --format '{{.Names}}  {{.Ports}}'",
                 "MacServer rule: Docker publishes on 127.0.0.1 only; 0.0.0.0 also answers on the Wi-Fi/LAN"],
                facts=facts, files=["/etc/docker/daemon.json  (\"ip\": \"127.0.0.1\")", f"{SUPA}/docker-compose.yml  (ports:)",
                                    "/etc/nftables.conf"])
        req = dbg.get("requests") or {}
        if (req.get("classes") or {}).get("5xx"):
            n = req["classes"]["5xx"]
            last = next((e for e in req.get("errors", []) if e["status"] >= 500), None)
            add("warn", f"the API answered {n} request(s) with a server error in the last minute",
                f"{last['method']} {last['path']} → {last['status']} for {last['client']}" if last else "",
                ["sudo macserver logs rest", "sudo macserver logs db", "sudo docker logs --tail 50 supabase-envoy"],
                files=[f"{SUPA}/docker-compose.yml  (service wiring)"])
        counters = getattr(sampler, "counter_changed", {})
        if time.time() - counters.get("oom_kills", 0) < 600:
            add("warn", "the kernel killed a process: out of memory",
                f"{system.get('oom_kills')} kill(s) since boot", ["sudo dmesg -T | grep -i -E 'killed process|out of memory'",
                                                                 "sudo docker stats --no-stream"],
                facts=[f"memory now {100 * now['mem']['used'] / now['mem']['total']:.0f}% used" if now.get("mem") and now["mem"].get("total") else ""],
                files=["/proc/meminfo", f"{SUPA}/docker-compose.yml  (mem_limit)"])
        if time.time() - counters.get("thermal_throttle", 0) < 600:
            add("warn", "the CPU slowed itself down because of heat", f"{system.get('thermal_throttle')} throttle event(s) since boot",
                ["sensors", "systemctl status macserver-fans", "top -o %CPU"], files=["/sys/devices/system/cpu/cpu0/thermal_throttle/"])
        if sampler.iface and net.get("internet_fails", 0) >= 2:
            gw = net.get("gateway_ms")
            add("bad", "the Internet is unreachable",
                f"cannot connect to 1.1.1.1:443 ({net['internet_fails']} checks in a row)",
                ["ping -c 3 " + (net.get("gateway") or "the-router"), "ip route", "sudo macserver doctor",
                 "restart the router if the Wi-Fi is connected but nothing answers"],
                facts=[f"router {net.get('gateway') or '?'}: " + (f"answers in {gw:.0f} ms (the Wi-Fi link is fine, the router or ISP is not)" if gw is not None
                                                                   else "does NOT answer (Wi-Fi link or router problem)"),
                       f"DNS: {'works' if net.get('dns_ms') is not None else 'failing too'}"],
                files=["/etc/NetworkManager/system-connections/  (saved Wi-Fi)", "/etc/resolv.conf"])
        elif sampler.iface and net.get("dns_fails", 0) >= 2:
            add("warn", "DNS is not resolving names", "names cannot be looked up, but the Internet answers",
                ["resolvectl status || cat /etc/resolv.conf", "getent hosts registry-1.docker.io", "sudo tailscale dns status"],
                files=["/etc/resolv.conf", "/etc/NetworkManager/"])
    if not sampler.iface:
        add("bad", "no network connection", "no default route: Wi-Fi or cable is down",
            ["press 2, log in, run: sudo nmtui   (or plug in Ethernet)", "ip route", "sudo macserver doctor",
             "sudo dmesg | grep -i brcmfmac | tail"],
            facts=[f"Wi-Fi signal {now['signal']} dBm" if now.get("signal") is not None else "no Wi-Fi signal reading"],
            files=["/etc/NetworkManager/system-connections/  (saved Wi-Fi)"])
    temp = now.get("temp")
    stalled = cooling_fault(temp, now.get("fans"))
    if stalled:
        top = (sampler.procs.get("cpu") or [None])[0] if getattr(sampler, "procs", None) else None
        facts = [stalled + (f" · controller target {sampler.fans['target_pct']:.0f}%" if sampler.fans and sampler.fans.get("target_pct") else "")]
        if top and top["cpu"] > 20:
            facts.append(f"busiest program: {top['name']} at {top['cpu']:.0f}% of one core")
        add("warn", f"the fan is not turning ({temp:.0f}°C)", "the CPU is warm and nothing is cooling it",
            ["sensors", "systemctl status macserver-fans", "sudo macserver doctor", "keep the vents clear; use the charger"],
            facts=facts, files=["/sys/class/hwmon/  (sensor files)", "/run/macserver/fans.json  (fan controller)"])
    used, total = now.get("disk", (0, 0))
    if total and used / total > 0.9:
        add("warn", "disk almost full", f"{fmt_bytes(total - used)} free",
            ["sudo du -xh /var/lib/docker --max-depth=1 | sort -h | tail -5", "sudo docker system df",
             "ls -lh /var/backups/macserver", "sudo docker image prune   (asks first)"],
            files=["/var/lib/docker/", "/var/backups/macserver/", "/var/log/journal/"])
    problem = backup_problem(dbg.get("backups"))
    if problem:
        drive = dbg["backups"]["drive"]
        add("warn", "the USB backup drive needs attention", problem,
            ["sudo macserver backup-drive status", "sudo macserver backup   (plug the drive in first)"],
            facts=["drive: " + ("plugged in" if drive.get("present") else "NOT plugged in")],
            files=["/var/lib/macserver/backup.json  (what the last backup did)", "/etc/macserver/backup-drive.key  (opens the drive)"])
    out.sort(key=lambda i: i["level"] != "bad")
    return out


def backup_problem(backups):
    """Why the USB backup drive needs attention, or "" (also "" when no drive is set up: that case keeps
    its old, gentler weekly rule). A drive that is simply unplugged is fine until a backup is missed."""
    drive = (backups or {}).get("drive")
    if drive is None:
        return ""
    if drive.get("error"):
        return f"the last backup failed: {drive['error']}"
    if not backups.get("count"):
        return "no backup has been made yet"
    if backups.get("newest_age_s", 0) > 36 * 3600:
        return f"the newest backup is {int(backups['newest_age_s'] // 3600)} hours old"
    return ""


def assess(sampler):
    return [(i["level"], i["title"]) for i in incidents(sampler)]


def counted_issues(sampler):
    """The issues the screen counts, worst first: every incident except the known-benign notices it mutes.
    The top-bar pill, the notice lines above the pages, `macserver issues` and the texts all use this list."""
    return [i for i in incidents(sampler) if not muted_notice(sampler, i["level"], i["title"])]


def issues_report(sampler, brief=False):
    """`macserver issues` in words: each issue the screen counts with why, facts, fixes and where to look."""
    items = counted_issues(sampler)
    if not items:
        return "All systems normal: the screen shows no notices or alerts."
    bad = sum(1 for i in items if i["level"] == "bad")
    counts = [f"{bad} critical" if bad else "", f"{len(items) - bad} notice(s)" if len(items) > bad else ""]
    out = [" · ".join(c for c in counts if c)]
    for n, item in enumerate(items, 1):
        out.append(f"{n}. {'CRITICAL' if item['level'] == 'bad' else 'NOTICE'}  {item['title']}")
        if not brief:
            out += [f"     {label:<5} {text}" for label, text, _ in detail_rows(item)] + [""]
    if brief:
        out.append("details and fixes: macserver issues")
    return "\n".join(out).rstrip()


def muted_notice(sampler, level, title):
    """Warning notices left out of the top-bar pill and the edge glow.

    Two known-benign notices stay out of the label. They are still listed with
    the incident details, and the real alarms behind them still fire:
    - the failed-unit notice when the only failed unit is the Claude Remote
      Control session and Claude was never signed in (problem login/consent):
      pressing "Start session" before signing in leaves this failed state
      behind, which is setup, not a malfunction.
    - the heat-throttle notice: brief slowdowns under burst load at normal
      temperatures. A genuinely hot CPU still warns through "running hot".
    """
    if level != "warn":
        return False
    if title == "the CPU slowed itself down because of heat":
        return True
    if title.endswith("systemd unit(s) failed"):
        s = sampler.status or {}
        failed = set(((s.get("debug") or {}).get("system") or {}).get("failed_units") or [])
        problem = (s.get("claude") or {}).get("problem", "")
        if failed and failed <= {"macserver-claude.service"} and problem in ("login", "consent"):
            return True
    return False


# --- panels ------------------------------------------------------------------------------------

def detail_rows(item):
    """The lines under an incident's title: (label, text, colour)."""
    rows = []
    if item["why"]:
        rows.append(("why", item["why"], C["text"]))
    rows += [("info", f, C["bright"]) for f in item["facts"] if f]
    rows += [("fix" if k == 0 else "", f"{k + 1}) {cmd}" if len(item["fix"]) > 1 else cmd, C["accent"])
             for k, cmd in enumerate(item["fix"])]
    rows += [("look" if k == 0 else "", path, C["svc"]) for k, path in enumerate(item["files"])]
    rows += [("log" if k == 0 else "", entry, C["dim"]) for k, entry in enumerate(item["logs"][-6:])]
    return rows


def incident_list_panel(c, sampler, x, y, w, h, items):
    bad = sum(1 for i in items if i["level"] == "bad")
    box(c, x, y, w, h, "incident", C["bad"], f"{bad} critical · {len(items) - bad} warning")
    inner, row, bottom = w - 4, y + 1, y + h - 1
    for n, item in enumerate(items):
        rows = detail_rows(item)
        room = bottom - row - 1
        if room < 1:
            line(c, x + 2, bottom - 1, inner, (f"… {len(items) - n} more (n: next page, all listed in the events)", C["dim"]))
            return
        if len(rows) > room:                        # not enough room: the most useful lines first
            keep = [r for r in rows if r[0] in ("why", "info", "fix")] or rows
            rows = (keep + [r for r in rows if r not in keep])[:room]
        pill_end = pill(c, x + 2, row, item["level"])
        line(c, pill_end + 2, row, inner - 9, (f"{n + 1}. {item['title']}", C["bright"], True))
        row += 1
        for label, text, colour in rows:
            if row >= bottom:
                break
            c.put(x + 4, row, f"{label:<5}", C["dim"])
            line(c, x + 10, row, inner - 8, (text, colour))
            row += 1
        row += 1


def vitals_panel(c, sampler, x, y, w, h):
    box(c, x, y, w, h, "vitals", C["accent"], "live")
    now, hist = sampler.now, sampler.hist
    rows = [("CPU", hist["cpu"], 100, "cpu", f"{now['cpu']:3.0f}%"),
            ("MEM", hist["mem"], 100, "mem", f"{hist['mem'][-1]:3.0f}%" if hist["mem"] else ""),
            ("TEMP", hist["temp"], 100, "temp", f"{now['temp']:.0f}°C" if now.get("temp") is not None else ""),
            ("NET", hist["rx"], nice_top(list(hist["rx"])[-w * 2:], 100_000), "down", fmt_bytes(now["rx"], True)),
            ("DISK", hist["wr"], nice_top(list(hist["wr"])[-w * 2:], 1_000_000), "disk", fmt_bytes(now["wr"], True))]
    gh = max((h - 2) // len(rows), 1)
    for n, (label, values, top, g, value) in enumerate(rows):
        ry = y + 1 + n * gh
        if ry >= y + h - 1:
            break
        c.put(x + 2, ry, f"{label:<5}", C["dim"])
        c.put(x + w - 3 - len(value), ry, value, C["bright"], bold=True)
        graph(c, x + 8, ry, w - 12 - len(value), max(gh - (1 if gh > 2 else 0), 1), values, top, g)


def status_panel(c, sampler, x, y, w, h):
    rows = connections(sampler)
    box(c, x, y, w, h, "system state", C["conn"])
    for n, (state, label, value) in enumerate(rows[: h - 2]):
        xx = pill(c, x + 2, y + 1 + n, state)
        xx = c.put(xx + 2, y + 1 + n, f"{label:<11}", C["dim"])
        line(c, xx, y + 1 + n, x + w - 2 - xx, (value, C["text"]))


def services_panel_compact(c, sampler, x, y, w, h):
    containers = (sampler.status or {}).get("containers", [])
    up = sum(1 for i in containers if i.get("state") == "running")
    box(c, x, y, w, h, "services", C["svc"], f"{up}/{len(containers)} running")
    row, xx = y + 1, x + 2
    for item in sorted(containers, key=lambda i: (i.get("state") == "running", i["name"])):
        label = item["name"].split(".")[-1].replace("supabase-", "")
        running = item.get("state") == "running"
        if xx + len(label) + 4 > x + w - 2:
            row, xx = row + 1, x + 2
        if row >= y + h - 1:
            break
        xx = dot(c, xx, row, "ok" if running else "bad")
        xx = c.put(xx + 1, row, label, C["text"] if running else C["bad"], bold=not running) + 2


def thermal_panel(c, sampler, x, y, w, h):
    now = sampler.now
    temp = now.get("temp")
    box(c, x, y, w, h, "thermal", C["warn"] if cooling_fault(temp, now.get("fans")) else C["accent"],
        f"{(now.get('mhz') or 0) / 1000:.2f} GHz" if now.get("mhz") else "")
    inner, row, bottom = w - 4, y + 1, y + h - 1
    mw = max(inner - 26, 6)
    if temp is not None and row < bottom:
        xx = c.put(x + 2, row, f"CPU {temp:4.0f}°C ", C["bright"], bold=True)
        meter(c, xx, row, mw, (temp - 30) / 70, "temp")
        note = "at its limit" if temp >= 100 else "normal"       # 100 °C is the chip's own ceiling, not a fault
        c.put(xx + mw + 1, row, note, C["dim"])
        row += 1
    fans = now.get("fans") or []
    fi = sampler.fans or {}
    fan_max = next((f.get("max_rpm") for f in fi.get("fans", []) if f.get("max_rpm")), None) or 6000
    if row < bottom:
        if fans:
            xx = c.put(x + 2, row, f"FAN {fans[0]:5d} rpm ", C["bright"], bold=True)
            bar(c, xx, row, mw, fans[0] / fan_max, C["conn"])
            row += 1
            if row < bottom:
                if fi.get("target_pct"):
                    mode = f"controller: {fi.get('mode', '?')} · target {fi['target_pct']:.0f}% · floor {fi.get('min_pct', 60):.0f}%"
                elif fi:
                    mode = "controller: " + str(fi.get("mode", "auto"))
                else:
                    mode = "controller: none (the firmware's own control)"
                line(c, x + 2, row, inner, (mode, C["dim"]))
                row += 1
        else:
            line(c, x + 2, row, inner, ("no fan reading (fan controller stopped?)", C["warn"]))
            row += 1
    dbg = debug_of(sampler)
    throttle = (dbg.get("system") or {}).get("thermal_throttle")
    if throttle is not None and row < bottom:
        base = getattr(sampler, "counter_base", {}).get("thermal_throttle", throttle)
        line(c, x + 2, row, inner, (f"throttle events since boot: {throttle}" + (f"  (+{throttle - base} since this screen started)" if throttle > base else ""),
                                    C["bad"] if throttle > base else C["dim"]))
        row += 1
    bat = now.get("battery") or (None, None, None)
    if bat[0] is not None and row < bottom:
        line(c, x + 2, row, inner, (f"battery {bat[0]}% {bat[1]}" + (f" · {bat[2]:.1f} W" if bat[2] else ""), C["dim"]))
        row += 1
    row += 1
    for r in sorted(getattr(sampler, "thermal", []), key=lambda r: -r["temp"]):
        if row >= bottom:
            break
        limit = r["limit"] or 100
        colour = grad("temp", temp_pos(r["temp"]))
        xx = fields(c, x + 2, row, x + w - 2, (r["chip"], C["dim"], 10), (r["label"], C["dim"], 14),
                    (f"{r['temp']:4.0f}°C", colour, 7))
        bar(c, xx, row, max(x + w - 3 - xx, 0), r["temp"] / limit, colour)
        row += 1


def procs_panel(c, sampler, x, y, w, h):
    procs = getattr(sampler, "procs", None) or {"cpu": [], "mem": []}
    box(c, x, y, w, h, "busiest programs", C["cpu"], "by name")
    inner, row, bottom = w - 4, y + 1, y + h - 1
    half = max((h - 4) // 2, 1)
    for title, key in (("CPU (100% = one core)", "cpu"), ("MEMORY", "mem")):
        if row >= bottom:
            break
        line(c, x + 2, row, inner, (title, C["dim"], True))
        row += 1
        for g in procs[key][:half]:
            if row >= bottom:
                break
            count = f" x{g['n']}" if g["n"] > 1 else ""
            fields(c, x + 2, row, x + w - 2, (f"{g['cpu']:5.1f}%" if key == "cpu" else f"{fmt_bytes(g['rss']):>8}", C["bright"], 9),
                   (g["name"] + count, C["text"], None))
            row += 1
        if not procs[key] and row < bottom:
            line(c, x + 2, row, inner, ("no process data yet", C["dim"]))
            row += 1


def scope_tag(c, x, y, scope):
    text, colour = {"all": ("ALL NETS", C["bad"]), "public": ("PUBLIC", C["bad"]), "lan": ("LAN", C["warn"]),
                    "open": ("FW OPEN", C["accent"]), "blocked": ("FW DROP", C["off"]),
                    "tailnet": ("TAILNET", C["ok"]), "docker": ("DOCKER", C["off"]),
                    "loopback": ("LOCAL", C["off"])}.get(scope, (scope.upper()[:8], C["off"]))
    return c.put(x, y, text.center(8), C["pill_fg"] if c.rich else C["bright"], colour, bold=True)


def listeners_panel(c, sampler, x, y, w, h):
    rows = debug_of(sampler).get("listeners")
    exposed = [r for r in rows or [] if r["scope"] in ("all", "public", "lan")]
    opened = sum(r["scope"] == "open" for r in rows or [])
    box(c, x, y, w, h, "listening ports", C["bad"] if exposed else C["conn"],
        f"{len(exposed)} exposed" if exposed else f"{opened} open by firewall rule" if opened else "all private")
    inner, row, bottom = w - 4, y + 1, y + h - 1
    if rows is None:
        line(c, x + 2, row, inner, ("no port data (collector not updated yet)", C["dim"]))
        return
    for r in rows:
        if row >= bottom - (2 if exposed else 0):
            break
        xx = scope_tag(c, x + 2, row, r["scope"])
        fields(c, xx + 1, row, x + w - 2, (f":{r['port']}", C["bright"], 7, r["scope"] in ("all", "public", "lan")),
               (r["svc"], C["dim"], 9), (r["proc"], C["bad"] if r["scope"] in ("all", "public") else C["text"], None))
        row += 1
    if exposed and bottom - 2 >= row:
        line(c, x + 2, bottom - 2, inner, ("! ALL NETS / LAN = answers on the Wi-Fi/LAN too.", C["bad"], True))
        line(c, x + 2, bottom - 1, inner, ("MacServer rule: only 127.0.0.1 or the tailnet.", C["dim"]))


def flows_panel(c, sampler, x, y, w, h, direction):
    flows = debug_of(sampler).get("flows")
    title = "inbound connections" if direction == "in" else "outbound connections"
    box(c, x, y, w, h, title, C["conn"], f"{flows['tracked']} tracked" if flows else "")
    inner, row, bottom = w - 4, y + 1, y + h - 1
    if not flows:
        line(c, x + 2, row, inner, ("no connection data (collector not updated yet)", C["dim"]))
        return
    rows = flows[direction]
    if not rows:
        line(c, x + 2, row, inner, ("none right now", C["dim"]))
    for r in rows:
        if row >= bottom:
            break
        # Only a stranger reaching in is a colour. Where this Mac connects to is routine: a LAN router
        # or an Internet service is plain text.
        colour = {"public": C["bad"], "lan": C["warn"], "tailnet": C["accent"]}.get(r["kind"], C["text"]) \
            if direction == "in" else C["accent"] if r["kind"] == "tailnet" else C["text"]
        right = x + w - 2
        if direction == "in":
            fields(c, x + 2, row, right, (f"{r['n']}x", C["bright"], 5),
                   (r["src"], colour, min(max(inner * 45 // 100, 14), 30), r["kind"] in ("lan", "public")),
                   (f"→ :{r['port']}", C["bright"], 9), (r["svc"], C["dim"], None))
        else:
            fields(c, x + 2, row, right, (f"{r['n']}x", C["bright"], 5),
                   (r["who"], C["text"], min(max(inner * 26 // 100, 10), 18), True),
                   ("→ ", C["dim"], 2), (r["dst"], colour, min(max(inner * 34 // 100, 12), 26)),
                   (f":{r['port']}", C["bright"], 7), (r["svc"], C["dim"], None))
        row += 1


def netchecks_panel(c, sampler, x, y, w, h):
    net = debug_of(sampler).get("net") or {}
    box(c, x, y, w, h, "connectivity", C["conn"])
    row, bottom = y + 1, y + h - 1

    def check(label, ms, fails, detail):
        nonlocal row
        if row >= bottom:
            return
        state = "ok" if ms is not None and ms < 300 else "warn" if ms is not None or fails < 2 else "bad"
        xx = pill(c, x + 2, row, state)
        xx = c.put(xx + 2, row, f"{label:<11}", C["dim"])
        line(c, xx, row, x + w - 2 - xx, (detail + (f"  {ms:.0f} ms" if ms is not None else "  no answer"), C["bright"] if state == "ok" else STATE_COLOUR[state]))
        row += 1

    if net:
        check("Router", net.get("gateway_ms"), 0 if net.get("gateway_ms") is not None else 2, net.get("gateway") or "no default route")
        check("Internet", net.get("internet_ms"), net.get("internet_fails", 0), "1.1.1.1:443")
        check("DNS", net.get("dns_ms"), net.get("dns_fails", 0), "registry-1.docker.io")
        row += 1
    for state, label, value in connections(sampler):
        if row >= bottom:
            break
        xx = pill(c, x + 2, row, state)
        xx = c.put(xx + 2, row, f"{label:<11}", C["dim"])
        line(c, xx, row, x + w - 2 - xx, (value, C["text"]))
        row += 1


def requests_feed_panel(c, sampler, x, y, w, h):
    req = debug_of(sampler).get("requests")
    box(c, x, y, w, h, "inbound requests", C["svc"], f"{req['total']} in the last {req['window_s']} s" if req else "")
    inner, row, bottom = w - 4, y + 1, y + h - 1
    if req is None:
        line(c, x + 2, row, inner, ("no API gateway container found", C["dim"]))
        return
    path_w = min(max(inner * 38 // 100, 14), 60)
    right = x + w - 2
    fields(c, x + 2, row, right, ("time", C["dim"], 10), ("code", C["dim"], 6), ("method", C["dim"], 8),
           ("path", C["dim"], path_w), ("caller", C["dim"], None))
    row += 1
    if not req["recent"]:
        line(c, x + 2, row, inner, ("no requests in the last minute. If apps should be calling: is the tailnet/public route up?", C["dim"]))
    for r in req["recent"]:
        if row >= bottom:
            break
        fields(c, x + 2, row, right, (time.strftime("%H:%M:%S", time.localtime(r["t"])), C["dim"], 10),
               (str(r["status"]), http_colour(r["status"]), 6, r["status"] >= 400), (r["method"], C["bright"], 8),
               (r["path"], C["text"], path_w), (f"{r['client']} ({r['agent']})", C["accent"], None))
        row += 1


def requests_stats_panel(c, sampler, x, y, w, h):
    req = debug_of(sampler).get("requests")
    box(c, x, y, w, h, "traffic", C["svc"], "last minute")
    inner, row, bottom = w - 4, y + 1, y + h - 1
    if not req:
        return
    per_s = req["total"] / max(req["window_s"], 1)
    line(c, x + 2, row, inner, (f"{req['total']} requests · {per_s:.1f}/s", C["bright"], True))
    row += 1
    gh = min(4, max(bottom - row - 12, 0))
    if gh:
        rep = max(inner * (2 if c.rich else 1) // max(len(req["per_2s"]), 1), 1)     # stretch to the panel's width
        graph(c, x + 2, row, inner, gh, [v for v in req["per_2s"] for _ in range(rep)], max(max(req["per_2s"]), 4), "down")
        row += gh
    total = max(req["total"], 1)
    for label, colour in (("2xx", C["ok"]), ("3xx", C["accent"]), ("4xx", C["warn"]), ("5xx", C["bad"])):
        if row >= bottom:
            return
        n = req["classes"].get(label, 0)
        c.put(x + 2, row, label, colour, bold=n > 0 and label in ("4xx", "5xx"))
        bar(c, x + 6, row, max(inner - 12, 4), n / total, colour)
        c.put(x + w - 3 - len(str(n)), row, str(n), C["bright"])
        row += 1
    for title, key in (("top requests", "top_paths"), ("top callers", "top_clients")):
        if row + 2 >= bottom:
            break
        row += 1
        line(c, x + 2, row, inner, (title.upper(), C["dim"], True))
        row += 1
        for name, n in req[key]:
            if row >= bottom:
                break
            fields(c, x + 2, row, x + w - 2, (f"{n}x", C["bright"], 5), (name, C["text"], None))
            row += 1
    errors = req.get("errors") or []
    if errors and row + 2 < bottom:
        row += 1
        line(c, x + 2, row, inner, ("RECENT ERRORS (15 min)", C["bad"], True))
        row += 1
        for e in errors:
            if row >= bottom:
                break
            fields(c, x + 2, row, x + w - 2, (time.strftime("%H:%M:%S", time.localtime(e["t"])), C["dim"], 9),
                   (str(e["status"]), http_colour(e["status"]), 4, True),
                   (f"{e['method']} {e['path']}  {e['client']}", C["text"], None))
            row += 1


def logs_panel(c, sampler, x, y, w, h):
    system = debug_of(sampler).get("system")
    box(c, x, y, w, h, "system logs", C["warn"], "errors only")
    inner, row, bottom = w - 4, y + 1, y + h - 1
    if system is None:
        line(c, x + 2, row, inner, ("no log data (collector not updated yet)", C["dim"]))
        return
    failed = system.get("failed_units") or []
    line(c, x + 2, row, inner, ("FAILED UNITS  ", C["dim"], True), (", ".join(failed) if failed else "none", C["bad"] if failed else C["ok"]))
    row += 1
    line(c, x + 2, row, inner, (f"OOM kills since boot {system.get('oom_kills', 0)}   heat throttles {system.get('thermal_throttle', 0)}", C["dim"]))
    row += 2
    for title, key, colour in (("JOURNAL  (errors, last 6 h)", "journal", C["text"]), ("KERNEL  (errors since boot)", "kernel", C["dim"])):
        if row >= bottom:
            break
        line(c, x + 2, row, inner, (title, C["dim"], True))
        row += 1
        lines = system.get(key) or []
        if not lines:
            line(c, x + 2, row, inner, ("nothing logged", C["ok"]))
            row += 1
        for entry in lines[-max((bottom - row) // (2 if key == "journal" else 1), 1):]:
            if row >= bottom:
                break
            line(c, x + 2, row, inner, (entry, colour))
            row += 1
        row += 1
    dbg = debug_of(sampler)
    if dbg and row < bottom:
        line(c, x + 2, bottom - 1, inner, (f"collected {fmt_ago(time.time() - dbg.get('at', time.time()))} ago", C["faint"] if c.rich else C["off"]))


def service_errors_panel(c, sampler, x, y, w, h):
    errors = debug_of(sampler).get("service_errors")
    box(c, x, y, w, h, "service errors", C["bad"] if errors else C["ok"], "last 15 min")
    inner, row, bottom = w - 4, y + 1, y + h - 1
    if errors is None:
        line(c, x + 2, row, inner, ("no service log data (collector not updated yet)", C["dim"]))
        return
    if not errors:
        line(c, x + 2, row, inner, ("no service logged an error", C["ok"]))
        return
    for name, lines in sorted(errors.items()):
        if row >= bottom:
            break
        line(c, x + 2, row, inner, (name.upper(), C["bad"], True), (f"  sudo macserver logs {name}", C["faint"] if c.rich else C["off"]))
        row += 1
        for entry in lines:
            if row >= bottom:
                break
            line(c, x + 4, row, inner - 2, (entry, C["text"]))
            row += 1


def files_panel(c, sampler, x, y, w, h):
    dbg = debug_of(sampler)
    files = dbg.get("files")
    box(c, x, y, w, h, "where to look", C["svc"], "files and folders")
    inner, row, bottom = w - 4, y + 1, y + h - 1
    if files is None:
        line(c, x + 2, row, inner, ("no file data (collector not updated yet)", C["dim"]))
        return
    backups = dbg.get("backups") or {}
    for f in files:
        if row >= bottom - 2:
            break
        fresh = f["age_s"] < 600 and not f["path"].startswith("/run/")    # /run/... is rewritten every 2 s
        path_w = min(max(inner * 48 // 100, 18), 58)
        shown = f["path"] if len(f["path"]) < path_w else "…" + f["path"][-(path_w - 2):]    # keep the file name, and a gap
        fields(c, x + 2, row, x + w - 2, ("● " if fresh else "  ", C["warn"], 2),
               (shown, C["accent"] if fresh else C["svc"], path_w),
               (f"{fmt_ago(f['age_s'])} ago", C["bright"] if fresh else C["dim"], 10),
               (fmt_bytes(f["size"]) if f.get("size") is not None else "folder", C["dim"], 9), (f["note"], C["text"], None))
        row += 1
    if backups.get("count") is not None and bottom - 2 >= row:
        where = " on the USB drive" if backups.get("drive") is not None else ""
        text = (f"backups: {backups['count']} kept{where} · newest {fmt_ago(backups['newest_age_s'])} ago ({fmt_bytes(backups['newest_size'])})"
                if backups.get("count") else "backups: none yet · sudo macserver backup")
        line(c, x + 2, bottom - 2, inner, (text, C["warn"] if backup_problem(backups) or not backups.get("count")
                                           or backups.get("newest_age_s", 0) > 7 * 86400 else C["dim"]))
    line(c, x + 2, bottom - 1, inner, ("● changed in the last 10 minutes", C["warn"]))


def commands_panel(c, sampler, x, y, w, h):
    s = sampler.status or {}
    name = (s.get("tailscale", {}) or {}).get("name") or "macserver"
    box(c, x, y, w, h, "commands", C["accent"], "copy and run")
    inner, row, bottom = w - 4, y + 1, y + h - 1
    rows = [("log in", f"ssh admin@{name.split('.')[0]}   (from a computer on the tailnet)"),
            ("", "or press 2 on this screen"), ("health", "sudo macserver status"),
            ("follow", "sudo macserver logs <auth|rest|db|storage|functions>"), ("restart", "sudo macserver restart"),
            ("network", "sudo macserver doctor"), ("containers", "sudo docker ps -a"),
            ("one service", "sudo docker inspect <name> --format '{{json .State}}'"),
            ("unit logs", "sudo journalctl -u <unit> -n 100 --no-pager"), ("ports", "sudo ss -tlnp"),
            ("connections", "sudo ss -tnp state established"), ("firewall", "sudo nft list ruleset"),
            ("heat", "sensors"), ("disk", "df -h /  ;  sudo docker system df"),
            ("backup", "sudo macserver backup"), ("API offline", "sudo macserver public off")]
    for label, cmd in rows:
        if row >= bottom:
            break
        c.put(x + 2, row, f"{label:<13}", C["dim"])
        line(c, x + 15, row, inner - 13, (cmd, C["accent"]))
        row += 1


# --- the banner, the tabs and the pages -------------------------------------------------------------

def hazard_row(c, y, t):
    """A moving warning-tape stripe across the screen."""
    shift = int(t * 9)
    for x in range(c.w):
        k = (x - shift) % 8
        ch, level = ("█", 1.0) if k < 2 else ("▒", 0.7) if k < 4 else ("░", 0.45) if k < 5 else (" ", 0.0)
        if ch != " ":
            c.put(x, y, ch, mix(C["bg"], C["bad"], level))


def clock_text(sampler):
    if not getattr(sampler, "crit_since", None):
        return ""
    s = int(time.time() - sampler.crit_since)
    return f"T+{s // 3600:02d}:{s % 3600 // 60:02d}:{s % 60:02d}"


def banner(c, sampler, items):
    """The top of the incident screen. Returns the first free row."""
    w, h = c.w, c.h
    bad = [i for i in items if i["level"] == "bad"]
    t = time.time()
    hazard_row(c, 1, t)
    if h >= 30 and w >= 100:
        end = big_text(c, 2, 2, "CRITICAL", "cpu")
        blink = int(t * 2) % 2 == 0
        c.put(end + 3, 2, "●" if blink else " ", C["bad"], bold=True)
        c.put(end + 5, 2, f"{len(bad)} critical · {len(items) - len(bad)} warning", C["bright"], bold=True)
        line(c, end + 3, 3, w - end - 5, (bad[0]["title"] if bad else items[0]["title"] if items else "", C["bad"], True))
        since = next((when for when, lvl, _ in sampler.events.items if lvl == "bad"), None)
        line(c, end + 3, 4, w - end - 5, (clock_text(sampler), C["bright"], True),
             (f"   since {time.strftime('%H:%M:%S', time.localtime(since))}" if since else "", C["dim"]))
        line(c, end + 3, 6, w - end - 5, ("space: normal dashboard · n/b: page · p: hold the page · 2: log in and fix it", C["dim"]))
        hazard_row(c, 8, t + 1.5)
        return 9
    line(c, 2, 2, w - 4, ("▲ " + (bad[0]["title"] if bad else items[0]["title"] if items else "critical"), C["bad"], True),
         (f"   {clock_text(sampler)}", C["bright"]))
    return 3


def tab_strip(c, sampler, y):
    x = 1
    for i, name in enumerate(PAGES):
        label = f" {name.upper()} "
        if i == sampler.page:
            x = c.put(x, y, label, C["pill_fg"] if c.rich else C["bright"], C["bad"], bold=True) + 1
        else:
            x = c.put(x, y, label, C["dim"]) + 1
    left = ROTATE_S - (time.monotonic() - sampler.page_t)
    note = "page held (p)" if sampler.paused else f"next page in {max(int(left) + 1, 1)} s"
    c.put(c.w - len(note) - 2, y, note, C["dim"])
    return y + 1


def need_services(sampler, w):
    containers = (sampler.status or {}).get("containers", [])
    return 2 + max(2, len(containers) * 20 // max(w - 4, 1) + 1)


def need_thermal(sampler):
    return 8 + len(getattr(sampler, "thermal", []))


def need_procs(sampler):
    procs = getattr(sampler, "procs", None) or {"cpu": [], "mem": []}
    return 4 + 2 * max(min(len(procs["cpu"]), 6), min(len(procs["mem"]), 6), 1)


def need_listeners(sampler):
    rows = debug_of(sampler).get("listeners") or []
    return len(rows) + 3 + (2 if any(r["scope"] in ("all", "public", "lan") for r in rows) else 0)


def need_flows(sampler, direction):
    return max(len((debug_of(sampler).get("flows") or {}).get(direction, [])) + 3, 5)


def page_incident(c, sampler, items, x, y, w, h):
    if w >= 150:
        a, b, d = columns(x, w, [40, 30, 30])
        incident_list_panel(c, sampler, a[0], y, a[1], h, items)
        v, st, sv = fit(y, h, [14, len(connections(sampler)) + 2, need_services(sampler, b[1])], grow=0)
        vitals_panel(c, sampler, b[0], v[0], b[1], v[1])
        status_panel(c, sampler, b[0], st[0], b[1], st[1])
        services_panel_compact(c, sampler, b[0], sv[0], b[1], sv[1])
        th, pr, ev = fit(y, h, [need_thermal(sampler), need_procs(sampler), 8], grow=2)
        thermal_panel(c, sampler, d[0], th[0], d[1], th[1])
        procs_panel(c, sampler, d[0], pr[0], d[1], pr[1])
        events_panel(c, sampler, d[0], ev[0], d[1], ev[1])
    elif w >= 100:
        a, b = columns(x, w, [58, 42])
        incident_list_panel(c, sampler, a[0], y, a[1], h, items)
        v, t, e = stack(y, h, [3, 3, 3])
        vitals_panel(c, sampler, b[0], v[0], b[1], v[1])
        thermal_panel(c, sampler, b[0], t[0], b[1], t[1])
        events_panel(c, sampler, b[0], e[0], b[1], e[1])
    else:
        incident_list_panel(c, sampler, x, y, w, h, items)


def page_network(c, sampler, items, x, y, w, h):
    checks = len(connections(sampler)) + 8
    if w >= 150:
        a, b, d = columns(x, w, [27, 38, 35])
        l, n = fit(y, h, [need_listeners(sampler), checks], grow=1)
        listeners_panel(c, sampler, a[0], l[0], a[1], l[1])
        netchecks_panel(c, sampler, a[0], n[0], a[1], n[1])
        i, o, g = fit(y, h, [need_flows(sampler, "in"), need_flows(sampler, "out"), 12], grow=2)
        flows_panel(c, sampler, b[0], i[0], b[1], i[1], "in")
        flows_panel(c, sampler, b[0], o[0], b[1], o[1], "out")
        net_panel(c, sampler, b[0], g[0], b[1], g[1])
        requests_feed_panel(c, sampler, d[0], y, d[1], h)
    elif w >= 100:
        a, b = columns(x, w, [48, 52])
        l, n = fit(y, h, [need_listeners(sampler), checks], grow=1)
        listeners_panel(c, sampler, a[0], l[0], a[1], l[1])
        netchecks_panel(c, sampler, a[0], n[0], a[1], n[1])
        i, o, g = fit(y, h, [need_flows(sampler, "in"), need_flows(sampler, "out"), 10], grow=2)
        flows_panel(c, sampler, b[0], i[0], b[1], i[1], "in")
        flows_panel(c, sampler, b[0], o[0], b[1], o[1], "out")
        net_panel(c, sampler, b[0], g[0], b[1], g[1])
    else:
        l, i, o = stack(y, h, [3, 2, 2])
        listeners_panel(c, sampler, x, l[0], w, l[1])
        flows_panel(c, sampler, x, i[0], w, i[1], "in")
        flows_panel(c, sampler, x, o[0], w, o[1], "out")


def page_requests(c, sampler, items, x, y, w, h):
    if w >= 100:
        a, b = columns(x, w, [62, 38])
        requests_feed_panel(c, sampler, a[0], y, a[1], h)
        requests_stats_panel(c, sampler, b[0], y, b[1], h)
    else:
        f, s = stack(y, h, [3, 2])
        requests_feed_panel(c, sampler, x, f[0], w, f[1])
        requests_stats_panel(c, sampler, x, s[0], w, s[1])


def page_system(c, sampler, items, x, y, w, h):
    top, low = stack(y, h, [4, 5]) if h >= 24 else stack(y, h, [1, 2])
    cpu_panel(c, sampler, x, top[0], w, top[1])
    if w >= 150:
        a, b, d = columns(x, w, [30, 40, 30])
        mem_panel(c, sampler, a[0], low[0], a[1], low[1])
        thermal_panel(c, sampler, b[0], low[0], b[1], low[1])
        procs_panel(c, sampler, d[0], low[0], d[1], low[1])
    elif w >= 100:
        a, b = columns(x, w, [50, 50])
        thermal_panel(c, sampler, a[0], low[0], a[1], low[1])
        procs_panel(c, sampler, b[0], low[0], b[1], low[1])
    else:
        thermal_panel(c, sampler, x, low[0], w, low[1])


def page_doomsday(c, sampler, items, x, y, w, h):
    """Tron-style doomsday lockdown page - the main event when isolation triggers.
    Rich mode only: falls back to incident page in console mode."""
    # Console mode fallback: the Tron aesthetic needs box-drawing chars not in Lat15 font
    if not c.rich:
        page_incident(c, sampler, items, x, y, w, h)
        return
    dd = (sampler.status or {}).get("doomsday") or {}
    state = dd.get("state", "doomsday")
    score = dd.get("score", 0)
    trigger = dd.get("trigger", "auto")
    since = dd.get("since")
    reasons = dd.get("reasons", [])
    
    # Time since lockdown
    if since and isinstance(since, (int, float)):
        try:
            elapsed = int(time.time() - since)
        except (TypeError, ValueError, OverflowError):
            elapsed = 0
    else:
        elapsed = 0
    
    # Tron color scheme - cyan on dark
    TRON_CYAN = C["conn"] if c.rich else C["text"]
    TRON_BRIGHT = C["bright"] if c.rich else C["text"]
    TRON_DIM = C["dim"] if c.rich else C["off"]
    TRON_WARN = C["warn"] if c.rich else C["warn"]
    TRON_BAD = C["bad"] if c.rich else C["bad"]
    TRON_BG = C["bg"] if c.rich else None
    
    # Fill background
    if c.rich:
        c.fill(x, y, w, h, TRON_BG)
    
    # Top border - Tron grid line
    grid_colour = TRON_DIM
    for i in range(w):
        c.put(x + i, y, "─", grid_colour)
        c.put(x + i, y + h - 1, "─", grid_colour)
    for i in range(h):
        c.put(x, y + i, "│", grid_colour)
        c.put(x + w - 1, y + i, "│", grid_colour)
    c.put(x, y, "╭", grid_colour)
    c.put(x + w - 1, y, "╮", grid_colour)
    c.put(x, y + h - 1, "╰", grid_colour)
    c.put(x + w - 1, y + h - 1, "╯", grid_colour)
    
    inner_x = x + 2
    inner_y = y + 1
    inner_w = w - 4
    inner_h = h - 2
    
    # ============================================================
    # HEADER: SYSTEM ISOLATION PROTOCOL
    # ============================================================
    header_y = inner_y
    c.put(inner_x, header_y, "▰" * min(inner_w, 60), TRON_CYAN)
    header_y += 1
    c.put(inner_x, header_y, "SYSTEM ISOLATION PROTOCOL  //  DOOMSDAY LOCKDOWN ACTIVE", TRON_BRIGHT, bold=True)
    header_y += 1
    c.put(inner_x, header_y, "▰" * min(inner_w, 60), TRON_CYAN)
    header_y += 2
    
    # State badge
    badge_text = "  STATE: DOOMSDAY LOCKDOWN  "
    if state == "debug":
        badge_text = "  STATE: DEBUG MODE (ISOLATED)  "
    badge_x = inner_x + (inner_w - len(badge_text)) // 2
    c.put(badge_x, header_y, badge_text, TRON_BG if c.rich else TRON_DIM, TRON_BAD, bold=True)
    header_y += 2
    
    # ============================================================
    # THREAT METRICS
    # ============================================================
    metrics_y = header_y
    c.put(inner_x, metrics_y, "┌─ THREAT ASSESSMENT ────────────────────────────────────┐", TRON_CYAN)
    metrics_y += 1
    
    # Score meter
    threshold = int(dd.get("threshold") or 6)
    score_pct = min(score / max(threshold * 2, score, 1), 1.0)   # full bar at twice the isolation threshold
    score_bar_w = min(inner_w - 4, 40)
    filled = round(score_pct * score_bar_w)
    bar_str = "█" * filled + "░" * (score_bar_w - filled)
    c.put(inner_x + 2, metrics_y, f"ANOMALY SCORE  │{bar_str}│  {score}  (isolates at {threshold} or more)", TRON_BRIGHT)
    metrics_y += 1

    # Trigger
    c.put(inner_x + 2, metrics_y, f"TRIGGER SOURCE  │  {trigger.upper():<12}  │", TRON_DIM)
    metrics_y += 1
    
    # Elapsed time
    mins, secs = divmod(elapsed, 60)
    hours, mins = divmod(mins, 60)
    time_str = f"{hours:02d}:{mins:02d}:{secs:02d}" if hours else f"{mins:02d}:{secs:02d}"
    c.put(inner_x + 2, metrics_y, f"LOCKDOWN TIMER  │  {time_str}  │", TRON_WARN if elapsed < 300 else TRON_BRIGHT)
    metrics_y += 1
    
    c.put(inner_x, metrics_y, "└────────────────────────────────────────────────────────┘", TRON_CYAN)
    metrics_y += 2
    
    # ============================================================
    # CONTAINMENT STATUS GRID
    # ============================================================
    grid_y = metrics_y
    c.put(inner_x, grid_y, "┌─ CONTAINMENT GRID ─────────────────────────────────────┐", TRON_CYAN)
    grid_y += 1
    
    # What the Mac reports now, not what lockdown is supposed to have done.
    status_now = sampler.status or {}
    ts_state = (status_now.get("tailscale") or {}).get("state", "unknown")
    running = [i for i in items if i.get("state") == "running"]
    tunnel_up = bool((status_now.get("public_route") or {}).get("up"))
    ssh_up = dd.get("ssh", "missing") not in ("missing", "")
    containment = [
        ("NETWORK", "TAILSCALE", ts_state.upper(), TRON_BAD if ts_state == "Running" else TRON_WARN),
        ("NETWORK", "PUBLIC TUNNEL", "STILL RUNNING" if tunnel_up else "STOPPED", TRON_BAD if tunnel_up else TRON_WARN),
        ("CONTAINERS", "RUNNING", f"{len(running)} of {len(items)}", TRON_BAD if running else TRON_WARN),
        ("FIREWALL", "POLICY", "DENY ALL (LAN SSH ALLOWED)" if ssh_up else "DENY ALL (NO SSH SERVER)", TRON_WARN),
    ]
    
    for cat, item, status, colour in containment:
        if grid_y >= inner_y + inner_h - 10:
            break
        c.put(inner_x + 2, grid_y, f"[{cat:<10}]  {item:<18}  ▶  {status}", colour)
        grid_y += 1
    
    c.put(inner_x, grid_y, "└────────────────────────────────────────────────────────┘", TRON_CYAN)
    grid_y += 2
    
    # ============================================================
    # DETECTION FINDINGS
    # ============================================================
    findings_y = grid_y
    c.put(inner_x, findings_y, "┌─ DETECTION FINDINGS ────────────────────────────────────┐", TRON_CYAN)
    findings_y += 1
    
    finding_labels = {
        "inbound lan connection": "LAN INBOUND ANOMALY",
        "unexpected outbound": "SUSPICIOUS OUTBOUND",
        "listener on": "ROGUE LISTENER",
        "5xx": "API ERROR SPIKE",
        "unknown root process": "UNKNOWN ROOT PROCESS",
    }
    
    for reason in reasons[:6]:
        if findings_y >= inner_y + inner_h - 5:
            break
        label = "ANOMALY DETECTED"
        for key, val in finding_labels.items():
            if key in reason.lower():
                label = val
                break
        # Truncate reason
        short_reason = reason[:inner_w - 30]
        c.put(inner_x + 2, findings_y, f"  ▶  {label:<22}  │  {short_reason}", TRON_WARN)
        findings_y += 1
    
    c.put(inner_x, findings_y, "└────────────────────────────────────────────────────────┘", TRON_CYAN)
    findings_y += 2
    
    # ============================================================
    # EVENT LOG (last 8 entries)
    # ============================================================
    log_y = findings_y
    c.put(inner_x, log_y, "┌─ EVENT LOG ──────────────────────────────────────────────┐", TRON_CYAN)
    log_y += 1
    
    # What was recorded when the Mac isolated itself (host/macserver-doomsday.sh), nothing else.
    when = time.strftime("%H:%M:%S", time.localtime(since)) if isinstance(since, (int, float)) else "--:--:--"
    notified = str(dd.get("notified") or "")
    log_entries = [
        (when, "ISOLATED", "by the owner" if trigger == "manual" else "by detection"),
        (when, "ALERT EMAIL", "handed to GitHub (HTTP 204)" if notified == "204" else
         f"NOT accepted (HTTP {notified})" if notified else "NOT sent: no GitHub token (macserver doomsday setup-gh)"),
        (when, "CONTAINMENT", "Tailscale, admin page, containers and public tunnel stopped; lockdown firewall applied"),
    ]
    
    for timestamp, event, detail in log_entries[-8:]:
        if log_y >= inner_y + inner_h - 3:
            break
        c.put(inner_x + 2, log_y, f"  [{timestamp}]  {event:<20}  │  {detail}", TRON_DIM)
        log_y += 1
    
    c.put(inner_x, log_y, "└────────────────────────────────────────────────────────┘", TRON_CYAN)
    log_y += 2
    
    # ============================================================
    # EMERGENCY ACCESS & RECOVERY
    # ============================================================
    if inner_y + inner_h - log_y >= 8:
        recovery_y = log_y
        c.put(inner_x, recovery_y, "┌─ EMERGENCY ACCESS ───────────────────────────────────────┐", TRON_CYAN)
        recovery_y += 1
        if ssh_up:
            c.put(inner_x + 2, recovery_y, "  SSH   │  LAN ONLY  │  192.168.x.x:22  │  TOTP REQUIRED", TRON_BRIGHT)
        else:
            c.put(inner_x + 2, recovery_y, "  SSH   │  NOT INSTALLED on this Mac: use the keyboard and screen", TRON_DIM)
        recovery_y += 1
        c.put(inner_x + 2, recovery_y, "  TTY   │  CONSOLE   │  DIRECT ACCESS   │  TOTP REQUIRED", TRON_BRIGHT)
        recovery_y += 1
        c.put(inner_x, recovery_y, "└────────────────────────────────────────────────────────┘", TRON_CYAN)
        recovery_y += 1
        
        c.put(inner_x, recovery_y, "┌─ RECOVERY PROTOCOL ──────────────────────────────────────┐", TRON_CYAN)
        recovery_y += 1
        c.put(inner_x + 2, recovery_y, "  1. STATUS  →  sudo macserver doomsday status", TRON_BRIGHT)
        recovery_y += 1
        c.put(inner_x + 2, recovery_y, "  2. DEBUG   →  sudo macserver doomsday unlock --code XXXXXX", TRON_WARN)
        recovery_y += 1
        c.put(inner_x + 2, recovery_y, "  3. RESTORE →  sudo macserver doomsday restore --code XXXXXX", TRON_BRIGHT)
        recovery_y += 1
        c.put(inner_x + 2, recovery_y, "  TOTP: Google Authenticator  •  6-digit  •  30s window", TRON_DIM)
        recovery_y += 1
        c.put(inner_x, recovery_y, "└────────────────────────────────────────────────────────┘", TRON_CYAN)
    
    # Bottom line
    c.put(inner_x, inner_y + inner_h - 2, "▰" * min(inner_w, 60), TRON_CYAN)
    c.put(inner_x + (inner_w - 14) // 2, inner_y + inner_h - 1, "END OF LINE", TRON_BRIGHT, bold=True)


def page_logs(c, sampler, items, x, y, w, h):
    if w >= 150:
        a, b, d = columns(x, w, [40, 36, 24])
        l, e = fit(y, h, [14, 8], grow=1)
        logs_panel(c, sampler, a[0], l[0], a[1], l[1])
        service_errors_panel(c, sampler, a[0], e[0], a[1], e[1])
        files_panel(c, sampler, b[0], y, b[1], h)
        commands_panel(c, sampler, d[0], y, d[1], h)
    elif w >= 100:
        a, b = columns(x, w, [50, 50])
        l, e = fit(y, h, [14, 8], grow=1)
        logs_panel(c, sampler, a[0], l[0], a[1], l[1])
        service_errors_panel(c, sampler, a[0], e[0], a[1], e[1])
        f, m = stack(y, h, [3, 2])
        files_panel(c, sampler, b[0], f[0], b[1], f[1])
        commands_panel(c, sampler, b[0], m[0], b[1], m[1])
    else:
        l, e, f = stack(y, h, [2, 2, 2])
        logs_panel(c, sampler, x, l[0], w, l[1])
        service_errors_panel(c, sampler, x, e[0], w, e[1])
        files_panel(c, sampler, x, f[0], w, f[1])


PAGE_DRAW = {"doomsday": page_doomsday, "incident": page_incident, "network": page_network, "requests": page_requests,
             "system": page_system, "logs": page_logs}


def incident_view(c, sampler, problems, kiosk):
    """The red debug screen: the banner, a tab strip, and the current page."""
    items = incidents(sampler)
    y = banner(c, sampler, items)
    sampler.advance_page(time.monotonic(), len(items))
    y = tab_strip(c, sampler, y)
    PAGE_DRAW[PAGES[sampler.page % len(PAGES)]](c, sampler, items, 0, y, c.w, c.h - 1 - y)


def setup_panel(c, sampler, x, y, w, h):
    running = sampler.firstboot == "activating"
    box(c, x, y, w, h, "setup", C["warn"])
    if running:
        lines = [("Setup is running. It continues by itself (about 15 minutes).", C["warn"], True),
                 ("If a QR code appears, scan it with your phone to add this Mac to Tailscale.", C["text"], False)]
    else:
        lines = [("Setup has not finished.", C["warn"], True),
                 ("Press 2 to log in, then run:  sudo /opt/macserver-src/install.sh", C["text"], False),
                 ("It continues where it stopped and tells you what it needs.", C["dim"], False)]
    for i, (text, colour, bold) in enumerate(lines):
        c.put(x + 2, y + 1 + i, clip(text, w - 4), colour, bold=bold)


def bottom_bar(c, sampler, kiosk, level="ok"):
    w, y = c.w, c.h - 1
    s = sampler.status or {}
    bg = C["panel"] if c.rich else None
    c.fill(0, y, w, 1, bg)
    name = (s.get("tailscale", {}) or {}).get("name")
    links = ([("admin", f"https://{name}/"), ("studio", f"https://{name}:{STUDIO_PORT}")] if name else [])
    links.append(("guide", DOCS))
    hint = "press 2 to log in · back here: Ctrl+Option+1" if kiosk else "q quit · sudo macserver help"
    hint = "n/b page · p hold · " + hint
    if level == "critical":
        hint = "space: incident / overview · " + hint
    x = 1
    for label, url in links:
        if x + len(label) + len(url) + 4 > w - len(hint) - 3:
            break
        x = c.put(x, y, label + " ", C["dim"], bg)
        x = c.put(x, y, url, C["accent"], bg) + 3
    c.put(w - len(hint) - 1, y, hint, C["dim"], bg)


# --- the home pages: the screen's normal state ----------------------------------------------------
# Five pages that turn by themselves (the overview stays longest): OVERVIEW, HARDWARE, NETWORK,
# SERVICES, SYSTEM. n / b turn the page, p holds it. Every panel reads status.json's `system` section
# (admin/collect_status.py) and draws only what it can read, so an older collector or a missing
# source leaves a gap, never an error. Only characters from GLYPHS and ASCII are used (console mode).

HOME_PAGES = ("overview", "hardware", "network", "services", "system")
HOME_DWELL_S = {"overview": 30.0, "hardware": 15.0, "network": 15.0, "services": 15.0, "system": 15.0}


def sysdata(sampler):
    return (sampler.status or {}).get("system") or {}


def livedata(sampler):
    live = (sampler.status or {}).get("live") or {}
    return {} if live.get("error") else live


def level_colour(frac, warn=0.85, bad=0.95):
    """Colour of a how-full bar: calm until it matters."""
    return C["bad"] if frac >= bad else C["warn"] if frac >= warn else C["ok"]


def fmt_since(epoch, now=None):
    """Time since an epoch moment: 12s, 5m, 3h, 4d."""
    if not epoch:
        return "never"
    s = max(int((now or time.time()) - epoch), 0)
    return f"{s}s" if s < 90 else f"{s // 60}m" if s < 5400 else f"{s // 3600}h" if s < 129600 else f"{s // 86400}d"


def per_cent(part, whole):
    return 100.0 * part / whole if whole else 0.0


def stacked(c, x, y, w, parts):
    """One bar split into coloured shares of the whole: parts = [(fraction, colour)]."""
    edges, acc = [], 0.0
    for frac, colour in parts:
        acc += max(frac, 0.0)
        edges.append((round(min(acc, 1.0) * w), colour))
    for i in range(w):
        colour = next((col for edge, col in edges if i < edge), None)
        c.put(x + i, y, "■" if (c.rich or colour) else "·", colour or (C["faint"] if c.rich else C["off"]))


def table(c, x, y, w, h, cols, rows, gap=2):
    """Header row, then as many rows as fit. cols = [(title, width or None, "l"|"r")]: the one column
    with width None takes what is left. rows = [[(text, colour[, bold]) per column]]. Returns rows drawn."""
    if h < 1 or w < 4:
        return 0
    fixed = sum(cw for _, cw, _ in cols if cw) + gap * (len(cols) - 1)
    widths = [cw if cw else max(w - fixed, 0) for _, cw, _ in cols]
    xx = x
    for (title, _, align), cw in zip(cols, widths):
        if cw <= 0 or xx >= x + w:
            break
        text = clip(title, cw)
        c.put(xx, y, text.rjust(cw) if align == "r" else text, C["dim"], bold=True)
        xx += cw + gap
    drawn = 0
    for row in rows[: max(h - 1, 0)]:
        xx = x
        for (_, _, align), cw, cell in zip(cols, widths, row):
            if cw <= 0 or xx >= x + w:
                break
            text = clip(str(cell[0]), min(cw, x + w - xx))
            c.put(xx, y + 1 + drawn, text.rjust(cw) if align == "r" else text, cell[1], bold=len(cell) > 2 and cell[2])
            xx += cw + gap
        drawn += 1
    return drawn


# --- the KPI strip: the numbers worth seeing from across the room ---------------------------------------------

def kpi_cards(sampler):
    """The headline cards in the order they are kept when the screen is narrow."""
    s, now, hist = sampler.status or {}, sampler.now, sampler.hist
    sd = sysdata(sampler)
    cpu_info_ = sd.get("cpu") or {}
    la = now.get("load") or [0.0, 0.0, 0.0]
    psi = (((cpu_info_.get("pressure") or {}).get("cpu") or {}).get("some") or {}).get("avg10")
    threads = len(now.get("cores") or []) or cpu_info_.get("threads") or 0
    ghz = (now.get("mhz") or 0) / 1000
    cards = [{"title": "cpu", "colour": C["cpu"], "value": f"{now['cpu']:.0f}%", "series": hist["cpu"], "top": 100, "grad": "cpu",
              "sub": f"{threads} threads" + (f" · {ghz:.1f} GHz" if ghz else ""),
              "foot": f"load {la[0]:.2f}" + (f" · stall {psi:.0f}%" if psi is not None else "")}]
    m = now["mem"]
    used_pct = per_cent(m["used"], m["total"])
    cards.append({"title": "memory", "colour": C["mem"], "value": f"{used_pct:.0f}%", "series": hist["mem"], "top": 100, "grad": "mem",
                  "vcolour": level_colour(1 - per_cent(m.get("available", m["total"] - m["used"]), m["total"]) / 100, 0.9, 0.95)
                  if m["total"] else None,
                  "sub": f"{fmt_bytes(m['used'])} of {fmt_bytes(m['total'])}",
                  "foot": f"free {fmt_bytes(m.get('available'))} · swap {fmt_bytes(m.get('swap_used', 0))}"})
    rx_top = nice_top(list(hist["rx"])[-120:], 100_000)
    iface = sampler.iface or "offline"
    sig = now.get("signal")
    cards.append({"title": "network", "colour": C["net"], "value": f"▼ {fmt_bytes(now['rx'], True)}", "series": hist["rx"], "top": rx_top,
                  "grad": "down", "sub": f"▲ {fmt_bytes(now['tx'], True)}",
                  "foot": f"{iface}" + (f" · {sig} dBm" if sig is not None else "")})
    used, total = now.get("disk", (0, 0))
    cards.append({"title": "disk /", "colour": C["svc"], "value": f"{per_cent(used, total):.0f}%", "series": hist["wr"],
                  "top": nice_top(list(hist["wr"])[-120:], 1_000_000), "grad": "disk",
                  "vcolour": level_colour(used / total) if total else None, "sub": f"{fmt_bytes(used)} of {fmt_bytes(total)}",
                  "foot": f"R {fmt_bytes(now['rd'], True)} · W {fmt_bytes(now['wr'], True)}"})
    temp = now.get("temp")
    fans = now.get("fans") or [f["rpm"] for f in ((sd.get("sensors") or {}).get("fans") or [])]
    fi = sampler.fans or {}
    stalled = cooling_fault(temp, fans)
    cards.append({"title": "temperature", "colour": C["accent"], "value": "?" if temp is None else f"{temp:.0f}°C",
                  "vcolour": C["bad"] if stalled else None if temp is None else grad("temp", temp_pos(temp)),
                  "series": hist["temp"], "top": 100, "grad": "temp", "sub": f"fan {fans[0]} rpm" if fans else "no fan reading",
                  "foot": "fan is not turning!" if stalled else f"fan target {fi['target_pct']:.0f}%" if fi.get("target_pct") else ""})
    containers = s.get("containers", []) or []
    up = sum(1 for i in containers if i.get("state") == "running")
    db = sd.get("db") or {}
    cards.append({"title": "services", "colour": C["svc"], "value": f"{up}/{len(containers)}" if containers else "-",
                  "vcolour": C["bad"] if containers and up < len(containers) else None, "series": sampler.status_hist["svc_cpu"],
                  "top": nice_top(list(sampler.status_hist["svc_cpu"]), 50),
                  "grad": "cpu", "sub": "running",
                  "foot": (f"db {db['connections']}/{db['max_connections']} conn · {db['tps']:.1f} tps" if db.get("connections") is not None
                           and db.get("max_connections") else "load " + f"{sum(i.get('cpu') or 0 for i in containers):.0f}% cpu")})
    bat = sd.get("power") or {}
    pct = bat.get("percent")
    if pct is None:
        pct, stat, watts = now.get("battery") or (None, None, None)
        bat = {"status": stat}
    if pct is not None:
        cards.append({"title": "power", "colour": C["conn"], "value": f"{pct}%", "frac": pct / 100, "bcolour": level_colour(1 - pct / 100, 0.8, 0.9),
                      "sub": f"{bat.get('status') or ''}" + (" · AC" if bat.get("ac_online") else ""),
                      "foot": (f"health {bat['health_pct']:.0f}% · {bat['cycles']} cycles" if bat.get("health_pct") and bat.get("cycles") is not None
                               else "")})
    cards.append({"title": "load", "colour": C["accent"], "value": f"{la[0]:.2f}", "series": hist["load"], "top": max(float(threads or 4) * 2, 4.0),
                  "grad": "cpu", "sub": f"{la[1]:.2f} · {la[2]:.2f}",
                  "foot": f"{now.get('running', 0)} running · {now.get('tasks', 0)} tasks"})
    return cards


def kpi_card(c, x, y, w, h, card):
    box(c, x, y, w, h, card["title"], card["colour"])
    xx = c.put(x + 2, y + 1, card["value"], card.get("vcolour") or C["bright"], bold=True)
    line(c, xx + 2, y + 1, x + w - 2 - (xx + 2), (card["sub"], C["dim"]))
    gh = h - 4
    if gh >= 1 and card.get("series") is not None:
        graph(c, x + 2, y + 2, w - 4, gh, card["series"], card.get("top") or 100, card.get("grad", "cpu"))
    elif gh >= 1 and card.get("frac") is not None:
        bar(c, x + 2, y + 2, w - 4, card["frac"], card.get("bcolour", C["accent"]))
    line(c, x + 2, y + h - 2, w - 4, (card.get("foot", ""), C["text"]))


def kpi_strip(c, sampler, x, y, w, h):
    cards = kpi_cards(sampler)
    n = max(1, min(len(cards), w // 27))
    for card, (cx, cw) in zip(cards[:n], columns(x, w, [1] * n)):
        kpi_card(c, cx, y, cw, h, card)


# --- panels ----------------------------------------------------------------------------------------------------

def cpu_detail_panel(c, sampler, x, y, w, h):
    if tiny(w, h):
        return
    now, sd, ld = sampler.now, sysdata(sampler), livedata(sampler)
    info = sd.get("cpu") or {}
    cores = now.get("cores") or []
    threads = info.get("threads") or len(cores)
    box(c, x, y, w, h, "cpu", C["cpu"], clip(f"{sampler.model} · {info.get('cores', '?')} cores / {threads} threads", max(w // 2 + 8, 12)))
    ix, row, bottom, iw = x + 2, y + 1, y + h - 1, w - 4
    mw = max(min(iw - 26, 40), 6)
    xx = c.put(ix, row, "Total ", C["dim"])
    bar(c, xx, row, mw, now["cpu"] / 100, C["cpu"])
    xx = c.put(xx + mw + 1, row, f"{now['cpu']:3.0f}%", C["bright"], bold=True)
    freq = now.get("mhz")
    if freq:
        line(c, xx + 2, row, ix + iw - xx - 2, (f"{freq / 1000:.2f} GHz", C["text"]))
    row += 1
    per_row = 2 if iw >= 64 else 1
    cw = iw // per_row
    core_temps = now.get("core_temps") or {}
    for i in range(0, len(cores), per_row):
        if row >= bottom:
            return
        for j in range(per_row):
            k = i + j
            if k >= len(cores):
                break
            cx = ix + j * cw
            xx = c.put(cx, row, f"C{k:<2}", C["dim"])
            m = max(cw - 17, 4)
            bar(c, xx, row, m, cores[k] / 100, C["cpu"])
            xx = c.put(xx + m + 1, row, f"{cores[k]:3.0f}%", C["text"])
            t = core_temps.get(sampler.core_of.get(k, k))
            if t is not None:
                c.put(xx + 1, row, f"{t:3.0f}°C", grad("temp", temp_pos(t)))
        row += 1
    times = (ld.get("cpu") or {}).get("times") or {}
    if times and row + 2 < bottom:
        row += 1
        kinds = (("user", C["accent"]), ("system", C["conn"]), ("iowait", C["net"]), ("irq", C["mem"]), ("steal", C["bad"]))
        stacked(c, ix, row, iw, [(times.get(k, 0) / 100, col) for k, col in kinds])
        row += 1
        xx = ix
        for k, col in kinds:
            xx = c.put(xx, row, "■ ", col)
            xx = c.put(xx, row, f"{k} {times.get(k, 0):.0f}%   ", C["text"])
        row += 1
    la = now.get("load") or [0.0, 0.0, 0.0]
    if row < bottom - 1:
        row += 1
        line(c, ix, row, iw, ("load ", C["dim"]), (f"{la[0]:.2f}  {la[1]:.2f}  {la[2]:.2f}", C["bright"]),
             ("  (1, 5, 15 min)   ", C["dim"]), (f"{now.get('running', 0)} running / {now.get('tasks', 0)} tasks", C["text"]))
        row += 1
    pressure = info.get("pressure") or {}
    if pressure and row + 3 < bottom:
        row += 1
        line(c, ix, row, iw, (f"{'STALL':<8}", C["dim"], True), (f"{'10 s':>8}{'60 s':>8}{'5 min':>8}", C["dim"]),
             ("   share of time work waited for it", C["faint"] if c.rich else C["off"]))
        row += 1
        for name, warn in (("cpu", 60), ("memory", 5), ("io", 10)):
            some = (pressure.get(name) or {}).get("some") or {}
            if not some:
                continue
            vals = [some.get(k, 0.0) for k in ("avg10", "avg60", "avg300")]
            line(c, ix, row, iw, (f"{name:<8}", C["dim"]), ("".join(f"{v:7.1f}%" for v in vals), C["warn"] if vals[0] >= warn else C["text"]))
            row += 1
    rates = ld.get("rates") or {}
    if rates and row < bottom:
        row += 1 if row < bottom - 1 else 0
        line(c, ix, row, iw, (f"switches {fmt_count(rates.get('ctxt', 0))}/s   interrupts {fmt_count(rates.get('intr', 0))}/s   "
                              f"forks {fmt_count(rates.get('forks', 0))}/s", C["dim"]))
        row += 1
    if info and row < bottom:
        turbo = {True: "turbo on", False: "turbo off"}.get(info.get("turbo"), "")
        line(c, ix, row, iw, (f"{info.get('governor') or '?'} governor · {info.get('min_mhz', 0):.0f}-{info.get('max_mhz', 0):.0f} MHz · {turbo}", C["dim"]))
        row += 1
    thr = info.get("throttle") or {}
    if any(thr.values()) and row < bottom:
        line(c, ix, row, iw, (f"heat slow-downs since boot: core {fmt_count(thr.get('core', 0))}, package {fmt_count(thr.get('package', 0))} (normal on this Mac)", C["faint"] if c.rich else C["off"]))
        row += 1
    room = bottom - row
    if room >= 4:                                   # whatever room is left shows the last minutes
        graph(c, x + 1, row + 1, w - 2, room - 1, sampler.hist["cpu"], 100, "cpu")
        secs = int((w - 2) * (2 if c.rich else 1) * SAMPLE_S)
        c.put(x + 2, row, f"last {secs // 60} min" if secs >= 120 else f"last {secs} s", C["dim"])


def memory_detail_panel(c, sampler, x, y, w, h):
    if tiny(w, h):
        return
    sd, ld = sysdata(sampler), livedata(sampler)
    m = {**sampler.now["mem"], **(sd.get("memory") or {})}
    total = m["total"]
    box(c, x, y, w, h, "memory", C["mem"], f"{fmt_bytes(total)} total")
    ix, row, bottom, iw = x + 2, y + 1, y + h - 1, w - 4
    mw = max(min(iw - 24, 36), 4)
    shown = [("Used", m["used"], level_colour(1 - per_cent(m.get("available", 0), total) / 100, 0.9, 0.95)),
             ("Available", m.get("available", 0), C["svc"]),
             ("Cached", m.get("cached", 0) + m.get("buffers", 0), C["svc"]),
             ("Kernel slab", m.get("slab", 0), C["net"]), ("Shared", m.get("shmem", 0), C["net"]), ("Free", m.get("free", 0), C["off"])]
    for label, value, colour in shown:
        if row >= bottom or (label in ("Kernel slab", "Shared", "Free") and not value):
            continue
        xx = c.put(ix, row, f"{label:<12}", C["dim"])
        xx = c.put(xx, row, f"{fmt_bytes(value):>9} ", C["bright"])
        bar(c, xx, row, mw, value / total if total else 0, colour)
        c.put(xx + mw + 1, row, f"{per_cent(value, total):3.0f}%", C["text"])
        row += 1
    swap_total, swap_used = m.get("swap_total", 0), m.get("swap_used", 0)
    if swap_total and row < bottom:
        xx = c.put(ix, row, f"{'Swap':<12}", C["dim"])
        xx = c.put(xx, row, f"{fmt_bytes(swap_used):>9} ", C["bright"])
        bar(c, xx, row, mw, swap_used / swap_total, level_colour(swap_used / swap_total, 0.6, 0.85))
        c.put(xx + mw + 1, row, f"{per_cent(swap_used, swap_total):3.0f}%", C["text"])
        row += 1
    rates = ld.get("rates") or {}
    notes = []
    if sd.get("memory"):
        notes.append(f"dirty {fmt_bytes(m.get('dirty', 0))} · writeback {fmt_bytes(m.get('writeback', 0))} · page tables {fmt_bytes(m.get('page_tables', 0))}")
        if m.get("commit_limit"):
            notes.append(f"committed {fmt_bytes(m.get('committed', 0))} of a {fmt_bytes(m['commit_limit'])} limit "
                         f"({per_cent(m.get('committed', 0), m['commit_limit']):.0f}%): promised, not used")
    if rates:
        notes.append(f"swap in {fmt_bytes(rates.get('swap_in', 0), True)} · out {fmt_bytes(rates.get('swap_out', 0), True)} · "
                     f"hard page faults {rates.get('major_faults', 0)}/s")
    if (sd.get("swap") or []) and len(sd["swap"]) > 0:
        notes.append("swap on " + ", ".join(f"{d['name']} ({d['kind']})" for d in sd["swap"]))
    if notes and row < bottom - 1:
        row += 1
    for note in notes:
        if row >= bottom:
            break
        line(c, ix, row, iw, (note, C["dim"]))
        row += 1
    room = bottom - row
    if room >= 4:
        graph(c, x + 1, row, w - 2, room, sampler.hist["mem"], 100, "mem")


def storage_panel(c, sampler, x, y, w, h):
    if tiny(w, h):
        return
    sd, ld = sysdata(sampler), livedata(sampler)
    store = sd.get("storage") or {}
    systems = store.get("filesystems") or []
    box(c, x, y, w, h, "storage", C["svc"], f"{len(systems)} filesystem(s)" if systems else "")
    ix, row, bottom, iw = x + 2, y + 1, y + h - 1, w - 4
    if not systems:
        used, total = sampler.now.get("disk", (0, 0))
        systems = [{"mount": "/", "used": used, "total": total, "avail": total - used, "fstype": "", "inodes": 0, "inodes_used": 0, "readonly": False}]
    mw = max(min(iw - 58, 20), 6) if iw >= 70 else 6
    for fs in systems:
        if row >= bottom:
            return
        frac = fs["used"] / fs["total"] if fs.get("total") else 0
        inodes = per_cent(fs.get("inodes_used", 0), fs.get("inodes", 0))
        xx = c.put(ix, row, f"{clip(fs['mount'], 10):<10}", C["bright"], bold=True)
        bar(c, xx, row, mw, frac, level_colour(frac))
        xx = c.put(xx + mw + 1, row, f"{frac * 100:3.0f}%", C["text"])
        text = f"  {fmt_bytes(fs['used'])} / {fmt_bytes(fs['total'])}   free {fmt_bytes(fs.get('avail'))}   {fs.get('fstype', '')}"
        if fs.get("inodes") and iw >= 96:
            text += f"   inodes {inodes:.0f}%"
        xx = line(c, xx, row, ix + iw - xx, (text, C["dim"]))
        if fs.get("readonly"):
            line(c, xx, row, ix + iw - xx, ("  READ-ONLY", C["bad"], True))
        row += 1
    disks = store.get("disks") or []
    temps = {(t["chip"], t["label"]): t["temp_c"] for t in (sd.get("sensors") or {}).get("temps") or []}
    for d in disks:
        if row + 1 >= bottom:
            break
        row += 1
        io = (ld.get("disks") or {}).get(d["name"]) or {}
        line(c, ix, row, iw, (f"{d['name']}  ", C["bright"], True), (f"{d.get('model') or ''}  {fmt_bytes(d.get('size'))}  ", C["text"]),
             (("SSD" if d.get("ssd") else "disk") + (f" · {d['state']}" if d.get("state") else "") + (f" · firmware {d['firmware']}" if d.get("firmware") else ""), C["dim"]),
             ((f" · {temps[('nvme', 'Composite')]:.0f}°C" if ("nvme", "Composite") in temps else ""), C["dim"]))
        row += 1
        if io:
            line(c, ix, row, iw, ("read ", C["dim"]), (f"{fmt_bytes(io['read'], True):>10}", C["bright"]), ("   write ", C["dim"]),
                 (f"{fmt_bytes(io['write'], True):>10}", C["bright"]), (f"   {io['riops']} + {io['wiops']} operations/s   busy ", C["dim"]),
                 (f"{io['busy']:.0f}%", C["warn"] if io["busy"] >= 85 else C["bright"]))
            row += 1
    dk = store.get("docker") or {}
    if dk and row + 1 < bottom:
        row += 1
        parts = []
        for key, label in (("images", "images"), ("volumes", "volumes"), ("build_cache", "build cache")):
            d = dk.get(key)
            if d:
                parts.append(f"{label} {fmt_bytes(d['size'])}" + (f" ({fmt_bytes(d['reclaimable'])} reclaimable)" if d.get("reclaimable") else ""))
        text, lines_ = "", []
        for part_ in parts:                       # as many parts per line as fit
            if text and len(text) + len(part_) + 5 > iw - 9:
                lines_.append(text)
                text = ""
            text = f"{text}  ·  {part_}" if text else part_
        lines_.append(text)
        for n, text in enumerate(lines_):
            if row >= bottom:
                break
            line(c, ix, row, iw, (f"{'Docker' if n == 0 else '':<9}", C["dim"], True), (text, C["text"]))
            row += 1
    room = bottom - row - 1
    if room >= 5:
        row += 1
        half = room // 2
        top = nice_top(list(sampler.hist["rd"])[-(w - 2) * 2:] + list(sampler.hist["wr"])[-(w - 2) * 2:], 1_000_000)
        graph(c, x + 1, row, w - 2, half, sampler.hist["rd"], top, "disk")
        graph(c, x + 1, row + half, w - 2, room - half, sampler.hist["wr"], top, "mem", down=True)
        c.put(x + 2, row, f"▲ read {fmt_bytes(sampler.now['rd'], True)}", C["accent"])
        c.put(x + 2, row + room - 1, f"▼ write {fmt_bytes(sampler.now['wr'], True)}", C["mem"])


def power_panel(c, sampler, x, y, w, h):
    if tiny(w, h):
        return
    sd = sysdata(sampler)
    b = sd.get("power")
    if not b:
        pct, stat, watts = sampler.now.get("battery") or (None, None, None)
        b = {"percent": pct, "status": stat, "power_w": watts} if pct is not None else None
    box(c, x, y, w, h, "power", C["conn"], ("on mains power" if b.get("ac_online") else "on battery") if b and "ac_online" in b else "")
    ix, row, bottom, iw = x + 2, y + 1, y + h - 1, w - 4
    if not b:
        line(c, ix, row, iw, ("no battery in this machine", C["dim"]))
        return
    pct = b.get("percent")
    if pct is not None:
        xx = c.put(ix, row, f"{pct:3d}% ", C["bright"], bold=True)
        mw = max(min(iw - 22, 30), 6)
        bar(c, xx, row, mw, pct / 100, level_colour(1 - pct / 100, 0.8, 0.9))
        line(c, xx + mw + 1, row, ix + iw - xx - mw - 1, (b.get("status") or "", C["text"]))
        row += 1
    rows = []
    if b.get("health_pct") is not None:
        rows.append(f"health {b['health_pct']:.1f}% of new · {b.get('capacity_wh')} of {b.get('design_wh')} Wh")
    if b.get("cycles") is not None:
        rows.append(f"{b['cycles']} charge cycles" + (f" · {b['technology']}" if b.get("technology") else ""))
    flow = []
    if b.get("power_w") is not None:
        flow.append(f"{b['power_w']:.1f} W")
    if b.get("voltage_v"):
        flow.append(f"{b['voltage_v']:.2f} V")
    if b.get("current_a"):
        flow.append(f"{b['current_a']:.2f} A")
    if b.get("temp_c") is not None:
        flow.append(f"cell temperature {b['temp_c']:.1f}°C")
    if flow:
        rows.append(" · ".join(flow))
    if b.get("seconds_left"):
        rows.append(("time to full " if b.get("status") == "Charging" else "time left ") + fmt_duration(b["seconds_left"]))
    if b.get("limit"):
        rows.append(f"charging stops at {b['limit']}%")
    for text in rows:
        if row >= bottom:
            break
        line(c, ix, row, iw, (text, C["text"] if row == y + 2 else C["dim"]))
        row += 1


def thermal_detail_panel(c, sampler, x, y, w, h):
    if tiny(w, h):
        return
    sd, now = sysdata(sampler), sampler.now
    temps = (sd.get("sensors") or {}).get("temps") or [{"chip": r["chip"], "label": r["label"], "temp_c": r["temp"], "crit_c": r["limit"], "max_c": None}
                                                         for r in getattr(sampler, "thermal", [])]
    fans = (sd.get("sensors") or {}).get("fans") or []
    box(c, x, y, w, h, "thermal and fans", C["warn"] if cooling_fault(now.get("temp"), now.get("fans") or [f["rpm"] for f in fans]) else C["accent"],
        f"{(now.get('mhz') or 0) / 1000:.2f} GHz" if now.get("mhz") else "")
    ix, row, bottom, iw = x + 2, y + 1, y + h - 1, w - 4
    mw = max(iw - 46, 6)
    for t in sorted(temps, key=lambda r: -r["temp_c"]):
        if row >= bottom - 3:
            break
        limit = t.get("crit_c") or t.get("max_c") or 100.0
        colour = grad("temp", temp_pos(t["temp_c"]))
        xx = fields(c, ix, row, ix + iw, (t["chip"], C["dim"], 9), (t["label"], C["dim"], 14), (f"{t['temp_c']:4.0f}°C", colour, 7))
        bar(c, xx, row, mw, t["temp_c"] / limit, colour)
        line(c, xx + mw + 1, row, ix + iw - xx - mw - 1, (f"of {limit:.0f}°C", C["faint"] if c.rich else C["off"]))
        row += 1
    fi = sampler.fans or {}
    fan_rows = fans or [{"rpm": r, "min": None, "max": fi.get("fans", [{}])[0].get("max_rpm") if fi.get("fans") else None, "target": None} for r in now.get("fans") or []]
    if row < bottom and fan_rows:
        row += 1
    for f in fan_rows:
        if row >= bottom:
            break
        top = f.get("max") or 8000
        xx = c.put(ix, row, f"FAN  {f['rpm']:5d} rpm ", C["bright"], bold=True)
        bar(c, xx, row, mw, f["rpm"] / top, C["conn"])
        line(c, xx + mw + 1, row, ix + iw - xx - mw - 1, (f"of {top} · floor {f.get('min') or '?'}", C["faint"] if c.rich else C["off"]))
        row += 1
    if fi and row < bottom:
        mode = f"fan controller: {fi.get('mode', '?')}" + (f" · target {fi['target_pct']:.0f}%" if fi.get("target_pct") else "")
        line(c, ix, row, iw, (mode, C["dim"]))
        row += 1
    if row < bottom:
        line(c, ix, row, iw, ("100°C is the chip's own limit. Running there under load is normal for this Mac.", C["dim"]))


def network_detail_panel(c, sampler, x, y, w, h):
    if tiny(w, h):
        return
    now, sd, ld = sampler.now, sysdata(sampler), livedata(sampler)
    net = sd.get("network") or {}
    box(c, x, y, w, h, "network", C["net"], sampler.iface or "offline")
    ix, row, bottom, iw = x + 2, y + 1, y + h - 1, w - 4
    xx = c.put(ix, row, "▼ ", C["net"], bold=True)
    xx = c.put(xx, row, f"{fmt_bytes(now['rx'], True):>10}", C["bright"], bold=True)
    xx = c.put(xx + 3, row, "▲ ", C["conn"], bold=True)
    xx = c.put(xx, row, f"{fmt_bytes(now['tx'], True):>10}", C["bright"], bold=True)
    line(c, xx + 3, row, ix + iw - xx - 3, (f"since boot  ▼ {fmt_bytes(now.get('rx_total'))}   ▲ {fmt_bytes(now.get('tx_total'))}", C["dim"]))
    row += 1
    gh = min(6, max((bottom - row - 8) // 2, 0)) if h >= 24 else 0
    if gh >= 2:
        graph(c, x + 1, row, w - 2, gh, sampler.hist["rx"], nice_top(list(sampler.hist["rx"])[-(w - 2) * 2:], 100_000), "down")
        graph(c, x + 1, row + gh, w - 2, gh, sampler.hist["tx"], nice_top(list(sampler.hist["tx"])[-(w - 2) * 2:], 100_000), "up", down=True)
        row += 2 * gh
    rates = (ld.get("net") or {}).get("ifaces") or {}
    ifaces = [i for i in net.get("interfaces") or [] if i["kind"] != "bridge" or i["state"] == "up"]
    if ifaces and row + 2 < bottom:
        row += 1
        rows = []
        for i in ifaces:
            r = rates.get(i["name"])
            up = i["state"] in ("up", "unknown")
            rows.append([(i["name"] if i["kind"] != "container" else f"containers x{i.get('count', 0)}", C["bright"] if up else C["dim"], up),
                         (i["state"] if i["kind"] != "container" else "", C["ok"] if i["state"] == "up" else C["dim"]),
                         ((i.get("addresses") or [""])[0], C["text"]),
                         (fmt_bytes(r["rx"], True) if r else "", C["net"]), (fmt_bytes(r["tx"], True) if r else "", C["conn"]),
                         (fmt_bytes(i["rx_bytes"]), C["dim"]), (fmt_bytes(i["tx_bytes"]), C["dim"]),
                         (f"{i['rx_errors'] + i['tx_errors']}", C["bad"] if i["rx_errors"] + i["tx_errors"] else C["dim"]),
                         (f"{i['rx_drops'] + i['tx_drops']}", C["warn"] if i["rx_drops"] + i["tx_drops"] > 100 else C["dim"])])
        cols = [("INTERFACE", 18, "l"), ("STATE", 7, "l"), ("ADDRESS", None, "l"), ("IN/s", 10, "r"), ("OUT/s", 10, "r"),
                ("IN TOTAL", 9, "r"), ("OUT TOTAL", 9, "r"), ("ERR", 4, "r"), ("DROP", 5, "r")]
        if iw < 100:
            cols = [cols[0], cols[2], cols[3], cols[4]]
            rows = [[r[0], r[2], r[3], r[4]] for r in rows]
        row += table(c, ix, row, iw, bottom - row, cols, rows) + 1
    wifi = net.get("wifi")
    if wifi and row < bottom:
        dbm = wifi.get("signal_dbm")
        line(c, ix, row, iw, ("Wi-Fi  ", C["dim"], True), (f"{wifi['ssid']}  ", C["bright"], True),
             (f"{wifi.get('band') or ''} channel {wifi.get('channel')} · {wifi.get('rate_mbps')} Mbit/s · {wifi.get('security') or 'open'}", C["text"]),
             ((f" · {dbm} dBm" if dbm is not None else "") + (f" ({wifi['signal_pct']}%)" if wifi.get("signal_pct") is not None else ""),
              C["warn"] if dbm is not None and dbm < -75 else C["text"]))
        row += 1
    chk = (debug_of(sampler).get("net") or {})
    if chk and row < bottom:
        def ms(v):
            return "no answer" if v is None else f"{v:.0f} ms"
        line(c, ix, row, iw, ("reach  ", C["dim"], True), (f"router {chk.get('gateway') or '-'} {ms(chk.get('gateway_ms'))}   internet {ms(chk.get('internet_ms'))}   "
                                                         f"DNS {ms(chk.get('dns_ms'))}", C["text"]))
        row += 1
    tcp = net.get("tcp")
    if tcp and row < bottom:
        ct = (sd.get("kernel") or {}).get("conntrack") or {}
        text = (f"{tcp.get('established')} connections open · {tcp.get('time_wait')} closing · {fmt_count(tcp.get('retransmitted') or 0)} segments resent"
                f" · {fmt_count(tcp.get('failed_attempts') or 0)} failed attempts")
        if ct.get("count") is not None and ct.get("max"):
            text += f" · tracked {ct['count']}/{fmt_count(ct['max'])}"
        line(c, ix, row, iw, ("tcp    ", C["dim"], True), (text, C["text"]))
        row += 1


def tailnet_panel(c, sampler, x, y, w, h):
    if tiny(w, h):
        return
    ts = sysdata(sampler).get("tailscale") or {}
    peers = ts.get("peers") or []
    online = sum(1 for p in peers if p["online"])
    box(c, x, y, w, h, "tailnet", C["conn"], (f"v{ts['version']} · " if ts.get("version") else "") + f"{online}/{len(peers)} devices online" if peers else "")
    ix, row, bottom, iw = x + 2, y + 1, y + h - 1, w - 4
    if not peers:
        line(c, ix, row, iw, ("no device list yet (collector not updated)", C["dim"]))
        return
    rows = [[(p["name"], C["bright"] if p["online"] else C["dim"], p["online"]), (p.get("os") or "", C["dim"]),
             (p["route"], C["ok"] if p["route"] == "direct" else C["warn"] if p["route"].startswith("relay") else C["dim"]),
             (fmt_bytes(p.get("rx_bytes", 0)), C["net"]), (fmt_bytes(p.get("tx_bytes", 0)), C["conn"]),
             ("now" if p["online"] else fmt_since(p.get("last_seen")) + " ago", C["text"] if p["online"] else C["dim"])] for p in peers]
    cols = [("DEVICE", None, "l"), ("OS", 8, "l"), ("ROUTE", 11, "l"), ("RECEIVED", 9, "r"), ("SENT", 9, "r"), ("SEEN", 9, "r")]
    if iw < 70:
        cols, rows = [cols[0], cols[2], cols[5]], [[r[0], r[2], r[5]] for r in rows]
    row += table(c, ix, row, iw, bottom - row, cols, rows) + 1
    notes = [(h_, C["warn"]) for h_ in ts.get("health") or []]
    if ts.get("key_expiry") and row < bottom:
        left = ts["key_expiry"] - time.time()
        notes.append((f"this Mac's Tailscale key expires in {fmt_duration(int(left))}" if left > 0 else "this Mac's Tailscale key has expired", C["dim"] if left > 14 * 86400 else C["warn"]))
    if ts.get("funnel_relays") and row < bottom:
        notes.append((f"{ts['funnel_relays']} Tailscale Funnel relays are connected (not counted as devices)", C["faint"] if c.rich else C["off"]))
    if ts.get("update_available"):
        notes.append(("a newer Tailscale is available", C["warn"]))
    for text, colour in notes:
        if row >= bottom:
            break
        line(c, ix, row, iw, (text, colour))
        row += 1


def uptime_text(seconds):
    return fmt_duration(int(seconds)) if seconds else "?"


def service_rows(sampler, detailed):
    """(project rows and service rows) for the services table, grouped by compose project."""
    s = sampler.status or {}
    items = list(s.get("containers", []) or [])
    groups = {}
    for item in items:
        short = item["name"].split(".")[-1]
        for prefix in ("supabase-", f"{item.get('project') or '-'}-"):
            short = short[len(prefix):] if short.startswith(prefix) else short
        item["short"] = re.sub(r"-\d+$", "", short)
        groups.setdefault(item.get("project") or "-", []).append(item)
    out = []
    for project, members in sorted(groups.items(), key=lambda kv: (-sum(m.get("mem") or 0 for m in kv[1]), kv[0])):
        members.sort(key=lambda i: (i.get("state") == "running", -(i.get("mem") or 0), i["short"]))
        up = sum(1 for m in members if m.get("state") == "running")
        cpu = sum(m.get("cpu") or 0 for m in members)
        mem = sum(m.get("mem") or 0 for m in members)
        out.append(("project", project, f"{up}/{len(members)} running · {cpu:.1f}% cpu · {fmt_bytes(mem)}", up < len(members)))
        out.extend(("service", m, None, None) for m in members)
    return out


def services_table(c, sampler, x, y, w, h, detailed=False):
    if tiny(w, h):
        return
    s = sampler.status or {}
    items = list(s.get("containers", []) or [])
    up = sum(1 for i in items if i.get("state") == "running")
    box(c, x, y, w, h, "services", C["svc"], f"{up}/{len(items)} running" if items else "")
    ix, iw = x + 2, w - 4
    if not items:
        c.put(ix, y + 1, "Supabase is installed by setup. Nothing to show yet.", C["dim"])
        return
    rows_all = service_rows(sampler, detailed)
    cols = [("SERVICE", None, "l"), ("STATE", 8, "l"), ("HEALTH", 9, "l"), ("CPU (bar = 1 core)", 24, "l"), ("MEMORY", 9, "r")]
    if iw >= 120:
        cols += [("IN/s", 9, "r"), ("OUT/s", 9, "r"), ("WRITE/s", 9, "r")]
    if iw >= 150 and not detailed:
        cols.insert(4, ("TREND (15 min)", 16, "l"))
    if iw >= 140 or detailed:
        cols += [("PIDS", 5, "r"), ("UP", 6, "r"), ("RESTARTS", 8, "r")]
    if detailed and iw >= 170:
        cols += [("IMAGE", 30, "l")]
    if iw < 80:
        cols = [cols[0], cols[1], cols[3], cols[4]] if iw >= 56 else [cols[0], cols[1], cols[4]]
    fixed = sum(cw for _, cw, _ in cols if cw) + 2 * (len(cols) - 1)
    widths = [cw if cw else max(iw - fixed, 0) for _, cw, _ in cols]
    xx = ix
    for (title, _, align), cw in zip(cols, widths):
        text = clip(title, cw)
        c.put(xx, y + 1, text.rjust(cw) if align == "r" else text, C["dim"], bold=True)
        xx += cw + 2
    room = h - 3
    shown = rows_all if len(rows_all) <= room else [r for r in rows_all if r[0] == "service"]       # tight: drop the group lines first
    hidden = 0
    if len(shown) > room:
        hidden = len(shown) - max(room - 1, 0)
        shown = shown[: max(room - 1, 0)]
    for n, (kind, a, b, c_flag) in enumerate(shown):
        row = y + 2 + n
        if kind == "project":
            xx = c.put(ix, row, clip(a, 28), C["accent"], bold=True)
            line(c, xx + 2, row, ix + iw - xx - 2, (b, C["bad"] if c_flag else C["dim"]))
            continue
        item = a
        running = item.get("state") == "running"
        state = "ok" if running else "bad"
        health = item.get("health") or ("unhealthy" if "unhealthy" in item.get("status", "") else "healthy" if "healthy" in item.get("status", "") else "")
        if running and health == "unhealthy":
            state = "warn"
        cpu = item.get("cpu")
        cells_ = {"SERVICE": (item["short"], C["bright"] if running else C["bad"], not running),
                  "STATE": (item.get("state", "?"), STATE_COLOUR[state]),
                  "HEALTH": (health or "-", C["warn"] if health == "unhealthy" else C["dim"]),
                  "MEMORY": (fmt_bytes(item.get("mem")) if item.get("mem") is not None else "-", C["text"]),
                  "IN/s": (fmt_bytes(item.get("net_rx", 0), True) if running else "-", C["net"]),
                  "OUT/s": (fmt_bytes(item.get("net_tx", 0), True) if running else "-", C["conn"]),
                  "WRITE/s": (fmt_bytes(item.get("blk_write", 0), True) if running else "-", C["text"]),
                  "PIDS": (str(item.get("pids", "-")), C["dim"]),
                  "UP": (fmt_since(item.get("started")) if running else "-", C["dim"]),
                  "RESTARTS": (str(item.get("restarts") if item.get("restarts") is not None else "-"), C["warn"] if item.get("restarts") else C["dim"]),
                  "IMAGE": (item.get("image", ""), C["faint"] if c.rich else C["off"])}
        xx = ix
        for (title, _, align), cw in zip(cols, widths):
            if title.startswith("TREND"):
                hist = sampler.svc_hist.get(item["name"])
                if hist and running:
                    graph(c, xx, row, 14, 1, hist, 100.0, "cpu")
                else:
                    c.put(xx, row, "·" * 14, C["faint"] if c.rich else C["off"])
            elif title.startswith("CPU"):
                if cpu is None:
                    c.put(xx, row, "-", C["dim"])
                else:
                    bar(c, xx, row, 14, min(cpu / 100, 1), C["cpu"])
                    c.put(xx + 15, row, f"{cpu:5.1f}%", C["text"])
            else:
                text, colour = cells_[title][0], cells_[title][1]
                text = clip(text, cw)
                if title == "SERVICE":
                    dot(c, xx, row, state)
                    c.put(xx + 2, row, clip(item["short"], cw - 2), colour, bold=len(cells_[title]) > 2 and cells_[title][2])
                else:
                    c.put(xx, row, text.rjust(cw) if align == "r" else text, colour)
            xx += cw + 2
    if hidden:
        c.put(ix, y + 2 + len(shown), f"… {hidden} more", C["dim"])


def db_panel(c, sampler, x, y, w, h):
    if tiny(w, h):
        return
    db = sysdata(sampler).get("db")
    box(c, x, y, w, h, "database", C["net"], (f"PostgreSQL {db['version']} · up {fmt_duration(db['uptime_s'])}" if db and db.get("version") else ""))
    ix, row, bottom, iw = x + 2, y + 1, y + h - 1, w - 4
    if not db:
        line(c, ix, row, iw, ("no database container found", C["dim"]))
        return
    if db.get("error"):
        line(c, ix, row, iw, (db["error"], C["warn"]))
        return
    conn, limit = db.get("connections", 0), db.get("max_connections") or 100
    mw = max(min(iw - 40, 24), 6)
    xx = c.put(ix, row, f"{'Connections':<13}", C["dim"])
    xx = c.put(xx, row, f"{conn:>4}/{limit} ", C["bright"])
    bar(c, xx, row, mw, conn / limit, level_colour(conn / limit, 0.7, 0.9))
    row += 1
    hit = db.get("cache_hit_pct")
    two = iw >= 70
    pairs = [("Size", fmt_bytes(db.get("size"))), ("Active now", f"{db.get('active', 0)} ({db.get('idle_in_transaction', 0)} idle in transaction)"),
             ("Transactions", f"{db.get('tps', 0):.1f}/s"), ("Cache hit", f"{hit:.2f}%" if hit is not None else "-"),
             ("Committed", fmt_count(db.get("commits", 0))), ("Rolled back", fmt_count(db.get("rollbacks", 0))),
             ("Deadlocks", str(db.get("deadlocks", 0))), ("Temp files", fmt_bytes(db.get("temp_bytes"))),
             ("Rows read", fmt_count(db.get("rows_returned", 0))), ("Rows written", fmt_count(db.get("rows_inserted", 0) + db.get("rows_updated", 0))),
             ("Rows deleted", fmt_count(db.get("rows_deleted", 0))), ("App users", str(db["users"]) if db.get("users") is not None else "-"),
             ("Stored files", str(db["storage_objects"]) if db.get("storage_objects") is not None else "-")]
    step = 2 if two else 1
    colw = iw // step
    for i in range(0, len(pairs), step):
        if row >= bottom:
            break
        for j in range(step):
            if i + j >= len(pairs):
                break
            label, value = pairs[i + j]
            bad = (label == "Deadlocks" and value != "0") or (label == "Cache hit" and hit is not None and hit < 95)
            xx = c.put(ix + j * colw, row, f"{label:<13}", C["dim"])
            line(c, xx, row, colw - 14, (value, C["warn"] if bad else C["bright"]))
        row += 1


def processes_panel(c, sampler, x, y, w, h):
    if tiny(w, h):
        return
    ld = livedata(sampler)
    procs = ld.get("procs") or []
    if not procs:                                   # an older collector: the screen's own list, by program name
        return procs_panel(c, sampler, x, y, w, h)
    tasks = ld.get("tasks") or {}
    box(c, x, y, w, h, "processes", C["cpu"], f"{tasks.get('total', len(procs))} tasks · {tasks.get('threads', '?')} threads" if tasks else "")
    ix, row, bottom, iw = x + 2, y + 1, y + h - 1, w - 4
    rows = [[(str(p["pid"]), C["dim"]), (p["name"], C["bright"]), (p["user"], C["dim"]), (f"{p['cpu']:.1f}%", C["text"]),
             (fmt_bytes(p["rss"]), C["text"]), (f"{p.get('mem', 0):.1f}%", C["dim"]), (str(p.get("threads", "")), C["dim"])] for p in procs]
    cols = [("PID", 8, "r"), ("PROGRAM", None, "l"), ("USER", 12, "l"), ("CPU", 7, "r"), ("MEMORY", 9, "r"), ("MEM%", 6, "r"), ("THR", 4, "r")]
    if iw < 64:
        cols, rows = [cols[1], cols[3], cols[4]], [[r[1], r[3], r[4]] for r in rows]
    table(c, ix, row, iw, bottom - row, cols, rows)


def system_facts_panel(c, sampler, x, y, w, h):
    if tiny(w, h):
        return
    s, sd, now = sampler.status or {}, sysdata(sampler), sampler.now
    host, hs = sd.get("host") or {}, s.get("host") or {}
    box(c, x, y, w, h, "this machine", C["accent"], hs.get("model") or "")
    ix, row, bottom, iw = x + 2, y + 1, y + h - 1, w - 4
    boot = host.get("boot_time")
    ts = sd.get("tailscale") or {}
    mu = s.get("macserver_update") or {}
    facts = [("Operating system", host.get("os") or "?"),
             ("Kernel", f"{hs.get('kernel', '?')}" + ("" if hs.get("t2_kernel", True) else "  (NOT the T2 kernel)")),
             ("Firmware", host.get("firmware") or "?"), ("Architecture", host.get("arch") or "?"),
             ("Running since", time.strftime("%a %d %b %H:%M", time.localtime(boot)) + f"  ({uptime_text(now.get('uptime'))})" if boot else uptime_text(now.get("uptime"))),
             ("Time zone", host.get("timezone") or "?"), ("Clock", "synced" if s.get("clock_synced") else "NOT synced" if s.get("clock_synced") is False else "?"),
             ("Docker", host.get("docker") or "?"), ("Tailscale", ts.get("version") or "?"),
             ("Debian updates", f"{hs.get('updates_pending', 0)} waiting" if hs.get("updates_pending") else "up to date"),
             ("Restart needed", "yes, to finish updates" if hs.get("reboot_required") else "no"),
             ("MacServer", (mu.get("message") or mu.get("state") or "?") + (f"  ({mu['current'][:7]})" if mu.get("current") else "")),
             ("Logins", ", ".join(f"{x_['user']} on {x_['tty']}" for x_ in host.get("sessions") or []) or "none")]
    for label, value in facts:
        if row >= bottom:
            break
        warn = value.startswith("NOT") or value.endswith("waiting") or value.startswith("yes")
        xx = c.put(ix, row, f"{label:<17}", C["dim"])
        line(c, xx, row, ix + iw - xx, (value, C["warn"] if warn else C["bright"]))
        row += 1


# --- the pages ------------------------------------------------------------------------------------------------------

def tiny(w, h):
    """Too small for a panel's content: the panel draws its frame (or nothing) only."""
    return w < 14 or h < 3


def home_tabs(c, sampler, y):
    """The page strip under the top bar; returns the first free row."""
    now_page = sampler.home % len(HOME_PAGES)
    x = 1
    for i, name in enumerate(HOME_PAGES):
        active = i == now_page
        if c.w < 62 and not active:
            continue
        label = f" {name.upper()} "
        x = c.put(x, y, label, (C["pill_fg"] if c.rich else C["bright"]) if active else C["dim"], C["accent"] if active else None, bold=active) + 1
    left = max(int(HOME_DWELL_S[HOME_PAGES[now_page]] - (time.monotonic() - sampler.home_t)), 0)
    note = "page held (p)" if sampler.paused else "" if sampler.force_overview else f"next page in {left}s (n/b p)"
    if note and x + len(note) + 3 < c.w:
        c.put(c.w - len(note) - 1, y, note, C["dim"])
    return y + 1


def page_overview(c, sampler, x, y, w, h):
    setup = not (sampler.status or {}).get("setup_done")
    kpi_h = 6 if h >= 44 else 4 if h >= 22 else 0
    if kpi_h:
        kpi_strip(c, sampler, x, y, w, kpi_h)
    y, h = y + kpi_h, h - kpi_h
    lower = setup_panel if setup else services_table
    if w >= 170:
        top_h = min(max(round(h * 0.46), 14), 30)
        if h - top_h < 12:
            top_h = max(h - 12, 8)
        a, b, d = columns(x, w, [31, 36, 33])
        cpu_detail_panel(c, sampler, a[0], y, a[1], top_h)
        m, st = fit(y, top_h, [12, 13], grow=1)
        memory_detail_panel(c, sampler, b[0], m[0], b[1], m[1])
        storage_panel(c, sampler, b[0], st[0], b[1], st[1])
        network_detail_panel(c, sampler, d[0], y, d[1], top_h)
        ew = w * 32 // 100
        lower(c, sampler, x, y + top_h, w - ew, h - top_h)
        cn, ev = fit(y + top_h, h - top_h, [len(connections(sampler)) + 4, 8], grow=1)
        conn_panel(c, sampler, x + w - ew, cn[0], ew, cn[1])
        events_panel(c, sampler, x + w - ew, ev[0], ew, ev[1])
    elif w >= 110:
        r1, r2, r3 = stack(y, h, [34, 26, 40])
        a, b = columns(x, w, [50, 50])
        cpu_detail_panel(c, sampler, a[0], r1[0], a[1], r1[1])
        memory_detail_panel(c, sampler, b[0], r1[0], b[1], r1[1])
        network_detail_panel(c, sampler, a[0], r2[0], a[1], r2[1])
        storage_panel(c, sampler, b[0], r2[0], b[1], r2[1])
        lower(c, sampler, x, r3[0], w, r3[1])
    else:
        r1, r2 = stack(y, h, [2, 3])
        cpu_detail_panel(c, sampler, x, r1[0], w, r1[1])
        lower(c, sampler, x, r2[0], w, r2[1])


def page_hardware(c, sampler, x, y, w, h):
    if w >= 170:
        r1, r2 = stack(y, h, [52, 48])
        a, b, d = columns(x, w, [36, 32, 32])
        cpu_detail_panel(c, sampler, a[0], r1[0], a[1], r1[1])
        th, pw = fit(r1[0], r1[1], [16, 10], grow=0)
        thermal_detail_panel(c, sampler, b[0], th[0], b[1], th[1])
        power_panel(c, sampler, b[0], pw[0], b[1], pw[1])
        processes_panel(c, sampler, d[0], r1[0], d[1], r1[1])
        memory_detail_panel(c, sampler, a[0], r2[0], a[1], r2[1])
        storage_panel(c, sampler, b[0], r2[0], b[1], r2[1])
        system_facts_panel(c, sampler, d[0], r2[0], d[1], r2[1])
    elif w >= 110:
        r1, r2 = stack(y, h, [50, 50])
        a, b = columns(x, w, [50, 50])
        cpu_detail_panel(c, sampler, a[0], r1[0], a[1], r1[1])
        th, pw = fit(r1[0], r1[1], [14, 9], grow=0)
        thermal_detail_panel(c, sampler, b[0], th[0], b[1], th[1])
        power_panel(c, sampler, b[0], pw[0], b[1], pw[1])
        memory_detail_panel(c, sampler, a[0], r2[0], a[1], r2[1])
        storage_panel(c, sampler, b[0], r2[0], b[1], r2[1])
    else:
        r1, r2 = stack(y, h, [3, 2])
        cpu_detail_panel(c, sampler, x, r1[0], w, r1[1])
        thermal_detail_panel(c, sampler, x, r2[0], w, r2[1])


def page_network_home(c, sampler, x, y, w, h):
    if w >= 170:
        r1, r2 = stack(y, h, [52, 48])
        a, b = columns(x, w, [56, 44])
        network_detail_panel(c, sampler, a[0], r1[0], a[1], r1[1])
        tailnet_panel(c, sampler, b[0], r1[0], b[1], r1[1])
        p, i, o, t = columns(x, w, [24, 24, 26, 26])
        listeners_panel(c, sampler, p[0], r2[0], p[1], r2[1])
        flows_panel(c, sampler, i[0], r2[0], i[1], r2[1], "in")
        flows_panel(c, sampler, o[0], r2[0], o[1], r2[1], "out")
        requests_stats_panel(c, sampler, t[0], r2[0], t[1], r2[1])
    elif w >= 110:
        r1, r2 = stack(y, h, [52, 48])
        a, b = columns(x, w, [55, 45])
        network_detail_panel(c, sampler, a[0], r1[0], a[1], r1[1])
        tailnet_panel(c, sampler, b[0], r1[0], b[1], r1[1])
        listeners_panel(c, sampler, a[0], r2[0], a[1], r2[1])
        fi, fo = stack(r2[0], r2[1], [1, 1])
        flows_panel(c, sampler, b[0], fi[0], b[1], fi[1], "in")
        flows_panel(c, sampler, b[0], fo[0], b[1], fo[1], "out")
    else:
        r1, r2 = stack(y, h, [3, 2])
        network_detail_panel(c, sampler, x, r1[0], w, r1[1])
        tailnet_panel(c, sampler, x, r2[0], w, r2[1])


def page_services_home(c, sampler, x, y, w, h):
    r1, r2 = stack(y, h, [58, 42]) if h >= 30 else stack(y, h, [2, 1])
    services_table(c, sampler, x, r1[0], w, r1[1], detailed=True)
    if w >= 150:
        a, b, d = columns(x, w, [36, 34, 30])
        db_panel(c, sampler, a[0], r2[0], a[1], r2[1])
        requests_stats_panel(c, sampler, b[0], r2[0], b[1], r2[1])
        events_panel(c, sampler, d[0], r2[0], d[1], r2[1])
    elif w >= 100:
        a, b = columns(x, w, [50, 50])
        db_panel(c, sampler, a[0], r2[0], a[1], r2[1])
        events_panel(c, sampler, b[0], r2[0], b[1], r2[1])
    else:
        db_panel(c, sampler, x, r2[0], w, r2[1])


def page_system_home(c, sampler, x, y, w, h):
    if w >= 170:
        r1, r2 = stack(y, h, [44, 56])
        a, b, d = columns(x, w, [28, 22, 50])
        system_facts_panel(c, sampler, a[0], r1[0], a[1], r1[1])
        status_panel(c, sampler, b[0], r1[0], b[1], r1[1])
        events_panel(c, sampler, d[0], r1[0], d[1], r1[1])
        p, q, s = columns(x, w, [34, 33, 33])
        logs_panel(c, sampler, p[0], r2[0], p[1], r2[1])
        service_errors_panel(c, sampler, q[0], r2[0], q[1], r2[1])
        files_panel(c, sampler, s[0], r2[0], s[1], r2[1])
    elif w >= 110:
        r1, r2 = stack(y, h, [45, 55])
        a, b = columns(x, w, [50, 50])
        system_facts_panel(c, sampler, a[0], r1[0], a[1], r1[1])
        events_panel(c, sampler, b[0], r1[0], b[1], r1[1])
        logs_panel(c, sampler, a[0], r2[0], a[1], r2[1])
        service_errors_panel(c, sampler, b[0], r2[0], b[1], r2[1])
    else:
        r1, r2 = stack(y, h, [1, 1])
        system_facts_panel(c, sampler, x, r1[0], w, r1[1])
        logs_panel(c, sampler, x, r2[0], w, r2[1])


HOME_DRAW = {"overview": page_overview, "hardware": page_hardware, "network": page_network_home,
             "services": page_services_home, "system": page_system_home}


NOTICE_ROWS = 3    # issues spelled out above the pages; the rest are counted, all are in `macserver issues`


def notice_lines(c, sampler, items, y):
    """One line per issue the top bar counts, above the pages: what it is and the first thing to do, so
    "1 NOTICE(S)" explains itself without a login. Returns the first free row."""
    if not items or c.h < 14:
        return y
    shown = items[:1 if c.h < 30 else NOTICE_ROWS]
    more = len(items) - len(shown)
    for n, item in enumerate(shown):
        note = (f"+{more} more: macserver issues" if more and n == len(shown) - 1
                else "details: macserver issues" if n == 0 else "")
        right = c.w - 1 - len(note)
        if note and right > 60:
            c.put(right, y, note, C["dim"])
        else:
            right = c.w - 1
        x = pill(c, 1, y, item["level"])
        parts = [(item["title"], C["bright"], True)]
        if item["fix"]:
            parts.append(("   fix: " + item["fix"][0], C["accent"]))
        if item["why"]:
            parts.append(("   " + item["why"], C["dim"]))
        line(c, x + 2, y, right - x - 4, *parts)
        y += 1
    return y


def draw_home(c, sampler, top=1):
    """The normal screen: the page strip, then the current page (below any notice lines)."""
    sampler.advance_home(time.monotonic())
    page = HOME_PAGES[sampler.home % len(HOME_PAGES)]
    y = home_tabs(c, sampler, top) if c.h >= 14 else top
    HOME_DRAW[page](c, sampler, 0, y, c.w, c.h - 1 - y)
    return c


def draw_overview(c, sampler):
    """Kept for callers of the old name: the home page currently showing."""
    return draw_home(c, sampler)


def draw(c, sampler, kiosk=True):
    """Lay out every panel for a canvas of any size (80x24 up to 250x80)."""
    w, h = c.w, c.h
    every = incidents(sampler)
    problems = [(i["level"], i["title"]) for i in every]
    counted = [i for i in every if not muted_notice(sampler, i["level"], i["title"])]   # = counted_issues()
    label = [(i["level"], i["title"]) for i in counted]
    level = alert_level(label)
    set_theme(level == "critical")                  # an incident turns the whole screen red
    if level != "critical":
        sampler.force_overview = False
    c.alert = level
    if level == "critical":
        sampler.crit_since = sampler.crit_since or time.time()
    else:
        sampler.crit_since = None
    if c.rich:
        c.fill(0, 0, w, h, C["bg"])
    top_bar(c, sampler, label)
    bottom_bar(c, sampler, kiosk, level)
    if level == "critical" and not sampler.force_overview:
        incident_view(c, sampler, problems, kiosk)
        c.ring(ring_colour(level, time.time()))
        return c
    draw_home(c, sampler, notice_lines(c, sampler, counted, 1))
    c.ring(ring_colour(level, time.time()))
    return c


def render_once(width=120, height=34, rich=True):
    sampler = Sampler()
    time.sleep(0.001)
    sampler.sample()
    return draw(Canvas(width, height, rich), sampler, kiosk=False)


# --- terminal front end ---------------------------------------------------------------

VT_REQUEST = Path(os.environ.get("MACSERVER_VT_REQUEST", "/run/macserver-console/vt"))


def wanted_screen(keys):
    """Kiosk keys: 2..6 on the number row (alone or with Ctrl/Option) open that
    screen, 2 being the login prompt. Ctrl+2 arrives as NUL. Anything else: None."""
    for byte in keys:
        if 0x32 <= byte <= 0x36:
            return byte - 0x30
        if byte == 0:
            return 2
    return None


def request_screen(n):
    """Ask the root helper (macserver-vt.path) to switch the Mac's screen."""
    try:
        VT_REQUEST.write_text(f"{n}\n")
    except OSError:
        pass


def rich_terminal():
    return os.environ.get("TERM", "") != "linux"


def run(kiosk):
    import termios
    import tty
    fd = sys.stdin.fileno() if sys.stdin.isatty() else None
    saved = termios.tcgetattr(fd) if fd is not None else None
    out = sys.stdout
    rich = rich_terminal()
    resized = [True]
    signal.signal(signal.SIGWINCH, lambda *_: resized.__setitem__(0, True))
    if kiosk:
        for sig in (signal.SIGINT, signal.SIGQUIT, signal.SIGTSTP, signal.SIGHUP):
            signal.signal(sig, signal.SIG_IGN)
    try:
        if fd is not None:
            (tty.setraw if kiosk else tty.setcbreak)(fd)
        if os.environ.get("TERM") == "linux":
            out.write("\x1b[9;0]\x1b[14;0]")           # console: never blank, never power down
        out.write("\x1b[?1049h\x1b[?25l\x1b[?7l\x1b[2J")
        out.flush()
        sampler = Sampler()
        next_tick = next_pulse = next_draw = 0.0
        canvas = None
        redraw = False
        sync_on, sync_off = ("\x1b[?2026h", "\x1b[?2026l") if rich else ("", "")
        while True:
            t = time.monotonic()
            if t >= next_tick or t >= next_draw or resized[0] or redraw:
                if t >= next_tick:
                    sampler.sample()
                    next_tick = t + SAMPLE_S
                next_draw = t + DRAW_S
                if resized[0]:
                    out.write("\x1b[2J")
                    resized[0] = False
                redraw = False
                size = os.get_terminal_size(out.fileno()) if out.isatty() else os.terminal_size((120, 34))
                try:
                    canvas = draw(Canvas(size.columns, size.lines, rich), sampler, kiosk)
                    frame = canvas.ansi(full_frame=True)
                except Exception as err:       # a bad reading must never blank the screen
                    canvas, frame = None, f"\x1b[H\x1b[0mMacServer dashboard: {err!r}"
                    if not kiosk:
                        raise
                out.write(sync_on + frame + sync_off)
                out.flush()
            elif canvas is not None and canvas.alert != "ok" and t >= next_pulse:
                # Between full redraws, animate only the screen edge (pulse or flash).
                out.write(sync_on + canvas.ring_ansi(ring_colour(canvas.alert, time.time())) + sync_off)
                out.flush()
                next_pulse = t + 0.12
            wait = min(next_tick, next_draw) - time.monotonic()
            if canvas is not None and canvas.alert != "ok":
                wait = min(wait, next_pulse - time.monotonic())
            wait = max(wait, 0.02)
            if fd is None:
                time.sleep(wait)
                continue
            ready, _, _ = select.select([fd], [], [], wait)
            if ready:
                keys = os.read(fd, 64)
                if not keys:
                    time.sleep(wait)       # input closed: keep drawing
                    continue
                if b" " in keys:           # during an incident: incident view <-> normal dashboard
                    sampler.force_overview = not sampler.force_overview
                    redraw = True
                incident = canvas is not None and canvas.alert == "critical" and not sampler.force_overview
                turn = sampler.turn_page if incident else sampler.turn_home      # the red pages or the normal ones
                if b"n" in keys or b"\t" in keys:        # next / back / hold
                    turn(1)
                    redraw = True
                if b"b" in keys:
                    turn(-1)
                    redraw = True
                if b"p" in keys:
                    sampler.paused = not sampler.paused
                    sampler.page_t = sampler.home_t = time.monotonic()
                    redraw = True
                if kiosk:
                    screen = wanted_screen(keys)
                    if screen:
                        request_screen(screen)
                elif b"q" in keys or b"Q" in keys or b"\x03" in keys:
                    return
    finally:
        out.write("\x1b[?7h\x1b[?25h\x1b[?1049l\x1b[0m")
        out.flush()
        if saved is not None:
            termios.tcsetattr(fd, termios.TCSADRAIN, saved)


def main(argv):
    arg = argv[1] if len(argv) > 1 else ""
    if arg == "--once":
        tty_out = sys.stdout.isatty()
        size = os.get_terminal_size() if tty_out else os.terminal_size((120, 34))
        c = render_once(min(max(size.columns, 80), 200), min(max(size.lines - 2, 24), 40), rich_terminal())
        print(c.ansi() if tty_out else c.text())
    elif arg == "--issues":
        sampler = Sampler()
        time.sleep(0.001)
        sampler.sample()
        print(issues_report(sampler, brief="--brief" in argv[2:]))
    elif arg == "--kiosk":
        while True:
            try:
                run(kiosk=True)
            except Exception:     # never leave the Mac's screen empty
                time.sleep(2)
    elif arg == "":
        run(kiosk=False)
    else:
        print(__doc__.split("\n\n")[1])
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
