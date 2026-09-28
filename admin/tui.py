#!/usr/bin/env python3
"""MacServer dashboard: the always-on screen of the Mac.

    dashboard            live, full screen; q quits
    dashboard --once     print a summary once (shown at login)
    dashboard --kiosk    always-on mode for tty1: ignores every key, never exits

Read-only and unprivileged: live numbers come from /proc and /sys, everything else
from /run/macserver/status.json (written every 30 s by the root collector; it holds
no secrets). Only characters present in the Mac's console font (Lat15 Terminus) are
drawn; tests/test_dashboard.py checks that against the font.
"""
import collections
import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import time

STATUS = Path(os.environ.get("MACSERVER_STATUS", "/run/macserver/status.json"))
PROC = Path(os.environ.get("MACSERVER_PROC", "/proc"))
SYS = Path(os.environ.get("MACSERVER_SYS", "/sys"))
DOCS = "github.com/corund207/MacServer"
STUDIO_PORT = 8443
SAMPLE_S = 2          # how often live numbers are read
BUCKET_S = 10         # one chart column = 10 seconds
HISTORY = 720         # chart columns kept (2 hours)

# Every character the dashboard draws besides printable ASCII. All are in the
# Lat15-Terminus console font (checked by tests/test_dashboard.py).
GLYPHS = "─│╭╮╰╯├┤█▒░●✗·°↑↓…"

# Styles: (foreground, bold). Colours are the 8 of the Linux console; bold = bright.
BLACK, RED, GREEN, YELLOW, BLUE, MAGENTA, CYAN, WHITE = range(8)
NORMAL, VALUE, MUTED, FRAME, TITLE, GOOD, WARN, BAD, ACCENT, HEADER, PURPLE = range(11)
STYLES = {NORMAL: (WHITE, False), VALUE: (WHITE, True), MUTED: (WHITE, False),
          FRAME: (BLUE, True), TITLE: (CYAN, True), GOOD: (GREEN, True), WARN: (YELLOW, True),
          BAD: (RED, True), ACCENT: (CYAN, False), HEADER: (BLACK, False), PURPLE: (MAGENTA, True)}
HEADER_BG = CYAN
BADGES = {"ok": ("  OK  ", GREEN), "warn": (" WARN ", YELLOW), "bad": (" DOWN ", RED), "off": (" OFF  ", BLUE)}


# --- reading the machine -----------------------------------------------------------

def read(path, default=""):
    try:
        return Path(path).read_text()
    except OSError:
        return default


def cpu_times():
    """(busy, total) jiffies from /proc/stat."""
    fields = [int(x) for x in read(PROC / "stat", "cpu 0 0 0 0").splitlines()[0].split()[1:]]
    idle = fields[3] + (fields[4] if len(fields) > 4 else 0)
    return sum(fields) - idle, sum(fields)


def memory():
    info = {}
    for line in read(PROC / "meminfo").splitlines():
        name, _, rest = line.partition(":")
        if rest.split():
            info[name] = int(rest.split()[0]) * 1024
    total = info.get("MemTotal", 0)
    return total - info.get("MemAvailable", total), total


def disk():
    try:
        st = os.statvfs(os.environ.get("MACSERVER_ROOT", "/"))
    except OSError:
        return 0, 0
    total = st.f_blocks * st.f_frsize
    return total - st.f_bavail * st.f_frsize, total


def sensors():
    """Hottest CPU temperature (C) and fan speeds (rpm)."""
    temps = collections.defaultdict(list)
    fans = []
    for mon in sorted((SYS / "class/hwmon").glob("hwmon*")):
        name = read(mon / "name").strip()
        for t in mon.glob("temp*_input"):
            try:
                value = int(read(t, "0")) / 1000
            except ValueError:
                continue
            if 0 < value < 125:
                temps[name].append(value)
        for f in sorted(mon.glob("fan*_input")):
            try:
                fans.append(int(read(f, "0")))
            except ValueError:
                pass
    # The CPU package sensor is the one that matters; others are fallbacks.
    for name in ("coretemp", "k10temp", "acpitz", "applesmc"):
        if temps.get(name):
            return max(temps[name]), fans
    return None, fans


def battery():
    for supply in sorted((SYS / "class/power_supply").glob("*")):
        if read(supply / "type").strip() == "Battery":
            cap = read(supply / "capacity").strip()
            return (int(cap) if cap.isdigit() else None), read(supply / "status").strip() or "?"
    return None, None


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


