#!/usr/bin/env python3
"""MacServer dashboard: the always-on screen of the Mac. One steady page, readable from
across the room: anything wrong with the first thing to run, five big readings, every app
and service, connections and recent events.

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
DRAW_S = 1.0          # the screen is redrawn this often, so the clock ticks every second
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
    "bg": "#000000", "panel": "#0c0c0c", "text": "#d0d0d0", "bright": "#ffffff", "dim": "#808080",
    "faint": "#262626", "line": "#3a3a3a",
    "cpu": "#00ff5f", "mem": "#ffd000", "net": "#c040ff", "conn": "#00e5ff", "svc": "#2f8fff", "temp": "#ff3cac",
    "warn": "#ffa000", "ok": "#00ff5f", "bad": "#ff1744", "off": "#5c5c5c", "accent": "#00d0ff", "pill_fg": "#000000",
}.items()}

GRADIENTS = {k: [rgb(x) for x in v] for k, v in {
    "cpu": ["#00c853", "#00ff5f", "#ffd000", "#ff1744"],
    "mem": ["#ff9800", "#ffd000", "#ff6d00", "#ff1744"],
    "temp": ["#2f8fff", "#c040ff", "#ff3cac", "#ff1744"],
    "down": ["#7c1fff", "#c040ff", "#ff6bff"],
    "up": ["#00a3a3", "#00e5ff", "#7df9ff"],
    "bat": ["#ff1744", "#ffd000", "#00ff5f"],
    "disk": ["#0050ff", "#2f8fff", "#66c2ff"],
}.items()}

STATE_COLOUR = {"ok": C["ok"], "warn": C["warn"], "bad": C["bad"], "off": C["off"], "info": C["accent"]}
STATE_TEXT = {"ok": "OK", "warn": "WARN", "bad": "DOWN", "off": "OFF", "info": "INFO"}
ALERT_ORANGE = (255, 160, 0)


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


def gateway(iface):
    """The default gateway's address on this interface (the router), from /proc/net/route."""
    for line in read(PROC / "net/route").splitlines()[1:]:
        parts = line.split()
        if len(parts) > 2 and parts[0] == iface and parts[1] == "00000000":
            try:
                raw = int(parts[2], 16)
            except ValueError:
                return None
            return ".".join(str((raw >> s) & 255) for s in (0, 8, 16, 24))
    return None


def lan_neighbours(iface):
    """Devices the Mac has talked to lately on its own network (the kernel's ARP table): [(ip, mac)], by address.
    Readable without root; only complete entries on this interface."""
    out = []
    for line in read(PROC / "net/arp").splitlines()[1:]:
        parts = line.split()
        if len(parts) >= 6 and parts[5] == iface and parts[2] != "0x0" and parts[3] != "00:00:00:00:00:00":
            out.append((parts[0], parts[3].lower()))
    return sorted(out, key=lambda e: [int(n) if n.isdigit() else 0 for n in e[0].split(".")])


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
        events.add("info", f"{u_new} Debian package update(s) waiting")


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
        self.fans = None
        self.procs = {"cpu": [], "mem": []}     # busiest programs (read every 2 s)
        self.prev_procs, self.prev_procs_t = {}, time.monotonic()
        self.status_hist = collections.defaultdict(lambda: collections.deque(maxlen=HISTORY))   # series read from status.json
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
        self.now["lan"], self.now["gateway"] = lan_neighbours(iface), gateway(iface)
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
        """Processes and the kernel's OOM / heat counters: every 2 s is plenty."""
        cur = read_procs()
        self.procs = group_procs(cur, self.prev_procs, max(t - self.prev_procs_t, 0.001))
        self.prev_procs, self.prev_procs_t = cur, t
        system = ((self.status or {}).get("debug") or {}).get("system") or {}
        for key in ("oom_kills", "thermal_throttle"):
            value = system.get(key)
            if value is None:
                continue
            self.counter_base.setdefault(key, value)
            if value > self.counter_last.get(key, self.counter_base[key]):
                self.counter_changed[key] = time.time()
            self.counter_last[key] = value


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
    line = C["line"] if c.rich else colour
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


