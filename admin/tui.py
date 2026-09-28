#!/usr/bin/env python3
"""MacServer dashboard: the always-on screen of the Mac, in the style of btop.

    dashboard            live, full screen; q quits
    dashboard --once     print a summary once (shown at login)
    dashboard --kiosk    always-on mode for tty1: ignores every key, never exits

Two looks from one layout:
  rich     in a modern terminal (the Mac runs `foot` in the `cage` kiosk): 24-bit
           colour gradients and braille graphs (2x4 dots per character).
  console  on the bare Linux console (TERM=linux, the fallback): 16 colours and
           only characters in the Lat15-Terminus font (tests check this).

Read-only and unprivileged: live numbers come from /proc and /sys every second;
everything else from /run/macserver/status.json (written every 30 s by the root
collector; it holds no secrets).
"""
import collections
import json
import os
from pathlib import Path
import re
import select
import signal
import subprocess
import sys
import time

STATUS = Path(os.environ.get("MACSERVER_STATUS", "/run/macserver/status.json"))
PROC = Path(os.environ.get("MACSERVER_PROC", "/proc"))
SYS = Path(os.environ.get("MACSERVER_SYS", "/sys"))
DOCS = "github.com/corund207/MacServer"
STUDIO_PORT = 8443
SAMPLE_S = 1.0
HISTORY = 1200        # samples kept per graph (20 minutes at one per second)

# Characters drawn in console mode besides printable ASCII: all exist in the
# Lat15-Terminus console font (tests/test_dashboard.py checks it against the font).
GLYPHS = "─│╭╮╰╯├┤┬┴█▒░■●✗·°↑↓…▲▼"
# Rich mode may also use these (the terminal draws braille itself).
RICH_EXTRA = "◆⏵"


# --- theme ---------------------------------------------------------------------------

def rgb(h):
    return int(h[1:3], 16), int(h[3:5], 16), int(h[5:7], 16)