def load_status():
    try:
        data = json.loads(STATUS.read_text())
    except (OSError, ValueError):
        return None
    data["age_s"] = time.time() - data.get("generated_at", 0)
    return data


def firstboot_state():
    try:
        return subprocess.run(["systemctl", "show", "-p", "ActiveState", "--value",
                               "macserver-firstboot.service"], capture_output=True, text=True,
                              timeout=5).stdout.strip()
    except (OSError, subprocess.TimeoutExpired):
        return ""


class Sampler:
    """Live numbers plus chart history (averages per BUCKET_S)."""

    def __init__(self):
        self.prev_cpu = cpu_times()
        self.iface = default_iface()
        self.prev_net = net_bytes(self.iface)
        self.prev_t = time.monotonic()
        self.hist = {k: collections.deque(maxlen=HISTORY) for k in ("cpu", "mem", "temp", "rx", "tx", "bat")}
        self.bucket = collections.defaultdict(list)
        self.bucket_start = time.monotonic()
        self.now = {"cpu": 0.0, "rx": 0.0, "tx": 0.0}
        self.status = None
        self.status_read = 0.0
        self.firstboot = ""

    def sample(self):
        t = time.monotonic()
        dt = max(t - self.prev_t, 0.001)
        busy, total = cpu_times()
        pb, pt = self.prev_cpu
        self.now["cpu"] = 100.0 * (busy - pb) / (total - pt) if total > pt else 0.0
        self.prev_cpu = (busy, total)
        iface = default_iface()
        rx, tx = net_bytes(iface)
        if iface == self.iface and rx >= self.prev_net[0] and tx >= self.prev_net[1]:
            self.now["rx"], self.now["tx"] = (rx - self.prev_net[0]) / dt, (tx - self.prev_net[1]) / dt
        self.iface, self.prev_net, self.prev_t = iface, (rx, tx), t
        self.now["mem"] = memory()
        self.now["disk"] = disk()
        self.now["temp"], self.now["fans"] = sensors()
        self.now["battery"] = battery()
        self.now["signal"] = wifi_signal(iface)
        self.now["uptime"] = uptime_s()
        for key in ("cpu", "rx", "tx"):
            self.bucket[key].append(self.now[key])
        if self.now["temp"] is not None:
            self.bucket["temp"].append(self.now["temp"])
        used, total = self.now["mem"]
        if total:
            self.bucket["mem"].append(100.0 * used / total)
        if self.now["battery"][0] is not None:
            self.bucket["bat"].append(self.now["battery"][0])
        if t - self.bucket_start >= BUCKET_S:
            for key, values in self.bucket.items():
                if values:
                    self.hist[key].append(sum(values) / len(values))
            self.bucket.clear()
            self.bucket_start = t
        if t - self.status_read >= 5 or self.status is None:
            self.status = load_status()
            self.firstboot = firstboot_state()
            self.status_read = t


# --- drawing -----------------------------------------------------------------------

class Canvas:
    def __init__(self, w, h):
        self.w, self.h = w, h
        self.cells = [[(" ", NORMAL)] * w for _ in range(h)]
        self.bg = [[None] * w for _ in range(h)]   # background colour, None = screen default

    def put(self, x, y, text, style=NORMAL, bg=None):
        if not 0 <= y < self.h:
            return x
        for ch in str(text):
            if 0 <= x < self.w:
                self.cells[y][x] = (ch, style)
                self.bg[y][x] = bg
            x += 1
        return x

    def badge(self, x, y, state):
        """A coloured status tag, readable from across the room."""
        text, colour = BADGES.get(state, BADGES["off"])
        return self.put(x, y, text, HEADER, bg=colour)

    def box(self, x, y, w, h, title="", title_style=TITLE, note="", note_style=MUTED):
        if w < 4 or h < 2:
            return
        self.put(x, y, "╭" + "─" * (w - 2) + "╮", FRAME)
        for row in range(y + 1, y + h - 1):
            self.put(x, row, "│", FRAME)
            self.put(x + w - 1, row, "│", FRAME)
        self.put(x, y + h - 1, "╰" + "─" * (w - 2) + "╯", FRAME)
        if title:
            end = self.put(x + 2, y, f" {title} ", title_style)
            if note:
                self.put(end, y, f"{note} ", note_style)

    def text(self):
        return "\n".join("".join(ch for ch, _ in row).rstrip() for row in self.cells)

    def ansi(self):
        out = []
        for y, row in enumerate(self.cells):
            line, last = [], None
            for x, (ch, style) in enumerate(row):
                key = (style, self.bg[y][x])
                if key != last:
                    fg, bold = STYLES[style]
                    code = f"\x1b[0;{30 + fg}{';1' if bold else ''}{f';{40 + key[1]}' if key[1] is not None else ''}m"
                    line.append(code)
                    last = key
                line.append(ch)
            out.append("".join(line).rstrip() + "\x1b[0m")
        while out and out[-1] == "\x1b[0m":
            out.pop()
        return "\n".join(out)