# --- assessment ----------------------------------------------------------------------

def alert_level(problems):
    return "critical" if any(p[0] == "bad" for p in problems) else "warning" if problems else "ok"


def public_route_text(route):
    if route.get("up"):
        return "on · Caddy and Cloudflare tunnel running"
    return f"down · tunnel {route.get('tunnel', '?')}, Caddy {route.get('caddy', '?')}"


def debian_row(host):
    """The Debian packages row. Not MacServer's own version (that is the MacServer row): packages waiting are
    information, not a fault. Security fixes install by themselves each morning; the rest wait for an update."""
    total, security = host.get("updates_pending", 0) or 0, host.get("updates_security", 0) or 0
    if not total:
        return ("ok", "Debian", "packages up to date")
    other = total - security
    if not other:
        return ("info", "Debian", f"{security} security fix(es) waiting · installed automatically each morning")
    sec = f" ({security} security, automatic)" if security else ""
    return ("info", "Debian", f"{total} package(s) waiting{sec} · sudo macserver update")


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
    rows.append(debian_row(s.get("host", {}) if s else {}))
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


# --- debug data: processes, sensors, where each service keeps its files ----------------------------

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


def fmt_ago(seconds):
    seconds = max(int(seconds), 0)
    return f"{seconds}s" if seconds < 90 else f"{seconds // 60}m" if seconds < 5400 else f"{seconds // 3600}h"


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
    watch = (sysdata(sampler).get("network") or {}).get("lan_watch")
    if watch:                                            # admin/lan_devices.py: strangers and spoofing on the LAN
        for a in watch.get("alerts") or []:
            add(a.get("level", "warn"), a.get("title", "network watch"), a.get("why", ""), a.get("fix") or [],
                ["journalctl -u macserver-devices -n 50"],
                files=["/etc/macserver/devices-known.json  (your names and trust list)"])
        if watch.get("stale"):
            add("warn", "the network watch is not running", "no update from macserver-devices for over half an hour: "
                "new devices on the network are not being noticed",
                ["systemctl status macserver-devices", "sudo systemctl restart macserver-devices"],
                ["journalctl -u macserver-devices -n 50"])
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


def bottom_bar(c, sampler, kiosk):
    w, y = c.w, c.h - 1
    s = sampler.status or {}
    bg = C["panel"] if c.rich else None
    c.fill(0, y, w, 1, bg)
    name = (s.get("tailscale", {}) or {}).get("name")
    links = ([("admin", f"https://{name}/"), ("studio", f"https://{name}:{STUDIO_PORT}")] if name else [])
    links.append(("guide", DOCS))
    hint = "press 2 to log in · back here: Ctrl+Option+1" if kiosk else "q quit · sudo macserver help"
    x = 1
    for label, url in links:
        if x + len(label) + len(url) + 4 > w - len(hint) - 3:
            break
        x = c.put(x, y, label + " ", C["dim"], bg)
        x = c.put(x, y, url, C["accent"], bg) + 3
    c.put(w - len(hint) - 1, y, hint, C["dim"], bg)


# Panels read status.json's `system` section (admin/collect_status.py) and draw only what they can
# read, so an older collector or a missing source leaves a gap, never an error.


def sysdata(sampler):
    return (sampler.status or {}).get("system") or {}


def level_colour(frac, warn=0.85, bad=0.95):
    """Colour of a how-full bar: calm until it matters."""
    return C["bad"] if frac >= bad else C["warn"] if frac >= warn else C["ok"]


def per_cent(part, whole):
    return 100.0 * part / whole if whole else 0.0


# --- the screen: one page ----------------------------------------------------------------------------------------
# One page with a fixed place for everything, so the eye learns where to look. Nothing turns by itself and there
# are no keys to learn (2 logs in). From the top:
#   needs attention   only when something is wrong: each issue, why, and the first thing to run
#   five metrics      CPU, memory, disk, network, thermal: the reading, a meter, its history and the key figures
#   services          every container by app, with state, CPU, memory and uptime
#   devices           every Tailscale device (address, system, how it connects, last seen, traffic) and every
#                     device the Mac has talked to on its own network lately (address, hardware address)
#   connections       Internet, Tailscale, public API, Claude, clock, updates; then the event log
# Black background, saturated colour for meaning only: green good, amber notice, red broken, and one hue per metric.

