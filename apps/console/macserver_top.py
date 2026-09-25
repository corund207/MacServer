#!/usr/bin/env python3
"""MacServer console: a read-only, btop-style status display for the Mac's own screen.

It reads /proc, /sys and the collector's bounded status file. It opens no network
socket, runs no commands, accepts no input in kiosk mode and never writes files.
"""
import argparse
from collections import deque
from datetime import datetime, timezone
import json
import locale
import math
import os
from pathlib import Path
import platform
import signal
import stat
import sys
import time
import unicodedata

STATUS_FILE = "/run/macserver/status.json"
MAX_STATUS = 128 * 1024
STALE_AFTER = 60
HISTORY = 400
STATES = ("healthy", "degraded", "failed", "unavailable")
RANK = {"healthy": 0, "unavailable": 1, "degraded": 2, "failed": 3}
SERVICES = {"db": "PostgreSQL", "auth": "Auth", "rest": "PostgREST", "api-gw": "Envoy",
            "functions": "Functions", "storage": "Storage", "realtime": "Realtime",
            "studio": "Studio", "meta": "Database metadata", "imgproxy": "Image proxy"}
VERDICTS = {"healthy": ("NOMINAL", "The appliance is reporting normally"),
            "degraded": ("ATTENTION", "Some evidence needs attention"),
            "failed": ("ACTION REQUIRED", "A verified service has failed"),
            "unavailable": ("UNVERIFIED", "Appliance state is not fully known")}
VIRTUAL_NET = ("lo", "veth", "docker", "br-", "virbr", "tailscale")
GLYPHS = {
    # Terminal emulators: rounded boxes, braille graphs.
    "unicode": {"box": "╭╮╰╯─│", "graph": "braille", "fill": "■", "empty": "■", "mark": "●", "sep": "·"},
    # Linux VT fonts (Terminus Uni2) reliably carry square box drawing and block elements.
    "console": {"box": "┌┐└┘─│", "graph": "blocks", "fill": "█", "empty": "░", "mark": "▌", "sep": "|"},
}


def clean(value, limit=160):
    """Printable, single-width text only: status data never reaches the tty as control codes."""
    text = str(value)[:limit]
    return "".join(c if c.isprintable() and unicodedata.east_asian_width(c) not in "WF" else "?" for c in text)


def fmt_bytes(value):
    if value is None:
        return "n/a"
    for unit, size in (("TiB", 1024 ** 4), ("GiB", 1024 ** 3), ("MiB", 1024 ** 2), ("KiB", 1024)):
        if value >= size:
            return f"{value / size:.1f} {unit}"
    return f"{value:.0f} B"


def fmt_duration(seconds):
    if seconds is None:
        return "n/a"
    days, rest = divmod(int(seconds), 86400)
    hours, minutes = rest // 3600, rest % 3600 // 60
    return f"{days}d {hours}h" if days else f"{hours}h {minutes}m" if hours else f"{minutes}m"


def level_style(percent):
    return "bad" if percent >= 90 else "warn" if percent >= 70 else "ok"


# --- Host sampling --------------------------------------------------------------------

def parse_cpu(text):
    result = {}
    for line in text.splitlines():
        parts = line.split()
        if parts and parts[0].startswith("cpu"):
            values = [int(v) for v in parts[1:9]]
            idle = values[3] + values[4]
            result[parts[0]] = (idle, sum(values))
    return result


def cpu_percent(previous, current):
    result = {}
    for name, (idle, total) in current.items():
        if name in previous and total > previous[name][1]:
            busy = (total - previous[name][1]) - (idle - previous[name][0])
            result[name] = max(0.0, min(100.0, 100.0 * busy / (total - previous[name][1])))
    return result


def parse_meminfo(text):
    values = {}
    for line in text.splitlines():
        name, _, rest = line.partition(":")
        parts = rest.split()
        if parts and parts[0].isdigit():
            values[name] = int(parts[0]) * 1024
    return values


def parse_net(text):
    rx = tx = 0
    for line in text.splitlines()[2:]:
        name, _, rest = line.partition(":")
        name, fields = name.strip(), rest.split()
        if len(fields) >= 9 and not name.startswith(VIRTUAL_NET):
            rx += int(fields[0]); tx += int(fields[8])
    return rx, tx