C = {k: rgb(v) for k, v in {
    "bg": "#0b0e14", "panel": "#0f131b", "text": "#c9d1d9", "bright": "#f0f6fc", "dim": "#7d8590",
    "faint": "#2d333b", "line": "#262c36",
    "cpu": "#3fb950", "mem": "#e3b341", "net": "#a371f7", "conn": "#39c5cf", "svc": "#58a6ff", "warn": "#d29922",
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


class Sampler:
    """Live numbers once per second, with history for the graphs."""

    def __init__(self):
        self.model, _, self.core_of = cpu_info()
        self.prev_cpu = cpu_times()
        self.iface = default_iface()
        self.prev_net = net_bytes(self.iface)
        self.prev_io = disk_io()
        self.prev_t = time.monotonic()
        self.hist = collections.defaultdict(lambda: collections.deque(maxlen=HISTORY))
        self.now = {"cpu": 0.0, "cores": [], "rx": 0.0, "tx": 0.0, "rd": 0.0, "wr": 0.0}
        self.status = None
        self.status_read = -1e9
        self.firstboot = ""

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
        rd, wr = disk_io()
        if rd >= self.prev_io[0] and wr >= self.prev_io[1]:
            self.now["rd"], self.now["wr"] = (rd - self.prev_io[0]) / dt, (wr - self.prev_io[1]) / dt
        self.prev_io = (rd, wr)
        self.prev_t = t
        _, self.now["mhz"], _ = cpu_info()
        self.now["mem"] = memory()
        self.now["disk"] = disk()
        self.now["temp"], self.now["core_temps"], self.now["fans"] = sensors()
        self.now["battery"] = battery()
        self.now["signal"] = wifi_signal(iface)
        self.now["uptime"] = uptime_s()
        self.now["load"] = loadavg()
        m = self.now["mem"]
        h = self.hist
        h["cpu"].append(self.now["cpu"])
        for i, v in enumerate(self.now["cores"]):
            h[f"core{i}"].append(v)
        h["mem"].append(100.0 * m["used"] / m["total"] if m["total"] else 0)
        h["rx"].append(self.now["rx"])
        h["tx"].append(self.now["tx"])
        h["io"].append(self.now["rd"] + self.now["wr"])
        if self.now["temp"] is not None:
            h["temp"].append(self.now["temp"])
        if t - self.status_read >= 5:
            self.status = load_status()
            self.firstboot = firstboot_state()
            self.status_read = t


# --- canvas --------------------------------------------------------------------------

class Canvas:
    """A grid of cells: (character, foreground RGB, background RGB or None, bold)."""

    def __init__(self, w, h, rich=True):
        self.w, self.h, self.rich = w, h, rich
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
                    if self.rich:
                        sgr = f"0;{'1;' if bold else ''}38;2;{fg[0]};{fg[1]};{fg[2]}"
                        sgr += f";48;2;{bg[0]};{bg[1]};{bg[2]}" if bg else ""
                    else:
                        i = nearest16(fg)
                        sgr = f"0;{'1;' if bold or i >= 8 else ''}{30 + i % 8}"
                        sgr += f";{40 + nearest16(bg, True)}" if bg else ""
                    line.append(f"\x1b[{sgr}m")
                    last = key
                line.append(ch)
            out.append("".join(line) + "\x1b[0m")
        return ("" if full_frame else "\n").join(out)


# --- widgets -------------------------------------------------------------------------

def clip(text, width):
    if width <= 0:
        return ""
    return text if len(text) <= width else text[: width - 1] + "…"


def fmt_bytes(n, rate=False):
    if n is None:
        return "?"
    units = ["B", "KB", "MB", "GB", "TB"]
    i = 0
    n = float(n)
    while n >= 1000 and i < len(units) - 1:
        n /= 1000
        i += 1
    s = f"{n:.0f} {units[i]}" if i == 0 or n >= 100 else f"{n:.1f} {units[i]}"
    return s + ("/s" if rate else "")


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

def assess(sampler):
    s, now, problems = sampler.status or {}, sampler.now, []
    if not s:
        if sampler.firstboot == "activating":
            problems.append(("warn", "setup is running"))
        else:
            problems.append(("bad", "no health data (setup not finished, or the collector stopped)"))
    else:
        if not s.get("setup_done"):
            problems.append(("warn", "setup has not finished"))
        down = [x["name"] for x in s.get("containers", []) if x.get("state") != "running"]
        if down:
            problems.append(("bad", f"{len(down)} service(s) stopped"))
        if s.get("tailscale", {}).get("state") not in ("Running", None):
            problems.append(("bad", "Tailscale is not connected"))
        if s.get("age_s", 0) > 120:
            problems.append(("warn", "health data is old"))
        if s.get("public_domain") and s.get("public_ok") is False:
            problems.append(("bad", "public API not answering"))
        if s.get("host", {}).get("reboot_required"):
            problems.append(("warn", "restart needed for updates"))
    if not sampler.iface:
        problems.append(("bad", "no network connection"))
    temp = now.get("temp")
    if temp is not None and temp >= 90:
        problems.append(("warn", f"running hot ({temp:.0f}°C)"))
    used, total = now.get("disk", (0, 0))
    if total and used / total > 0.9:
        problems.append(("warn", "disk almost full"))
    return problems


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
    if dom:
        pok = s.get("public_ok")
        rows.append(("ok" if pok else "bad" if pok is False else "warn", "Public API",
                     f"{dom} · {'answering' if pok else 'not answering' if pok is False else 'checking'}"))
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
    return rows


# --- panels --------------------------------------------------------------------------

def top_bar(c, sampler, problems):
    w, now, s = c.w, sampler.now, sampler.status or {}
    name = (s.get("tailscale", {}) or {}).get("name") or "macserver"
    c.fill(0, 0, w, 1, C["panel"] if c.rich else None)
    x = c.put(1, 0, "◆ ", C["accent"], C["panel"] if c.rich else None)
    x = c.put(x, 0, "MACSERVER", C["bright"], C["panel"] if c.rich else None, bold=True)
    x = c.put(x + 2, 0, name, C["dim"], C["panel"] if c.rich else None)
    worst = "bad" if any(p[0] == "bad" for p in problems) else "warn" if problems else "ok"
    label = {"ok": " ALL SYSTEMS NORMAL ", "warn": f" {len(problems)} NOTICE(S) ",
             "bad": f" {len(problems)} ISSUE(S) "}[worst]
    right = []
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
    room = gh - 2                              # rows for the lines, above load/fan
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
            c.put(xx + 1, row, f"{t:3.0f}°C", grad("temp", (t - 30) / 70))
        graph_rows = per - 2 if per >= 3 else per - 1
        if n > 0 and graph_rows > 0 and c.rich:
            graph(c, sx + 5, row + 1, side_w - 6, graph_rows, hist, 100, "cpu")
        row += rows_here
    if row < y + h - 1:
        la = now.get("load") or [0, 0, 0]
        fans = now.get("fans") or []
        info = f"load {la[0]:.2f} {la[1]:.2f} {la[2]:.2f}" + (f"   fan {fans[0]} rpm" if fans else "")
        c.put(sx + 1, y + h - 2, clip(info, side_w - 1), C["dim"])


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
    if row < y + h - 1:
        io = f"disk read {fmt_bytes(sampler.now['rd'], True)}  write {fmt_bytes(sampler.now['wr'], True)}"
        c.put(x + 2, row, clip(io, inner - 2), C["dim"])
        row += 1
    gh = y + h - 1 - row
    if gh >= 2:
        graph(c, x + 1, row, inner, gh, sampler.hist["mem"], 100, "mem")


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


def services_panel(c, sampler, x, y, w, h):
    s = sampler.status or {}
    items = list(s.get("containers", []) if s else [])
    up = sum(1 for i in items if i.get("state") == "running")
    note = f"{up}/{len(items)} running" if items else ""
    box(c, x, y, w, h, "services", C["svc"], note)
    if not items:
        c.put(x + 2, y + 1, "Supabase is installed by setup. Nothing to show yet.", C["dim"])
        return
    items.sort(key=lambda i: (i.get("state") == "running", -(i.get("cpu") or 0), i.get("name", "")))
    for item in items:   # "realtime-dev.supabase-realtime" -> "realtime"; the group column says supabase
        short = item["name"].split(".")[-1]
        for prefix in ("supabase-", f"{item.get('project') or '-'}-"):
            short = short[len(prefix):] if short.startswith(prefix) else short
        item["short"] = re.sub(r"-\d+$", "", short)
    name_w = min(max(len(i["short"]) for i in items) + 4, 30)
    cpu_top = nice_top([i.get("cpu") or 0 for i in items], 5)
    cols = [("SERVICE", name_w), ("GROUP", 12), ("STATE", 11), (f"CPU (bar = {cpu_top}%)", 24), ("MEMORY", 11)]
    fixed = sum(cw for _, cw in cols)
    status_w = max(w - 4 - fixed, 0)
    xx = x + 2
    for title, cw in cols + [("STATUS", status_w)]:
        c.put(xx, y + 1, title[:cw], C["dim"], bold=True)
        xx += cw
    max_rows = h - 3
    shown = items if len(items) <= max_rows else items[: max_rows - 1]
    for n, item in enumerate(shown):
        row = y + 2 + n
        running = item.get("state") == "running"
        state = "ok" if running else "bad"
        healthy = "unhealthy" not in item.get("status", "")
        if running and not healthy:
            state = "warn"
        xx = x + 2
        dot(c, xx, row, state)
        c.put(xx + 2, row, clip(item["short"], name_w - 3), C["bright"] if running else C["bad"])
        xx += name_w
        c.put(xx, row, clip(item.get("project") or "-", 11), C["dim"])
        xx += 12
        c.put(xx, row, item.get("state", "?"), STATE_COLOUR[state])
        xx += 11
        cpu = item.get("cpu")
        if cpu is not None:
            meter(c, xx, row, 14, min(cpu / cpu_top, 1), "cpu")
            c.put(xx + 15, row, f"{cpu:5.1f}%", C["text"])
        else:
            c.put(xx, row, "-", C["dim"])
        xx += 24
        c.put(xx, row, fmt_bytes(item.get("mem")) if item.get("mem") is not None else "-", C["text"])
        xx += 11
        c.put(xx, row, clip(item.get("status", ""), status_w), C["dim"])
    if len(shown) < len(items):
        c.put(x + 2, y + 2 + len(shown), f"… {len(items) - len(shown)} more", C["dim"])


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


def draw(c, sampler, kiosk=True):
    """Lay out every panel for a canvas of any size (80x24 up to 250x80)."""
    w, h = c.w, c.h
    if c.rich:
        c.fill(0, 0, w, h, C["bg"])
    problems = assess(sampler)
    top_bar(c, sampler, problems)
    bottom_bar(c, sampler, kiosk)
    avail = h - 2
    y = 1
    s = sampler.status or {}
    setup = not s.get("setup_done")
    cpu_h = min(max(round(avail * 0.38), 7), 26)
    mid_h = min(max(round(avail * 0.28), 8), 20)
    if avail - cpu_h - mid_h < 6:           # small screens: shrink the graphs first
        cpu_h = max(avail - mid_h - 6, 5)
    cpu_panel(c, sampler, 0, y, w, cpu_h)
    y += cpu_h
    wide = w >= 140
    if wide:
        mw, nw = w * 30 // 100, w * 36 // 100
        mem_panel(c, sampler, 0, y, mw, mid_h)
        net_panel(c, sampler, mw, y, nw, mid_h)
        conn_panel(c, sampler, mw + nw, y, w - mw - nw, mid_h)
        y += mid_h
        bottom_h = avail + 1 - y
        (setup_panel if setup else services_panel)(c, sampler, 0, y, w, bottom_h)
    else:
        mw = w // 2
        mem_panel(c, sampler, 0, y, mw, mid_h)
        net_panel(c, sampler, mw, y, w - mw, mid_h)
        y += mid_h
        bottom_h = avail + 1 - y
        cw = min(max(w * 42 // 100, 36), w // 2) if w >= 90 else 0
        if cw:
            conn_panel(c, sampler, 0, y, cw, bottom_h)
        (setup_panel if setup else services_panel)(c, sampler, cw, y, w - cw, bottom_h)
    return c


def render_once(width=120, height=34, rich=True):
    sampler = Sampler()
    time.sleep(0.5)
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
        next_tick = 0.0
        while True:
            t = time.monotonic()
            if t >= next_tick or resized[0]:
                if t >= next_tick:
                    sampler.sample()
                    next_tick = t + SAMPLE_S
                if resized[0]:
                    out.write("\x1b[2J")
                    resized[0] = False
                size = os.get_terminal_size(out.fileno()) if out.isatty() else os.terminal_size((120, 34))
                try:
                    frame = draw(Canvas(size.columns, size.lines, rich), sampler, kiosk).ansi(full_frame=True)
                except Exception as err:       # a bad reading must never blank the screen
                    frame = f"\x1b[H\x1b[0mMacServer dashboard: {err!r}"
                    if not kiosk:
                        raise
                out.write(("\x1b[?2026h" if rich else "") + frame + ("\x1b[?2026l" if rich else ""))
                out.flush()
            wait = max(next_tick - time.monotonic(), 0.05)
            if fd is None:
                time.sleep(wait)
                continue
            ready, _, _ = select.select([fd], [], [], wait)
            if ready:
                keys = os.read(fd, 64)
                if not keys:
                    time.sleep(wait)       # input closed: keep drawing
                elif kiosk:
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