def section_title(c, x, y, w, text, note="", colour=None):
    """A row that names a group inside a panel: bold label, then a thin rule, then an optional note."""
    end = c.put(x, y, clip(text.upper(), w), colour or C["accent"], bold=True)
    room = x + w - end - 1
    if note and room > len(note) + 3:
        end = c.put(end + 1, y, note, C["dim"])
        room = x + w - end - 1
    if room > 1:
        c.put(end + 1, y, "─" * room, C["faint"])


def metric_panel(c, x, y, w, h, m):
    """One metric: the reading and its context on the first row, a meter, the history graph, the key figures."""
    box(c, x, y, w, h, m["title"], m["colour"])
    if w < 14 or h < 3:
        return
    ix, iw, bottom = x + 2, w - 4, y + h - 1
    xx = c.put(ix, y + 1, m["value"], m.get("vcolour") or C["bright"], bold=True)
    right = clip(m.get("context", ""), max(ix + iw - xx - 2, 0))
    c.put(ix + iw - len(right), y + 1, right, C["text"])
    row = y + 2
    if m.get("frac") is not None and row < bottom:
        meter(c, ix, row, iw, m["frac"], m.get("grad", "cpu"))
        row += 1
    stats = [s for s in m.get("stats", []) if s][: max(bottom - row - 1, 0)]
    graph_h = bottom - row - len(stats) - (1 if stats else 0)
    if graph_h >= 1 and m.get("series") is not None:
        graph(c, ix, row + 1 if graph_h > 2 else row, iw, graph_h - (1 if graph_h > 2 else 0),
              m["series"], m.get("top") or 100, m.get("grad", "cpu"))
        if m.get("graph_note") and graph_h > 2:
            c.put(ix + iw - len(m["graph_note"]), row, m["graph_note"], C["dim"])
    row = bottom - len(stats)
    for label, value, *colour in stats:
        c.put(ix, row, clip(label, iw), C["dim"])
        text = clip(value, max(iw - len(label) - 1, 0))
        c.put(ix + iw - len(text), row, text, colour[0] if colour else C["text"])
        row += 1


def backup_text(sampler):
    """When the newest backup was made, for the disk panel: (text, colour)."""
    backups = (((sampler.status or {}).get("debug") or {}).get("backups")) or {}
    if backups.get("count") is None:
        return "", C["text"]
    if backup_problem(backups):
        return "needs attention", C["warn"]
    if not backups.get("count"):
        return "none yet", C["warn"]
    return f"{fmt_ago(backups.get('newest_age_s') or 0)} ago", C["text"]


