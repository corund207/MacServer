#!/usr/bin/env python3
"""MacServer fan control: the Mac's fans never run below FAN_MIN_PCT (default 100%:
always full speed) of their top speed, and rise towards 100% with CPU load and heat.

Runs as root from macserver-fans.service. It sets the Apple SMC fans (T2 Macs:
…/APP0001:00/fanN_manual and fanN_output) every 2 seconds and writes what it did to
/run/macserver/fans.json for the dashboard. Without a temperature reading it runs
the fans at full speed; when it stops, the Mac's automatic control takes over.

Settings in /etc/macserver/macserver.conf:
  FAN_MIN_PCT=100    lowest fan speed, percent of the fan's maximum (30..100)
  FAN_MODE=curve     curve = this controller; auto = leave the fans to the Mac
"""
import json
import os
from pathlib import Path
import signal
import sys
import time

SYS = Path(os.environ.get("MACSERVER_SYS", "/sys"))
PROC = Path(os.environ.get("MACSERVER_PROC", "/proc"))
CONF = Path(os.environ.get("MACSERVER_CONF", "/etc/macserver/macserver.conf"))
OUT = Path(os.environ.get("MACSERVER_FANS", "/run/macserver/fans.json"))
INTERVAL = 2.0
TEMP_LOW, TEMP_HIGH, TEMP_PANIC = 50.0, 90.0, 95.0
FALL_PER_STEP = 3.0       # percent points the target may drop per step (it may rise at once)


def read(path, default=""):
    try:
        return Path(path).read_text().strip()
    except OSError:
        return default


def conf():
    values = {}
    for line in read(CONF).splitlines():
        key, _, value = line.partition("=")
        if key.strip() and not key.startswith("#"):
            values[key.strip()] = value.strip()
    return values


def smc():
    """The directory holding the Apple SMC fan controls, or None. On T2 Macs they sit on
    the SMC's ACPI device (…/APP0001:00/fan1_*, where t2fanrd finds them); on older
    Macs on the applesmc hwmon device."""
    for pattern in ("devices/pci*/*/*/*/APP0001:00", "devices/pci*/*/*/APP0001:00",
                    "devices/pci*/*/*/*/*/APP0001:00", "bus/acpi/devices/APP0001:00",
                    "devices/platform/applesmc.768"):
        for path in sorted(SYS.glob(pattern)):
            if list(path.glob("fan*_output")):
                return path
    for mon in sorted((SYS / "class/hwmon").glob("hwmon*")):
        if read(mon / "name") == "applesmc" and list(mon.glob("fan*_output")):
            return mon
    return None


def fans(mon):
    """[(n, min_rpm, max_rpm)] for every fan the SMC reports."""
    out = []
    for f in sorted(mon.glob("fan*_input")):
        n = f.name[3:].split("_")[0]
        try:
            lo, hi = int(read(mon / f"fan{n}_min", "0")), int(read(mon / f"fan{n}_max", "0"))
        except ValueError:
            continue
        if hi > 0:
            out.append((n, lo, hi))
    return out


def cpu_temp():
    """Hottest CPU temperature in C, or None."""
    temps = []
    for mon in sorted((SYS / "class/hwmon").glob("hwmon*")):
        if read(mon / "name") in ("coretemp", "k10temp"):
            for t in mon.glob("temp*_input"):
                try:
                    value = int(read(t, "0")) / 1000
                except ValueError:
                    continue
                if 0 < value < 125:
                    temps.append(value)
    return max(temps) if temps else None


def cpu_busy():
    """(busy, total) jiffies."""
    fields = [int(x) for x in (read(PROC / "stat", "cpu 0 0 0 0").splitlines() or ["cpu 0 0 0 0"])[0].split()[1:]]
    idle = fields[3] + (fields[4] if len(fields) > 4 else 0)
    return sum(fields) - idle, sum(fields)