class Sampler:
    """Reads host counters under `root` (tests point it at a fixture tree)."""

    def __init__(self, root="/", disk=None):
        self.root = Path(root)
        self.disk = disk or self._statvfs
        self.previous_cpu, self.previous_net = {}, None
        self.cpu = deque(maxlen=HISTORY); self.mem = deque(maxlen=HISTORY)
        self.rx = deque(maxlen=HISTORY); self.tx = deque(maxlen=HISTORY)
        self.cores = {}
        self.latest = {}

    def _read(self, relative):
        try:
            return (self.root / relative).read_text()
        except (OSError, UnicodeDecodeError):
            return None

    @staticmethod
    def _statvfs():
        try:
            info = os.statvfs("/")
            return info.f_blocks * info.f_frsize, info.f_bavail * info.f_frsize
        except (AttributeError, OSError):
            return None

    def temperature(self):
        readings = []
        for sensor in sorted((self.root / "sys/class/hwmon").glob("hwmon*")):
            name = (self._read(sensor.relative_to(self.root) / "name") or "").strip()
            for probe in sorted(sensor.glob("temp*_input")):
                raw = self._read(probe.relative_to(self.root))
                label = (self._read(probe.relative_to(self.root).with_name(probe.name.replace("_input", "_label"))) or "").strip()
                if raw and raw.strip().lstrip("-").isdigit() and 0 < int(raw) / 1000 < 130:
                    readings.append((name, label, int(raw) / 1000))
        package = [v for n, l, v in readings if n == "coretemp" and l.startswith("Package")]
        preferred = package or [v for n, _, v in readings if n in ("coretemp", "applesmc")] or [v for *_, v in readings]
        return max(preferred) if preferred else None

    def power(self):
        ac = battery = status = None
        for supply in sorted((self.root / "sys/class/power_supply").glob("*")):
            relative = supply.relative_to(self.root)
            kind = (self._read(relative / "type") or "").strip()
            if kind == "Mains":
                ac = (self._read(relative / "online") or "").strip() == "1"
            elif kind == "Battery":
                capacity = (self._read(relative / "capacity") or "").strip()
                battery = int(capacity) if capacity.isdigit() else None
                status = clean((self._read(relative / "status") or "").strip(), 20) or None
        return {"ac": ac, "battery": battery, "status": status}

    def sample(self, now):
        cpu_text, mem_text, net_text = self._read("proc/stat"), self._read("proc/meminfo"), self._read("proc/net/dev")
        uptime, load = self._read("proc/uptime"), self._read("proc/loadavg")
        cpu = parse_cpu(cpu_text) if cpu_text else {}
        percents = cpu_percent(self.previous_cpu, cpu); self.previous_cpu = cpu
        self.cpu.append(percents.get("cpu"))
        for name in sorted((n for n in cpu if n != "cpu"), key=lambda n: int(n[3:])):
            self.cores.setdefault(name, deque(maxlen=HISTORY)).append(percents.get(name))
        memory = parse_meminfo(mem_text) if mem_text else {}
        total, available = memory.get("MemTotal"), memory.get("MemAvailable")
        used = total - available if total and available is not None else None
        self.mem.append(100 * used / total if used is not None else None)
        rx_rate = tx_rate = None
        if net_text:
            counters = parse_net(net_text)
            if self.previous_net and now > self.previous_net[0]:
                elapsed = now - self.previous_net[0]
                rx_rate = max(0, counters[0] - self.previous_net[1][0]) / elapsed
                tx_rate = max(0, counters[1] - self.previous_net[1][1]) / elapsed
            self.previous_net = (now, counters)
        self.rx.append(rx_rate); self.tx.append(tx_rate)
        self.latest = {
            "cpu": percents.get("cpu"), "cores": {n: h[-1] for n, h in self.cores.items()},
            "memory": {"total": total, "available": available, "used": used, "cached": memory.get("Cached"),
                       "swap_total": memory.get("SwapTotal"), "swap_free": memory.get("SwapFree")},
            "disk": self.disk(), "rx": rx_rate, "tx": tx_rate, "temperature": self.temperature(),
            "power": self.power(), "uptime": float(uptime.split()[0]) if uptime else None,
            "load": [float(v) for v in load.split()[:3]] if load else None,
        }
        return self.latest