def metrics(sampler):
    """The five metric panels, most important first (a narrow screen keeps the first ones)."""
    now, hist, s = sampler.now, sampler.hist, sampler.status or {}
    sd = sysdata(sampler)
    la = now.get("load") or [0.0, 0.0, 0.0]
    threads = len(now.get("cores") or []) or (sd.get("cpu") or {}).get("threads") or 0
    ghz = (now.get("mhz") or 0) / 1000
    out = [{"title": "cpu", "colour": C["cpu"], "value": f"{now['cpu']:.0f}%", "frac": now["cpu"] / 100, "grad": "cpu",
            "context": f"{threads} threads" + (f" · {ghz:.2f} GHz" if ghz else ""), "series": hist["cpu"], "top": 100,
            "graph_note": "20 min",
            "stats": [("load 1 · 5 · 15 min", f"{la[0]:.2f}  {la[1]:.2f}  {la[2]:.2f}"),
                      ("tasks", f"{now.get('running', 0)} running · {now.get('tasks', 0)} total")]}]
    m = now["mem"]
    avail = m.get("available", m["total"] - m["used"])
    out.append({"title": "memory", "colour": C["mem"], "value": f"{per_cent(m['used'], m['total']):.0f}%",
                "vcolour": level_colour(1 - per_cent(avail, m["total"]) / 100, 0.9, 0.95) if m["total"] else None,
                "frac": m["used"] / m["total"] if m["total"] else 0, "grad": "mem",
                "context": f"{fmt_bytes(m['used'])} of {fmt_bytes(m['total'])}", "series": hist["mem"], "top": 100,
                "stats": [("available", fmt_bytes(avail)), ("swap used", fmt_bytes(m.get("swap_used", 0)))]})
    used, total = now.get("disk", (0, 0))
    btext, bcolour = backup_text(sampler)
    out.append({"title": "disk", "colour": C["svc"], "value": f"{per_cent(used, total):.0f}%",
                "vcolour": level_colour(used / total) if total else None, "frac": used / total if total else 0, "grad": "disk",
                "context": f"{fmt_bytes(used)} of {fmt_bytes(total)}", "series": hist["wr"], "graph_note": "writes",
                "top": nice_top(list(hist["wr"])[-120:], 1_000_000),
                "stats": [("read · write", f"{fmt_bytes(now['rd'], True)} · {fmt_bytes(now['wr'], True)}"),
                          ("last backup", btext or "not set up", bcolour)]})
    ts = s.get("tailscale") or {}
    sig = now.get("signal")
    out.append({"title": "network", "colour": C["net"], "value": f"▼ {fmt_bytes(now['rx'], True)}", "grad": "down",
                "context": f"▲ {fmt_bytes(now['tx'], True)}", "series": hist["rx"], "graph_note": "download",
                "top": nice_top(list(hist["rx"])[-120:], 100_000),
                "stats": [("interface", (sampler.iface or "offline") + (f" · {sig} dBm" if sig is not None else "")),
                          ("tailnet", f"{ts.get('peers_online', 0)} device(s) online" if ts else "-")]})
    temp = now.get("temp")
    fans = now.get("fans") or [f["rpm"] for f in ((sd.get("sensors") or {}).get("fans") or [])]
    stalled = cooling_fault(temp, fans)
    pct, stat, watts = now.get("battery") or (None, None, None)
    out.append({"title": "thermal", "colour": C["temp"], "value": "-" if temp is None else f"{temp:.0f}°C",
                "vcolour": C["bad"] if stalled else None if temp is None else grad("temp", temp_pos(temp)),
                "frac": None if temp is None else min(temp / 105, 1), "grad": "temp",
                "context": f"fan {fans[0]} rpm" if fans else "no fan reading", "series": hist["temp"], "top": 100,
                "stats": [("cooling", "FAN NOT TURNING" if stalled else "normal", C["bad"] if stalled else C["ok"]),
                          ("battery", f"{pct}% {stat or ''}".strip() + (f" · {watts:.1f} W" if watts else "") if pct is not None else "-")]})
    return out