def fmt_bytes(n, rate=False):
    if n is None:
        return "?"
    units = ["B", "KB", "MB", "GB", "TB"]
    i = 0
    while n >= 1000 and i < len(units) - 1:
        n /= 1000
        i += 1
    s = f"{n:.0f} {units[i]}" if i == 0 or n >= 100 else f"{n:.1f} {units[i]}"
    return s + ("/s" if rate else "")


def clip(text, width):
    """Shorten to width characters, marking the cut with …"""
    if width <= 0:
        return ""
    return text if len(text) <= width else text[: width - 1] + "…"


def fmt_duration(s):
    d, h, m = s // 86400, s % 86400 // 3600, s % 3600 // 60
    return f"{d}d {h}h" if d else f"{h}h {m}m" if h else f"{m}m"


def level_style(fraction, warn=0.75, bad=0.9):
    return BAD if fraction >= bad else WARN if fraction >= warn else GOOD


def bar(c, x, y, width, fraction, style):
    """Horizontal bar: █ filled, ░ empty."""
    fraction = min(max(fraction or 0.0, 0.0), 1.0)
    filled = round(fraction * width)
    c.put(x, y, "█" * filled, style)
    c.put(x + filled, y, "░" * (width - filled), FRAME)


def column_chart(c, x, y, width, height, values, top, style):
    """Area chart, newest on the right: a bright top edge (█ full cell, ▒ half cell)
    over a light ░ fill, so steady values read as a line, not a slab."""
    values = list(values)[-width:]
    offset = width - len(values)
    for row in range(height):
        c.put(x, y + row, "·" * offset, FRAME)
    for i, v in enumerate(values):
        units = round(min(max(v / top if top else 0, 0), 1) * height * 2)
        if v > 0 and units == 0:
            units = 1
        edge = (units - 1) // 2          # row (from the bottom) holding the top edge
        for row in range(height):
            from_bottom = height - 1 - row
            if units and from_bottom == edge:
                ch = "█" if units - from_bottom * 2 >= 2 else "▒"
            elif units and from_bottom < edge:
                ch = "░"
            else:
                ch = " "
            c.put(x + offset + i, y + row, ch, style if ch != " " else NORMAL)


def dot(c, x, y, state):
    """● green (ok), yellow (warn), red (bad), blue (off/unknown)."""
    return c.put(x, y, "●", {"ok": GOOD, "warn": WARN, "bad": BAD}.get(state, FRAME))


def nice_top(values, minimum):
    top = max(list(values) + [minimum])
    for exp in range(0, 13):
        for step in (1, 2, 5):
            cand = step * 10 ** exp
            if cand >= top:
                return cand
    return top


# --- the screen ---------------------------------------------------------------------

def assess(sampler):
    """Overall state and the list of problems, most important first."""
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