def demand(load, temp):
    """0..1: how hard to cool. The higher of CPU load and temperature decides."""
    t = 0.0 if temp is None else (temp - TEMP_LOW) / (TEMP_HIGH - TEMP_LOW)
    return min(max(max(load, t), 0.0), 1.0)


def target_pct(min_pct, load, temp, previous=None):
    """Fan speed in percent of maximum: at least min_pct, 100 at high load or heat.
    Rises at once; falls by at most FALL_PER_STEP per step, so the noise changes gently."""
    if temp is not None and temp >= TEMP_PANIC:
        return 100.0
    want = min_pct + (100.0 - min_pct) * demand(load, temp)
    if previous is not None and want < previous:
        want = max(want, previous - FALL_PER_STEP)
    return round(want, 1)


def rpm_for(pct, lo, hi):
    return max(lo, min(hi, round(hi * pct / 100.0)))


def write(path, value):
    Path(path).write_text(f"{value}\n")


def set_auto(mon):
    for n, _, _ in fans(mon) if mon else []:
        try:
            write(mon / f"fan{n}_manual", 0)
        except OSError:
            pass


def report(data):
    try:
        OUT.parent.mkdir(parents=True, exist_ok=True)
        tmp = OUT.with_suffix(".tmp")
        tmp.write_text(json.dumps(data))
        os.chmod(tmp, 0o644)
        os.replace(tmp, OUT)
    except OSError:
        pass


def step(mon, state):
    """One control step. state carries the previous CPU reading and target."""
    c = conf()
    try:
        min_pct = min(max(float(c.get("FAN_MIN_PCT", 100)), 30.0), 100.0)
    except ValueError:
        min_pct = 100.0
    mode = c.get("FAN_MODE", "curve")
    busy, total = cpu_busy()
    pb, pt = state.get("cpu", (busy, total))
    load = (busy - pb) / (total - pt) if total > pt else 0.0
    state["cpu"] = (busy, total)
    temp = cpu_temp()
    info = {"at": int(time.time()), "mode": mode, "min_pct": min_pct, "load": round(load * 100, 1),
            "temp": temp, "fans": []}
    if mode != "curve":
        set_auto(mon)
        info["mode"] = "auto"
        state["target"] = None
    else:
        # No temperature reading: full speed is the safe choice (a hot Mac must not
        # be left to a lazy automatic curve).
        pct = 100.0 if temp is None else target_pct(min_pct, load, temp, state.get("target"))
        if temp is None:
            info["mode"] = "full (no temperature reading)"
        state["target"] = pct
        info["target_pct"] = pct
        for n, lo, hi in fans(mon):
            rpm = rpm_for(pct, lo, hi)
            write(mon / f"fan{n}_manual", 1)
            write(mon / f"fan{n}_output", rpm)
            info["fans"].append({"fan": n, "target_rpm": rpm, "rpm": int(read(mon / f"fan{n}_input", "0") or 0),
                                 "max_rpm": hi})
    report(info)
    return info


def main():
    if sys.argv[1:] == ["--check"]:          # for the installer: are there fans to control?
        mon = smc()
        print(mon or "no controllable fans")
        return 0 if mon and fans(mon) else 1
    mon = smc()
    if mon is None or not fans(mon):
        report({"at": int(time.time()), "mode": "none", "fans": [], "message": "no Apple SMC fans found"})
        print("no Apple SMC fans found; nothing to control", file=sys.stderr)
        return 0
    state = {}

    def stop(*_):
        set_auto(mon)
        report({"at": int(time.time()), "mode": "auto (controller stopped)", "fans": []})
        sys.exit(0)
    signal.signal(signal.SIGTERM, stop)
    signal.signal(signal.SIGINT, stop)
    try:
        while True:
            try:
                step(mon, state)
            except OSError as err:          # the SMC refused a write: give control back
                set_auto(mon)
                report({"at": int(time.time()), "mode": f"auto ({err.strerror or err})", "fans": []})
            time.sleep(INTERVAL)
    finally:
        set_auto(mon)


if __name__ == "__main__":
    sys.exit(main())