def metrics_row(c, sampler, x, y, w, h):
    panels = metrics(sampler)
    n = max(1, min(len(panels), w // 34 if w >= 68 else 1))
    for m, (cx, cw) in zip(panels[:n], columns(x, w, [1] * n)):
        metric_panel(c, cx, y, cw, h, m)


def issue_rows(item):
    """The lines for one issue on the screen: the first fix for a notice, up to three for a critical one."""
    fixes = item["fix"][:3 if item["level"] == "bad" else 1]
    return [("fix" if k == 0 else "", f"{k + 1}) {cmd}" if len(fixes) > 1 else cmd) for k, cmd in enumerate(fixes)]


def issues_need(items):
    return 2 + sum(1 + len(issue_rows(i)) for i in items)


def issues_panel(c, sampler, x, y, w, h, items):
    bad = sum(1 for i in items if i["level"] == "bad")
    box(c, x, y, w, h, "needs attention", C["bad"] if bad else C["warn"], "details and fixes: macserver issues" if w >= 60 else "")
    if h < 3 or w < 14:
        return
    row, bottom, inner = y + 1, y + h - 1, w - 4
    for n, item in enumerate(items):
        rows = issue_rows(item)
        if row >= bottom or (row == bottom - 1 and n < len(items) - 1):
            c.put(x + 2, bottom - 1, clip(f"+{len(items) - n} more: macserver issues", inner).ljust(inner), C["dim"])
            return
        xx = pill(c, x + 2, row, item["level"])
        line(c, xx + 2, row, x + w - 2 - xx - 2, (item["title"], C["bright"], True),
             (("   " + item["why"]) if item["why"] else "", C["dim"]))
        row += 1
        for label, text in rows:
            if row >= bottom or (row == bottom - 1 and n < len(items) - 1):
                break
            c.put(x + 4, row, f"{label:<4}", C["dim"])
            line(c, x + 9, row, inner - 7, (text, C["accent"]))
            row += 1


def service_state(item):
    if item.get("state") != "running":
        return "bad"
    health = item.get("health") or ("unhealthy" if "unhealthy" in item.get("status", "") else "")
    return "warn" if health == "unhealthy" else "ok"


def service_groups(sampler):
    """[(app, [containers])] by compose project, the biggest app first; stopped and unhealthy services lead their app."""
    groups = {}
    for item in list((sampler.status or {}).get("containers", []) or []):
        project = item.get("project") or "-"
        short = item["name"].split(".")[-1]
        for prefix in ("supabase-", f"{project}-"):
            short = short[len(prefix):] if short.startswith(prefix) else short
        groups.setdefault(project, []).append(dict(item, short=re.sub(r"-\d+$", "", short)))
    out = []
    for project, members in sorted(groups.items(), key=lambda kv: (-len(kv[1]), kv[0])):
        members.sort(key=lambda i: (service_state(i) == "ok", i["short"]))
        out.append((project[len("macserver-"):] if project.startswith("macserver-") else project, members))
    return out


def service_blocks(sampler):
    """One block of lines per app: its title, then its containers. ("app", name, "up/total", state) or ("svc", container)."""
    out = []
    for name, members in service_groups(sampler):
        up = sum(1 for m in members if service_state(m) == "ok")
        out.append([("app", name, f"{up}/{len(members)}", "ok" if up == len(members) else "bad")] + [("svc", m) for m in members])
    return out


def pack_columns(blocks, ncols, rows):
    """Lay the apps into columns of `rows` lines, breaking only between apps; an app too long for one column
    goes on in the next under its title again. None when they do not all fit."""
    if rows < 2:                                   # no room for a title and one service
        return None
    cols, cur = [], []
    for block in blocks:
        if cur and len(cur) + len(block) > rows:
            cols.append(cur)
            cur = []
        while len(block) > rows:
            cols.append(block[:rows])
            block = [block[0]] + block[rows:]
        cur += block
    if cur:
        cols.append(cur)
    return cols if len(cols) <= ncols else None


SVC_COL_W = 50     # the narrowest column of the services list: dot, name, state, CPU, memory


def service_columns(col_w):
    """The services table's columns for this width: [(title, width, align)]; the name takes what is left."""
    cols = [("", 2, "l"), ("NAME", None, "l"), ("STATE", 10, "l"), ("CPU", 7, "r"), ("MEM", 9, "r")]
    for extra in (("UP", 7, "r"), ("RESTARTS", 10, "r"), ("NET IN", 11, "r"), ("NET OUT", 11, "r")):
        if col_w - sum(wd for _, wd, _ in cols if wd) - extra[1] >= 24:
            cols.append(extra)
    name_w = col_w - sum(wd for _, wd, _ in cols if wd)
    return [(t, wd if wd else name_w, al) for t, wd, al in cols]


def service_cells(item):
    state = service_state(item)
    running = item.get("state") == "running"
    cpu, mem, started = item.get("cpu"), item.get("mem"), item.get("started")
    colour = STATE_COLOUR[state]
    return {"": ("●", colour),
            "NAME": (item["short"], C["bright"] if state == "ok" else colour, state != "ok"),
            "STATE": (item.get("state", "?") if state != "warn" else "unhealthy", C["dim"] if state == "ok" else colour),
            "CPU": (f"{cpu:.1f}%" if cpu is not None else "-", C["text"]),
            "MEM": (fmt_bytes(mem) if mem is not None else "-", C["text"]),
            "UP": (fmt_duration(int(max(time.time() - started, 0))).split()[0] if running and started else "-", C["dim"]),
            "RESTARTS": (str(item.get("restarts", "-")), C["warn"] if item.get("restarts") else C["dim"]),
            "NET IN": (fmt_bytes(item.get("net_rx", 0), True) if running else "-", C["net"]),
            "NET OUT": (fmt_bytes(item.get("net_tx", 0), True) if running else "-", C["conn"])}


def table_row(c, x, y, cols, cells, gap=1):
    """One row of fixed columns; right-aligned columns keep their numbers lined up."""
    for title, wd, align in cols:
        text, colour, *bold = cells.get(title, ("", C["text"]))
        text = clip(str(text), wd - gap) if wd > gap else ""
        c.put(x, y, text.rjust(wd - gap) if align == "r" else text, colour, bold=bool(bold and bold[0]))
        x += wd


def services_panel(c, sampler, x, y, w, h):
    items = list((sampler.status or {}).get("containers", []) or [])
    up = sum(1 for i in items if service_state(i) == "ok")
    box(c, x, y, w, h, "services", C["svc"], f"{up}/{len(items)} running" if items else "")
    if h < 3 or w < 14:
        return
    if not items:
        line(c, x + 2, y + 1, w - 4, ("Supabase is installed by setup. Nothing to show yet.", C["dim"]))
        return
    blocks = service_blocks(sampler)
    inner, rows = w - 4, h - 3
    fit_cols = max(1, (inner + 3) // (SVC_COL_W + 3))
    layout = None
    for ncols in range(1, fit_cols + 1):          # as few columns as fit: wide columns carry more detail
        layout = pack_columns(blocks, ncols, rows)
        if layout:
            break
    hidden = 0
    if not layout:                                 # more than the panel holds: fill it, then count the rest
        layout = (pack_columns(blocks, 10 ** 6, rows) or [])
        hidden = sum(1 for col in layout[fit_cols:] for e in col if e[0] == "svc")
        layout = layout[:fit_cols]
        if hidden and layout:
            layout[-1] = layout[-1][:rows - 1]
    ncols = max(len(layout), 1)
    col_w = (inner - 3 * (ncols - 1)) // ncols
    cols = service_columns(col_w)
    for k, entries in enumerate(layout):
        cx = x + 2 + k * (col_w + 3)
        table_row(c, cx, y + 1, cols, {t: (t, C["dim"], True) for t, _, _ in cols})
        for n, entry in enumerate(entries):
            if entry[0] == "app":
                _, name, count, state = entry
                section_title(c, cx, y + 2 + n, col_w, name, count, C["bad"] if state == "bad" else C["accent"])
            else:
                table_row(c, cx, y + 2 + n, cols, service_cells(entry[1]))
    if hidden:
        line(c, x + 2 + (ncols - 1) * (col_w + 3), y + h - 2, col_w, (f"… {hidden} more: sudo macserver status", C["dim"]))


def services_need(sampler, w):
    """Rows the services panel wants at this width (frame and column titles included)."""
    blocks = service_blocks(sampler)
    total = sum(len(b) for b in blocks)
    fit_cols = max(1, (w - 4 + 3) // (SVC_COL_W + 3))
    for rows in range(max(-(-total // fit_cols), 1), total + 1):
        if pack_columns(blocks, fit_cols, rows):
            return rows + 3
    return total + 3


def os_name(os_):
    return {"windows": "Windows", "macOS": "macOS", "iOS": "iOS", "android": "Android", "linux": "Linux"}.get(os_, os_ or "-")


def device_lines(sampler):
    """The devices panel's lines: ("head", title, note) or ("dev", cells, state)."""
    now = time.time()
    peers = (sysdata(sampler).get("tailscale") or {}).get("peers") or []
    out = []
    if peers:
        online = sum(1 for p in peers if p.get("online"))
        out.append(("head", "tailnet", f"{online} of {len(peers)} online"))
        for p in peers:
            seen = ("now" if p.get("online") else
                    f"{fmt_duration(int(max(now - p['last_seen'], 0))).split()[0]} ago" if p.get("last_seen") else "-")
            traffic = f"▼{fmt_bytes(p.get('rx_bytes', 0))} ▲{fmt_bytes(p.get('tx_bytes', 0))}" if p.get("rx_bytes") or p.get("tx_bytes") else ""
            kind = "phone" if p.get("os") in ("iOS", "android") else "computer"
            route = p.get("route") or "-"
            out.append(("dev", (p.get("name") or "?", p.get("ip") or "-", kind, os_name(p.get("os")), seen,
                                f"{route} {traffic}".strip() if p.get("online") else ""),
                        "ok" if p.get("online") else "off"))
    known = (sysdata(sampler).get("network") or {}).get("neighbours")
    if known is None:                                   # no collector data: the screen's own look at the ARP table
        gw = sampler.now.get("gateway")
        known = [{"ip": ip, "mac": mac, "kind": "router" if ip == gw else "private MAC" if int(mac[:2], 16) & 2 else "device"}
                 for ip, mac in sampler.now.get("lan") or []]
    if known:
        watch = (sysdata(sampler).get("network") or {}).get("lan_watch") or {}
        new = sum(1 for d in known if d.get("new"))
        note = f"{len(known)} on {sampler.iface or watch.get('iface') or '?'}" + (f" · {new} new" if new else "")
        if watch and not watch.get("capture"):
            note += " · not listening"
        out.append(("head", "local network", note))
        for d in known:
            seen = d.get("seen")
            age = now - seen if seen else None
            when = "recent" if age is None else "now" if age < 900 else f"{fmt_duration(int(age)).split()[0]} ago"
            private = d.get("kind") == "private MAC" or int(d["mac"][:2], 16) & 2
            what = d.get("model") if d.get("model") and d.get("model") != d.get("name") else ""
            detail = " · ".join(x for x in (what, (d.get("maker") or ("private address" if private else "")).split(",")[0]) if x)
            risky = any(r.get("level") == "bad" and r.get("what") == "open" for r in d.get("risks") or [])
            flag = "NEW " if d.get("new") else ""
            out.append(("dev", (d.get("name") or d["mac"], d["ip"], d.get("kind") or "device", d.get("system") or "-", when,
                                flag + detail), "bad" if risky else "warn" if d.get("new") else "ok"))
    return out


def devices_panel(c, sampler, x, y, w, h):
    lines = device_lines(sampler)
    count = sum(1 for e in lines if e[0] == "dev")
    box(c, x, y, w, h, "devices", C["conn"], f"{count} known" if count else "")
    if h < 3 or w < 14:
        return
    if not lines:
        line(c, x + 2, y + 1, w - 4, ("No devices yet: Tailscale and the Wi-Fi report them here.", C["dim"]))
        return
    ix, right = x + 2, x + w - 2
    # The DEVICE column is as wide as the longest name (the full name is the point); the other columns
    # narrow first, then DETAIL and SEEN go, before a name is cut.
    inner = w - 6
    longest = max([len(e[1][0]) for e in lines if e[0] == "dev"] + [12])
    name_w = min(longest + 1, max(22, inner * 2 // 5))
    if inner >= name_w + 16 + 22 + 20 + 9 + 16:
        widths = [name_w, 16, 22, 20, 9, None]
    elif inner >= name_w + 16 + 16 + 14 + 9 + 8:
        widths = [name_w, 16, 16, 14, 9, None]
    elif inner >= name_w + 16 + 16 + 14 + 9:
        widths = [name_w, 16, 16, 14, 9, 0]
    elif inner >= name_w + 16 + 18 + 10:
        widths = [name_w, 16, 18, None, 0, 0]
    else:
        widths = [max(min(longest + 1, inner - 16 - 10), 12), 16, None, 0, 0, 0]
    head = ("DEVICE", "ADDRESS", "TYPE", "SYSTEM", "SEEN", "DETAIL")
    fields(c, ix, y + 1, right, ("", C["dim"], 2), *[(t, C["dim"], wd, True) for t, wd in zip(head, widths) if wd != 0])
    room = h - 3
    for n, entry in enumerate(lines[:room]):
        row = y + 2 + n
        if n == room - 1 and len(lines) > room:
            line(c, ix, row, w - 4, (f"… {len(lines) - n} more", C["dim"]))
            break
        if entry[0] == "head":
            section_title(c, ix, row, w - 4, entry[1], entry[2])
            continue
        cells, state = entry[1], entry[2]
        last = C["ok"] if cells[5].startswith("direct") else C["warn"] if cells[5].startswith("NEW") else C["dim"]
        colours = (C["bright"], C["accent"], C["text"], C["text"], C["dim"], last)
        fields(c, ix, row, right, ("●", STATE_COLOUR[state], 2),
               *[(text, colour if state != "off" else C["dim"], wd) for text, colour, wd in zip(cells, colours, widths) if wd != 0])


def devices_need(sampler):
    return 3 + max(len(device_lines(sampler)), 1)


def conn_need(sampler):
    name = ((sampler.status or {}).get("tailscale", {}) or {}).get("name")
    return len(connections(sampler)) + 2 + (3 if name else 0)


def page(c, sampler, items, x, y, w, h):
    """The one page: issues, metrics, services beside devices, then connections beside events."""
    if items:
        ih = min(issues_need(items), max(h * 40 // 100, 3))
        issues_panel(c, sampler, x, y, w, ih, items)
        y, h = y + ih, h - ih
    if h < 3:
        return
    setup = not (sampler.status or {}).get("setup_done")
    metric_h = 14 if h >= 48 else 11 if h >= 36 else 8 if h >= 24 else 0
    if metric_h == 14 and w >= 120:                # spare rows make the graphs taller, up to 22
        want = max(5 if setup else services_need(sampler, w * 55 // 100), devices_need(sampler))
        metric_h = min(max(h - want - max(conn_need(sampler), 12), 14), 22)
    if metric_h:
        metrics_row(c, sampler, x, y, w, metric_h)
        y, h = y + metric_h, h - metric_h
    if h < 3:
        return
    left = setup_panel if setup else services_panel
    if w >= 120:
        svc_w = w * 55 // 100
        want = max(5 if setup else services_need(sampler, svc_w), devices_need(sampler))
        mid_h = max(min(want, h - max(conn_need(sampler), 8)), min(h, 8))
        low_h = h - mid_h
        left(c, sampler, x, y, svc_w, mid_h)
        devices_panel(c, sampler, x + svc_w, y, w - svc_w, mid_h)
        conn_w = w * 40 // 100
        conn_panel(c, sampler, x, y + mid_h, conn_w, low_h)
        events_panel(c, sampler, x + conn_w, y + mid_h, w - conn_w, low_h)
    else:
        sv, dv, cn = fit(y, h, [5 if setup else services_need(sampler, w), devices_need(sampler), conn_need(sampler)], grow=0)
        left(c, sampler, x, sv[0], w, sv[1])
        devices_panel(c, sampler, x, dv[0], w, dv[1])
        conn_panel(c, sampler, x, cn[0], w, cn[1])


def draw(c, sampler, kiosk=True):
    """The whole screen for a canvas of any size (80x24 up to 250x80)."""
    counted = counted_issues(sampler)
    label = [(i["level"], i["title"]) for i in counted]
    level = alert_level(label)
    c.alert = level
    if c.rich:
        c.fill(0, 0, c.w, c.h, C["bg"])
    top_bar(c, sampler, label)
    bottom_bar(c, sampler, kiosk)
    page(c, sampler, counted, 0, 1, c.w, c.h - 2)
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
        sync_on, sync_off = ("\x1b[?2026h", "\x1b[?2026l") if rich else ("", "")
        while True:
            t = time.monotonic()
            if t >= next_tick or t >= next_draw or resized[0]:
                if t >= next_tick:
                    sampler.sample()
                    next_tick = t + SAMPLE_S
                next_draw = t + DRAW_S
                if resized[0]:
                    out.write("\x1b[2J")
                    resized[0] = False
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