class DemoSampler(Sampler):
    """Synthetic values for previews on development machines. Always labelled DEMO."""

    def __init__(self):
        super().__init__(root="/nonexistent")
        self.tick = 0
        for step in range(HISTORY):
            self._advance(step)

    def _advance(self, step):
        wave = lambda base, swing, speed, phase=0: max(0.0, min(100.0, base + swing * math.sin(step / speed + phase) + swing * .4 * math.sin(step / 2.7 + phase * 3)))
        self.cpu.append(wave(16, 9, 9))
        for index in range(4):
            self.cores.setdefault(f"cpu{index}", deque(maxlen=HISTORY)).append(wave(15 + index * 4, 12, 6 + index, index))
        self.mem.append(wave(41, 1.5, 30))
        self.rx.append(32_000 + 14_000 * math.sin(step / 5) + 6_000 * math.sin(step / 1.7))
        self.tx.append(9_000 + 5_000 * math.sin(step / 7 + 1))

    def sample(self, now):
        self.tick += 1; self._advance(HISTORY + self.tick)
        total = 8 * 1024 ** 3
        self.latest = {"cpu": self.cpu[-1], "cores": {n: h[-1] for n, h in self.cores.items()},
                       "memory": {"total": total, "available": total * (1 - self.mem[-1] / 100), "used": total * self.mem[-1] / 100,
                                  "cached": 1.3 * 1024 ** 3, "swap_total": 2 * 1024 ** 3, "swap_free": 2 * 1024 ** 3},
                       "disk": (233 * 1024 ** 3, 158 * 1024 ** 3), "rx": self.rx[-1], "tx": self.tx[-1],
                       "temperature": 51.0, "power": {"ac": True, "battery": 100, "status": "Full"},
                       "uptime": 24_120 + self.tick, "load": [0.42, 0.38, 0.30]}
        return self.latest


# --- Collector evidence ---------------------------------------------------------------

def unavailable(reason):
    blank = {"state": "unavailable", "detail": reason}
    return {"observed": False, "stale": False, "age": None, "reason": reason, "services": [],
            "tailscale": blank, "backup": {**blank, "lastSuccess": None},
            "requests": {**blank, "perMinute": None, "errorsPerMinute": None}, "alerts": []}


def _item(value, extra=()):
    value = value if isinstance(value, dict) else {}
    state = value.get("state") if value.get("state") in STATES else "unavailable"
    item = {"state": state, "detail": clean(value.get("detail", "No detail")) if isinstance(value.get("detail"), str) else "No detail"}
    for name in extra:
        candidate = value.get(name)
        item[name] = candidate if isinstance(candidate, (int, float, str)) and not isinstance(candidate, bool) else None
    return item