def draw(c, sampler, kiosk=True):
    s = sampler.status or {}
    now = sampler.now
    w, h = c.w, c.h
    ts = s.get("tailscale", {}) if s else {}
    name = ts.get("name") or "macserver"
    problems = assess(sampler)

    # Header bar
    c.put(0, 0, " " * w, HEADER, bg=HEADER_BG)
    x = c.put(1, 0, "MACSERVER", HEADER, bg=HEADER_BG)
    c.put(x + 2, 0, name, HEADER, bg=HEADER_BG)
    worst = "bad" if any(p[0] == "bad" for p in problems) else "warn" if problems else "ok"
    label = {"ok": " ALL SYSTEMS NORMAL ", "warn": f" {len(problems)} NOTICE(S) ",
             "bad": f" {len(problems)} ISSUE(S) "}[worst]
    clock = time.strftime("%a %d %b  %H:%M")
    rx = max(w - len(label) - len(clock) - 4, x + 2 + len(name) + 2)
    c.put(rx, 0, label, HEADER, bg={"ok": GREEN, "warn": YELLOW, "bad": RED}[worst])
    c.put(rx + len(label) + 2, 0, clock, HEADER, bg=HEADER_BG)

    y = 2
    # Setup banner / problems line
    setup_done = bool(s.get("setup_done")) if s else False
    if not setup_done:
        running = sampler.firstboot == "activating"
        c.box(0, y, w, 4, "SETUP", WARN)
        if running:
            c.put(2, y + 1, "Setup is running. It continues by itself (about 15 minutes).", WARN)
            c.put(2, y + 2, "If a QR code appears on this screen, scan it with your phone to join Tailscale.", NORMAL)
        else:
            c.put(2, y + 1, "Setup has not finished. Log in (Ctrl + Option + F2) and run:", WARN)
            c.put(2, y + 2, "sudo /opt/macserver-src/install.sh", VALUE)
            c.put(40, y + 2, "   It continues where it stopped.", MUTED)
        y += 5
    elif problems:
        x = c.put(1, y, "Needs attention: ", WARN)
        for state, text in problems[:3]:
            x = dot(c, x, y, state)
            x = c.put(x + 1, y, text + "   ", VALUE)
        y += 2

    # SYSTEM and CONNECTIONS side by side (stacked on narrow screens)
    wide = w >= 100
    left_w = w // 2 if wide else w
    right_x = left_w if wide else 0
    right_w = w - left_w if wide else w
    sys_h = 8
    c.box(0, y, left_w, sys_h, "SYSTEM", note=f"up {fmt_duration(now.get('uptime', 0))}")
    bar_w = max(left_w - 32, 6)
    rows = []
    rows.append(("CPU", now["cpu"] / 100, f"{now['cpu']:3.0f}%"))
    used, total = now.get("mem", (0, 0))
    rows.append(("MEMORY", used / total if total else 0, f"{fmt_bytes(used)} / {fmt_bytes(total)}"))
    used, total = now.get("disk", (0, 0))
    rows.append(("DISK", used / total if total else 0, f"{fmt_bytes(used)} / {fmt_bytes(total)}"))
    temp, fans = now.get("temp"), now.get("fans") or []
    fan = f"  fan {fans[0]}" if fans else ""
    rows.append(("TEMP", (temp or 0) / 100, f"{temp:.0f}°C{fan}" if temp is not None else "unknown"))
    pct, bstat = now.get("battery", (None, None))
    rows.append(("BATTERY", (pct or 0) / 100, f"{pct}% {bstat.lower()}" if pct is not None else "none"))
    for i, (label, frac, value) in enumerate(rows):
        ry = y + 1 + i
        c.put(2, ry, f"{label:<8}", MUTED)
        if label == "BATTERY":
            style = BAD if frac < 0.2 else WARN if frac < 0.4 else GOOD
        elif label == "TEMP":
            style = level_style(frac, 0.8, 0.9)
        else:
            style = level_style(frac)
        bar(c, 11, ry, bar_w, frac, style)
        c.put(12 + bar_w, ry, value, VALUE)

    cy = y if wide else y + sys_h
    c.box(right_x, cy, right_w, sys_h, "CONNECTIONS")
    conns = []
    iface = sampler.iface
    if iface:
        sig = now.get("signal")
        kind = "Wi-Fi" if sig is not None or iface.startswith("wl") else "cable/USB"
        extra = f"  signal {sig} dBm" if sig is not None else ""
        conns.append(("ok" if sig is None or sig > -75 else "warn", "Internet", f"{kind} ({iface}){extra}"))
    else:
        conns.append(("bad", "Internet", "not connected"))
    tstate = ts.get("state", "unknown") if s else "unknown"
    peers = ts.get("peers_online")
    if not s:
        conns.append(("off", "Tailscale", "not set up yet"))
    else:
        conns.append(("ok" if tstate == "Running" else "bad", "Tailscale",
                      f"{tstate}" + (f" · {peers} device(s) online" if peers is not None and tstate == "Running" else "")))
    dom = s.get("public_domain", "") if s else ""
    if dom:
        pok = s.get("public_ok")
        conns.append(("ok" if pok else "bad" if pok is False else "warn", "Public API",
                      f"{dom}  {'answering' if pok else 'not answering' if pok is False else 'checking'}"))
    else:
        conns.append(("off", "Public API", "off (tailnet only)"))
    cl = s.get("claude", {}) if s else {}
    if cl.get("installed"):
        active = cl.get("state") == "active"
        conns.append(("ok" if active else "off", "Claude",
                      "session running · open it at claude.ai/code" if active else "start a session from the admin page"))
    synced = s.get("clock_synced") if s else None
    conns.append(("ok" if synced else "warn" if synced is False else "off", "Clock",
                  time.strftime("%H:%M:%S") + ("  synced" if synced else "  not synced" if synced is False else "")))
    upd = s.get("host", {}).get("updates_pending", 0) if s else 0
    conns.append(("warn" if upd else "ok", "Updates",
                  f"{upd} pending: sudo macserver update" if upd else "up to date"))
    for i, (state, label, value) in enumerate(conns[: sys_h - 2]):
        ry = cy + 1 + i
        c.badge(right_x + 2, ry, state)
        c.put(right_x + 9, ry, f"{label:<11}", MUTED)
        c.put(right_x + 21, ry, clip(value, right_w - 23), VALUE)
    y = cy + sys_h

    # SUPABASE services
    containers = s.get("containers", []) if s else []
    up = sum(1 for x in containers if x.get("state") == "running")
    items = [(("ok" if x.get("state") == "running" else "bad"),
              x["name"].split(".")[-1].replace("supabase-", "").replace("macserver-", ""), x.get("state"))
             for x in containers]
    lines, line, lw = [], [], 2
    for item in items:
        need = len(item[1]) + 4 + (0 if item[0] == "ok" else len(item[2]) + 3)   # "● name  " [" (state)"]
        if line and lw + need > w - 2:
            lines.append(line)
            line, lw = [], 2
        line.append(item)
        lw += need
    if line:
        lines.append(line)
    footer_h = 3
    chart_room = h - y - footer_h - (len(lines) or 1) - 2
    sup_h = (len(lines) or 1) + 2
    note = f"{up}/{len(containers)} running" if containers else "not installed yet"
    c.box(0, y, w, sup_h, "SUPABASE", note=note, note_style=GOOD if containers and up == len(containers) else WARN)
    if not lines:
        c.put(2, y + 1, "Installed by setup. Nothing to show yet.", MUTED)
    for i, line in enumerate(lines):
        x = 2
        for state, label, cstate in line:
            x = dot(c, x, y + 1 + i, state)
            x = c.put(x + 1, y + 1 + i, label, VALUE if state == "ok" else BAD)
            if state != "ok":
                x = c.put(x, y + 1 + i, f" ({cstate})", BAD)
            x += 2
    y += sup_h

    # ACTIVITY charts, if there is room
    if chart_room >= 5:
        n = 3 if w >= 120 else 2 if w >= 70 else 1
        chart_rows = 2 if chart_room >= 2 * 8 else 1
        ch_h = min(chart_room // chart_rows - 2, 14)
        cw = w // n
        hist = sampler.hist
        minutes = max((cw - 4) * BUCKET_S // 60, 1)
        temp = now.get("temp")
        pct = (now.get("battery") or (None, None))[0]
        charts = {
            "cpu": ("CPU", hist["cpu"], 100, ACCENT, f"{now['cpu']:.0f}% now", "%"),
            "mem": ("MEMORY", hist["mem"], 100, GOOD, f"{hist['mem'][-1]:.0f}% used" if hist["mem"] else "", "%"),
            "temp": ("TEMPERATURE", hist["temp"], 100, WARN, f"{temp:.0f}°C" if temp is not None else "", "°C"),
            "rx": ("NETWORK IN", hist["rx"], nice_top(hist["rx"], 10_000), GOOD, f"↓ {fmt_bytes(now['rx'], True)}", "B/s"),
            "tx": ("NETWORK OUT", hist["tx"], nice_top(hist["tx"], 10_000), PURPLE, f"↑ {fmt_bytes(now['tx'], True)}", "B/s"),
            "bat": ("BATTERY", hist["bat"], 100, GOOD, f"{pct}%" if pct is not None else "none", "%"),
        }
        layout = {3: [["cpu", "mem", "temp"], ["rx", "tx", "bat"]],
                  2: [["cpu", "rx"], ["mem", "tx"]], 1: [["cpu"], ["rx"]]}[n][:chart_rows]
        for keys in layout:
            for i, key in enumerate(keys):
                title, values, top, style, note, unit = charts[key]
                bx = i * cw
                bw = cw if i < n - 1 else w - bx
                c.box(bx, y, bw, ch_h + 2, title, note=note, note_style=VALUE)
                column_chart(c, bx + 2, y + 1, bw - 4, ch_h, values, top, style)
                scale = f"top {fmt_bytes(top, True)}" if unit == "B/s" else f"top {top}{unit}"
                c.put(bx + bw - len(scale) - 4, y + ch_h + 1, f" {scale} ", MUTED)
                c.put(bx + 2, y + ch_h + 1, f" last {minutes} min ", MUTED)
            y += ch_h + 2

    # Footer: where to connect, how to log in
    fy = h - 2
    links = [("Admin ", f"https://{name}/"), ("Studio / API ", f"https://{name}:{STUDIO_PORT}")] \
        if s and ts.get("name") else []
    links.append(("Guide ", DOCS))
    x = 1
    for label, url in links:            # only what fits, in order of importance
        if x + len(label) + len(url) > w - 1:
            break
        x = c.put(x, fy, label, MUTED)
        x = c.put(x, fy, url, ACCENT) + 3
    if kiosk:
        x = c.put(1, fy + 1, "Log in: ", MUTED)
        x = c.put(x, fy + 1, "Ctrl + Option + F2", VALUE)
        c.put(x, fy + 1, clip("  (hold fn too if the brightness changes) · back here: Ctrl + Option + F1", w - x - 1), MUTED)
    else:
        x = c.put(1, fy + 1, "q", VALUE)
        c.put(x, fy + 1, clip(" quit  ·  commands: sudo macserver help  ·  app keys: sudo macserver keys", w - x - 1), MUTED)
    return c


def render_once(width=100, height=None):
    sampler = Sampler()
    time.sleep(0.3)
    sampler.sample()
    c = Canvas(width, height or 26)
    return draw(c, sampler, kiosk=False)


# --- curses front end ---------------------------------------------------------------

def run(kiosk):
    import curses
    import locale
    locale.setlocale(locale.LC_ALL, "")
    if locale.getpreferredencoding(False).lower().replace("-", "") != "utf8":
        try:   # the charts need UTF-8 even if the login has no locale
            locale.setlocale(locale.LC_ALL, "C.UTF-8")
        except locale.Error:
            pass
    if os.environ.get("TERM") == "linux":
        # Keep the screen on: no console blanking, no power-down.
        sys.stdout.write("\x1b[9;0]\x1b[14;0]")
        sys.stdout.flush()
    if kiosk:
        for sig in (signal.SIGINT, signal.SIGQUIT, signal.SIGTSTP):
            signal.signal(sig, signal.SIG_IGN)
    sampler = Sampler()

    def main(scr):
        curses.curs_set(0)
        if kiosk:
            curses.raw()
        curses.start_color()
        try:
            curses.use_default_colors()
            default_bg = -1
        except curses.error:
            default_bg = BLACK
        pairs = {}

        def attr(style, bg):
            key = (style, bg)
            if key not in pairs:
                fg, bold = STYLES[style]
                idx = len(pairs) + 1
                curses.init_pair(idx, fg, default_bg if bg is None else bg)
                pairs[key] = curses.color_pair(idx) | (curses.A_BOLD if bold else 0)
            return pairs[key]
        scr.timeout(1000)
        last = 0.0
        while True:
            t = time.monotonic()
            if t - last >= SAMPLE_S:
                sampler.sample()
                last = t
                h, w = scr.getmaxyx()
                c = draw(Canvas(w, h), sampler, kiosk)
                scr.erase()
                for y in range(h):
                    for x in range(w):
                        if y == h - 1 and x == w - 1:
                            continue   # curses cannot write the last cell
                        ch, style = c.cells[y][x]
                        if ch != " " or c.bg[y][x] is not None:
                            try:
                                scr.addstr(y, x, ch, attr(style, c.bg[y][x]))
                            except curses.error:
                                pass
                scr.refresh()
            key = scr.getch()
            if key == curses.KEY_RESIZE:
                last = 0.0
            elif not kiosk and key in (ord("q"), ord("Q")):
                return

    while True:
        try:
            curses.wrapper(main)
            return
        except Exception:     # never leave tty1 empty: redraw after any glitch
            if not kiosk:
                raise
            time.sleep(2)


def main(argv):
    arg = argv[1] if len(argv) > 1 else ""
    if arg == "--once":
        cols = os.get_terminal_size().columns if sys.stdout.isatty() else 100
        c = render_once(min(max(cols, 80), 140))
        print(c.ansi() if sys.stdout.isatty() else c.text())
    elif arg == "--kiosk":
        run(kiosk=True)
    elif arg == "":
        run(kiosk=False)
    else:
        print(__doc__.split("\n\n")[1])
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