def read_status(path, now):
    """Bounded, symlink-refusing read of the root collector's snapshot."""
    try:
        fd = os.open(path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_NONBLOCK", 0))
    except OSError:
        return unavailable("No collector snapshot")
    try:
        info = os.fstat(fd)
        if not stat.S_ISREG(info.st_mode) or info.st_size > MAX_STATUS or info.st_nlink != 1:
            return unavailable("Unsafe collector snapshot")
        value = json.loads(os.read(fd, MAX_STATUS + 1))
    except (OSError, ValueError):
        return unavailable("Unreadable collector snapshot")
    finally:
        os.close(fd)
    try:
        observed = datetime.fromisoformat(value["observedAt"])
        if value.get("format") != 1 or observed.tzinfo is None or not isinstance(value.get("services"), list):
            raise ValueError
    except (KeyError, TypeError, ValueError):
        return unavailable("Invalid collector snapshot")
    age = now - observed.timestamp()
    if age < -5:
        return unavailable("Collector clock is ahead")
    services = [{"name": SERVICES[row["name"]], **_item(row)} for row in value["services"]
                if isinstance(row, dict) and row.get("name") in SERVICES]
    alerts = [{"severity": row.get("severity") if row.get("severity") in ("critical", "warning", "info") else "warning",
               "title": clean(row.get("title", "Alert"), 80), "detail": clean(row.get("detail", ""), 200)}
              for row in value.get("alerts", [])[:20] if isinstance(row, dict)]
    return {"observed": True, "stale": age > STALE_AFTER, "age": max(0, age), "reason": None, "services": services,
            "tailscale": _item(value.get("tailscale")), "backup": _item(value.get("backup"), ("lastSuccess",)),
            "requests": _item(value.get("requests"), ("perMinute", "errorsPerMinute")), "alerts": alerts}


def verdict(evidence):
    if not evidence["observed"]:
        return "unavailable"
    if evidence["stale"]:
        return "degraded"
    states = [evidence[k]["state"] for k in ("tailscale", "backup", "requests")] + [s["state"] for s in evidence["services"]]
    return max(states, key=RANK.get, default="unavailable")


DEMO_EVIDENCE = {"observed": True, "stale": False, "age": 6, "reason": None,
                 "services": [{"name": SERVICES[k], "state": s, "detail": d} for k, s, d in (
                     ("db", "healthy", "container running, health healthy"), ("auth", "healthy", "container running, health healthy"),
                     ("rest", "healthy", "container running"), ("api-gw", "healthy", "container running, health healthy"),
                     ("realtime", "failed", "container exited"), ("storage", "unavailable", "Container not observed"),
                     ("functions", "unavailable", "Container not observed"), ("studio", "unavailable", "Container not observed"))],
                 "tailscale": {"state": "healthy", "detail": "Daemon running with assigned address"},
                 "backup": {"state": "degraded", "detail": "Restore drill older than 30 days", "lastSuccess": "2026-09-25T03:15:00+00:00"},
                 "requests": {"state": "healthy", "detail": "60 second window", "perMinute": 42, "errorsPerMinute": 0},
                 "alerts": [{"severity": "critical", "title": "Service failure reported", "detail": "realtime"},
                            {"severity": "warning", "title": "Backup degraded", "detail": "Encrypted snapshot verified; isolated restore evidence is older than 30 days."}]}


# --- Rendering ------------------------------------------------------------------------

class Frame:
    """A width x height grid of (character, style) cells; curses-free so it can be tested."""

    def __init__(self, width, height):
        self.width, self.height = width, height
        self.cells = [[(" ", "text") for _ in range(width)] for _ in range(height)]

    def put(self, x, y, text, style="text"):
        if not 0 <= y < self.height:
            return
        for offset, char in enumerate(text):
            if 0 <= x + offset < self.width:
                self.cells[y][x + offset] = (char, style)

    def box(self, x, y, w, h, title, glyphs, right=None, title_style="title"):
        tl, tr, bl, br, horizontal, vertical = glyphs["box"]
        self.put(x, y, tl + horizontal * (w - 2) + tr, "border")
        for row in range(y + 1, y + h - 1):
            self.put(x, row, vertical, "border"); self.put(x + w - 1, row, vertical, "border")
        self.put(x, y + h - 1, bl + horizontal * (w - 2) + br, "border")
        self.put(x + 2, y, f" {title[: w - 6]} ", title_style)
        room = w - len(title) - 9  # Keep the right label clear of the title and corners.
        if right and room >= 4:
            right = right if len(right) <= room else right[: room - 3] + "..."
            self.put(x + w - 3 - len(right), y, f" {right} ", "muted")

    def text(self):
        return "\n".join("".join(c for c, _ in row) for row in self.cells)


def fit(text, width):
    return text if len(text) <= width else text[: max(0, width - 3)].rstrip() + "..." if width > 3 else text[:width]


def wrap(text, width, lines):
    """Word-wrap to at most `lines` lines, ending with an ellipsis when cut."""
    words, result, current = text.split(), [], ""
    for word in words:
        if len(current) + len(word) + (1 if current else 0) <= width:
            current = f"{current} {word}" if current else word
        else:
            result.append(current or word[:width]); current = word if current else ""
    if current:
        result.append(current)
    if len(result) > lines:
        result = result[:lines]; result[-1] = result[-1][: width - 3].rstrip() + "..."
    return [line[:width] for line in result]


def graph(values, width, height, mode):
    """Area graph rows (top first) scaled 0-100; braille packs 2 samples per cell."""
    per_cell, dots = (2, 4) if mode == "braille" else (1, 8)
    samples = list(values)[-width * per_cell:]
    samples = [None] * (width * per_cell - len(samples)) + samples
    levels = [None if v is None else round(max(0, min(100, v)) / 100 * height * dots) for v in samples]
    left_bits, right_bits = (0x40, 0x04, 0x02, 0x01), (0x80, 0x20, 0x10, 0x08)
    rows = []
    for row in range(height):
        base, line = (height - 1 - row) * dots, []
        for cell in range(width):
            if mode == "braille":
                bits = 0
                for column, table in ((0, left_bits), (1, right_bits)):
                    level = levels[cell * 2 + column]
                    for dot in range(max(0, min(4, (level or 0) - base))):
                        bits |= table[dot]
                line.append(chr(0x2800 + bits) if bits else " ")
            else:
                fill = max(0, min(8, (levels[cell] or 0) - base))
                line.append(" ▁▂▃▄▅▆▇█"[fill])
        rows.append("".join(line))
    return rows


def meter(frame, x, y, width, percent, glyphs):
    if width <= 0:
        return
    filled = 0 if percent is None else round(max(0, min(100, percent)) / 100 * width)
    frame.put(x, y, glyphs["fill"] * filled, level_style(percent or 0))
    frame.put(x + filled, y, glyphs["empty"] * (width - filled), "track")


def row(frame, x, y, width, label, value, style="text"):
    frame.put(x, y, label[:width], "muted")
    value = value[: max(0, width - len(label) - 1)]
    frame.put(x + width - len(value), y, value, style)


def render(width, height, snapshot, evidence, history, now, glyph_mode="unicode", demo=False, hostname=""):
    frame, g = Frame(width, height), GLYPHS[glyph_mode]
    if width < 80 or height < 24:
        message = f"MacServer console needs at least 80x24 (now {width}x{height})"
        frame.put(max(0, (width - len(message)) // 2), height // 2, message[:width], "warn")
        return frame
    state = verdict(evidence)
    word, title = VERDICTS[state]
    if evidence["observed"] and evidence["stale"]:
        title = "Collector evidence is stale"

    # Header: verdict, time and provenance.
    clock = datetime.fromtimestamp(now).strftime("%H:%M:%S")
    frame.box(0, 0, width, 3, "MacServer" + (" DEMO DATA" if demo else ""), g, clock, "accent")
    badge = f" {word} "
    frame.put(2, 1, badge, "badge-" + state)
    load = snapshot.get("load")
    facts = [f"up {fmt_duration(snapshot.get('uptime'))}"]
    if load:
        facts.append("load " + " ".join(f"{v:.2f}" for v in load))
    facts.append(f"evidence {int(evidence['age'])}s" if evidence["observed"] else evidence["reason"] or "no evidence")
    info = f" {g['sep']} ".join(facts)
    while len(info) > width - len(badge) - 20 and len(facts) > 1:
        facts.pop(0); info = f" {g['sep']} ".join(facts)
    frame.put(width - 2 - len(info), 1, info, "warn" if evidence["stale"] or not evidence["observed"] else "muted")
    room = width - len(info) - len(badge) - 8
    frame.put(3 + len(badge), 1, title if len(title) <= room else title[: max(0, room - 3)] + "...", "title")

    body_top, body_height = 3, height - 4
    top_height = max(10, body_height * 55 // 100)
    bottom_height = body_height - top_height

    # CPU: total graph with heat colouring plus per-core meters.
    cpu_width = width * 62 // 100
    temperature = snapshot.get("temperature")
    cpu_label = "n/a" if snapshot.get("cpu") is None else f"{snapshot['cpu']:.0f}%"
    frame.box(0, body_top, cpu_width, top_height, "cpu", g, cpu_label + (f" {g['sep']} {temperature:.0f}°C" if temperature else ""))
    inner = cpu_width - 4
    cores = sorted(snapshot.get("cores", {}).items(), key=lambda item: int(item[0][3:]))
    columns = max(1, min(len(cores) or 1, inner // 26))
    core_rows = math.ceil(len(cores) / columns) if cores else 0
    graph_height = top_height - 3 - core_rows
    for index, line in enumerate(graph(history.cpu, inner, graph_height, g["graph"])):
        share = (graph_height - index) / graph_height
        frame.put(2, body_top + 1 + index, line, "bad" if share > .75 else "warn" if share > .5 else "ok")
    cell = inner // columns
    for index, (name, percent) in enumerate(cores):
        x, y = 2 + (index % columns) * cell, body_top + 1 + graph_height + 1 + index // columns
        frame.put(x, y, f"C{name[3:]:<2}", "muted")
        meter(frame, x + 4, y, cell - 11, percent, g)
        frame.put(x + cell - 6, y, "n/a" if percent is None else f"{percent:3.0f}%", "text")

    # Memory, disk and power.
    mem_x, mem_width = cpu_width, width - cpu_width
    memory = snapshot.get("memory", {})
    frame.box(mem_x, body_top, mem_width, top_height, "mem", g, fmt_bytes(memory.get("total")))
    inner, x, y = mem_width - 4, mem_x + 2, body_top + 1
    used_pct = None if not memory.get("total") or memory.get("used") is None else 100 * memory["used"] / memory["total"]
    swap_total = memory.get("swap_total") or 0
    swap_pct = 100 * (swap_total - (memory.get("swap_free") or 0)) / swap_total if swap_total else None
    disk = snapshot.get("disk")
    disk_pct = 100 * (disk[0] - disk[1]) / disk[0] if disk and disk[0] else None
    lines = [("Used", used_pct, fmt_bytes(memory.get("used"))), ("Swap", swap_pct, fmt_bytes(swap_total - (memory.get("swap_free") or 0)) if swap_total else "off"),
             ("Disk /", disk_pct, f"{fmt_bytes(disk[1])} free" if disk else "n/a")]
    limit = body_top + top_height - 1
    for label, percent, detail in lines:
        if y + 1 >= limit:
            break
        row(frame, x, y, inner, label, f"{'n/a' if percent is None else f'{percent:.0f}%'}  {detail}")
        meter(frame, x, y + 1, inner, percent, g); y += 3
    extra = [("Available", fmt_bytes(memory.get("available")), "text"), ("Cached", fmt_bytes(memory.get("cached")), "text")]
    power = snapshot.get("power") or {}
    battery = "no battery" if power.get("battery") is None else f"battery {power['battery']}%" + (f" {power['status'].lower()}" if power.get("status") else "")
    source = "AC" if power.get("ac") else "on battery" if power.get("ac") is False else "power n/a"
    extra.insert(0, ("Power", f"{source} {g['sep']} {battery}", "warn" if power.get("ac") is False else "text"))
    for label, value, style in extra:
        if y >= limit:
            break
        row(frame, x, y, inner, label, value, style); y += 1
    y += 1
    remaining = limit - y
    if remaining >= 2:
        for index, line in enumerate(graph(history.mem, inner, remaining, g["graph"])):
            frame.put(x, y + index, line, "blue")

    # Network: receive and send graphs scaled to the visible peak.
    bottom = body_top + top_height
    net_width = width * 26 // 100
    frame.box(0, bottom, net_width, bottom_height, "net", g, "physical interfaces")
    inner = net_width - 4
    graph_rows = max(1, (bottom_height - 4) // 2)
    labels = ("Receive", "Send") if inner >= 22 else ("rx", "tx")
    y = bottom + 1
    for label, series, value, style in ((labels[0], history.rx, snapshot.get("rx"), "ok"), (labels[1], history.tx, snapshot.get("tx"), "blue")):
        visible = [v for v in list(series)[-inner * 2:] if v is not None]
        peak = max(visible + [1024])
        row(frame, 2, y, inner, label, f"{fmt_bytes(value)}/s" if value is not None else "n/a")
        for index, line in enumerate(graph([None if v is None else 100 * v / peak for v in series], inner, graph_rows, g["graph"])):
            frame.put(2, y + 1 + index, line, style)
        y += graph_rows + 1

    # Services, worst first.
    services_x, services_width = net_width, width * 38 // 100
    services = sorted(evidence["services"], key=lambda s: (-RANK[s["state"]], s["name"]))
    counts = {}
    for service in services:
        counts[service["state"]] = counts.get(service["state"], 0) + 1
    summary = f" {g['sep']} ".join(f"{n} {s}" for s, n in sorted(counts.items(), key=lambda i: -RANK[i[0]])) or "none observed"
    frame.box(services_x, bottom, services_width, bottom_height, "services", g, summary)
    inner = services_width - 4
    if not services:
        frame.put(services_x + 2, bottom + 1, "No service evidence. Unobserved is not healthy."[:inner], "muted")
    name_width = max([len(s["name"]) for s in services] + [8]) + 2
    for index, service in enumerate(services[: bottom_height - 2]):
        y, style = bottom + 1 + index, STATE_STYLE[service["state"]]
        frame.put(services_x + 2, y, g["mark"], style)
        frame.put(services_x + 4, y, service["name"], "text")
        frame.put(services_x + 4 + name_width, y, service["state"], style)
        detail_x = 4 + name_width + 13
        if inner - detail_x > 8:
            frame.put(services_x + detail_x, y, fit(service["detail"], inner - detail_x + 2), "muted")

    # Attention: alerts, then the recovery, network and request facts.
    alert_x = services_x + services_width
    alert_width = width - alert_x
    alerts = ([{"severity": "warning", "title": "Collector evidence is stale", "detail": f"Last snapshot {int(evidence['age'])} s ago."}] if evidence["stale"] else []) + evidence["alerts"]
    if not evidence["observed"]:
        alerts = [{"severity": "warning", "title": "No collector evidence", "detail": evidence["reason"]}]
    frame.box(alert_x, bottom, alert_width, bottom_height, "attention", g, f"{len(alerts)} active")
    inner = alert_width - 4
    facts_top = bottom + bottom_height - 5
    y = bottom + 1
    for alert in alerts:
        if y >= facts_top - 1:
            break
        style = {"critical": "bad", "warning": "warn", "info": "blue"}[alert["severity"]]
        frame.put(alert_x + 2, y, g["mark"], style); frame.put(alert_x + 4, y, fit(alert["title"], inner - 2), "text"); y += 1
        for line in wrap(alert["detail"], inner - 2, 2):
            if y >= facts_top - 1:
                break
            frame.put(alert_x + 4, y, line, "muted"); y += 1
    if not alerts:
        frame.put(alert_x + 2, y, "No active alerts."[:inner], "muted")
    frame.put(alert_x + 1, facts_top, g["box"][4] * (alert_width - 2), "border")
    requests = evidence["requests"]
    rate = "n/a" if requests.get("perMinute") is None else f"{requests['perMinute']}/min {g['sep']} {requests.get('errorsPerMinute') or 0} errors"
    for offset, (label, item, value) in enumerate((("Tailscale", evidence["tailscale"], evidence["tailscale"]["state"]),
                                                   ("Backup", evidence["backup"], evidence["backup"]["state"]),
                                                   ("Requests", requests, rate))):
        row(frame, alert_x + 2, facts_top + 1 + offset, inner, label, value, STATE_STYLE[item["state"]])

    # Footer: the trust boundary, restated.
    footer = f" read-only {g['sep']} no network {g['sep']} no controls" + (f" {g['sep']} DEMO DATA, not this Mac" if demo else "")
    frame.put(0, height - 1, footer, "warn" if demo else "muted")
    host = clean(hostname, 40)
    if width - len(footer) - 3 >= len(host):
        frame.put(width - len(host) - 1, height - 1, host, "muted")
    return frame


STATE_STYLE = {"healthy": "ok", "degraded": "warn", "failed": "bad", "unavailable": "muted"}


# --- Terminal output ------------------------------------------------------------------

def run(args):
    import curses

    locale.setlocale(locale.LC_ALL, "")
    glyph_mode = args.glyphs if args.glyphs != "auto" else ("console" if os.environ.get("TERM") == "linux" else "unicode")
    sampler = DemoSampler() if args.demo else Sampler()
    if args.kiosk:
        for name in ("SIGINT", "SIGQUIT", "SIGTSTP"):
            signal.signal(getattr(signal, name), signal.SIG_IGN)
        if os.environ.get("TERM") == "linux":
            # Keep the always-on display awake: disable VT blanking and powerdown.
            sys.stdout.write("\033[9;0]\033[14;0]"); sys.stdout.flush()

    def main(screen):
        try:
            curses.curs_set(0)
        except curses.error:
            pass  # Some terminals cannot hide the cursor.
        if args.kiosk:
            curses.raw()
        curses.start_color(); curses.use_default_colors()
        rich = curses.COLORS >= 256
        palette = {"text": 252 if rich else curses.COLOR_WHITE, "muted": 245 if rich else curses.COLOR_WHITE,
                   "title": 255 if rich else curses.COLOR_WHITE, "border": 239 if rich else curses.COLOR_BLUE,
                   "track": 237 if rich else curses.COLOR_BLUE, "accent": 149 if rich else curses.COLOR_GREEN,
                   "ok": 149 if rich else curses.COLOR_GREEN, "warn": 221 if rich else curses.COLOR_YELLOW,
                   "bad": 210 if rich else curses.COLOR_RED, "blue": 111 if rich else curses.COLOR_CYAN}
        styles = {}
        for index, (name, colour) in enumerate(palette.items(), start=1):
            curses.init_pair(index, colour, -1)
            styles[name] = curses.color_pair(index) | (curses.A_BOLD if name in ("title", "accent") else 0)
        for index, (state, name) in enumerate(STATE_STYLE.items(), start=len(palette) + 1):
            curses.init_pair(index, 234 if rich else curses.COLOR_BLACK, palette[name])
            styles["badge-" + state] = curses.color_pair(index) | curses.A_BOLD
        screen.timeout(int(args.interval * 1000))
        while True:
            now = time.time()
            snapshot = sampler.sample(now)
            evidence = DEMO_EVIDENCE if args.demo else read_status(args.status, now)
            height, width = screen.getmaxyx()
            frame = render(width, height, snapshot, evidence, sampler, now, glyph_mode, args.demo, platform.node())
            screen.erase()
            for y, cells in enumerate(frame.cells):
                x = 0
                while x < width:
                    style, start = cells[x][1], x
                    while x < width and cells[x][1] == style:
                        x += 1
                    try:
                        screen.addstr(y, start, "".join(c for c, _ in cells[start:x]), styles.get(style, 0))
                    except curses.error:
                        pass  # Writing the bottom-right cell moves the cursor off-screen.
            screen.refresh()
            key = screen.getch()
            if key == curses.KEY_RESIZE:
                curses.update_lines_cols()
            elif not args.kiosk and key in (ord("q"), ord("Q")):
                return

    curses.wrapper(main)


def main():
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--status", default=STATUS_FILE, help="collector snapshot to read")
    parser.add_argument("--interval", type=float, default=1.0, help="seconds between samples (0.5-10)")
    parser.add_argument("--glyphs", choices=("auto", "unicode", "console"), default="auto")
    parser.add_argument("--kiosk", action="store_true", help="ignore all keyboard input (tty1 service)")
    parser.add_argument("--demo", action="store_true", help="synthetic data for previews, labelled DEMO")
    parser.add_argument("--once", metavar="WxH", help="print one plain-text frame and exit")
    args = parser.parse_args()
    if not 0.5 <= args.interval <= 10:
        parser.error("--interval must be between 0.5 and 10")
    if args.once:
        try:
            width, height = (int(v) for v in args.once.lower().split("x"))
        except ValueError:
            parser.error("--once takes WIDTHxHEIGHT, for example 160x50")
        sampler = DemoSampler() if args.demo else Sampler()
        now = time.time(); sampler.sample(now - 1); time.sleep(0 if args.demo else 1); now = time.time()
        evidence = DEMO_EVIDENCE if args.demo else read_status(args.status, now)
        mode = "unicode" if args.glyphs == "auto" else args.glyphs
        print(render(width, height, sampler.sample(now), evidence, sampler, now, mode, args.demo, platform.node()).text())
        return 0
    run(args)
    return 0


if __name__ == "__main__":
    sys.exit(main())
