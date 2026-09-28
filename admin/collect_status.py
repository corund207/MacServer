#!/usr/bin/env python3
"""Collect MacServer health into a JSON file for the admin page.

Runs as root from macserver-status.timer. The output holds no secrets: only the
public (anon/publishable) app keys, which are meant to ship inside client apps.
"""
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import time

SUPABASE_ENV = Path("/opt/macserver/supabase/.env")
CONF = Path("/etc/macserver/macserver.conf")
OUT = Path("/run/macserver/status.json")
PUBLIC_KEYS = ("SUPABASE_PUBLISHABLE_KEY", "ANON_KEY")


def run(*cmd, timeout=10):
    try:
        done = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout, check=False)
    except (OSError, subprocess.TimeoutExpired):
        return None
    return done.stdout if done.returncode == 0 else None


def read(path, default=None):
    try:
        return Path(path).read_text().strip()
    except OSError:
        return default


def parse_env(text):
    values = {}
    for line in (text or "").splitlines():
        if "=" in line and not line.lstrip().startswith("#"):
            key, _, value = line.partition("=")
            values[key.strip()] = value.strip()
    return values


def memory(meminfo_text):
    fields = {}
    for line in meminfo_text.splitlines():
        name, _, rest = line.partition(":")
        if rest.split():
            fields[name] = int(rest.split()[0]) * 1024
    total = fields.get("MemTotal", 0)
    return {"total": total, "used": total - fields.get("MemAvailable", total)}


def hwmon(root="/sys/class/hwmon"):
    """Hottest CPU package temperature (C) and fan speeds (RPM)."""
    temps, fans = [], []
    for mon in sorted(Path(root).glob("hwmon*")):
        name = read(mon / "name", "")
        for sensor in sorted(mon.glob("temp*_input")):
            if name in ("coretemp", "applesmc", "acpitz"):
                value = read(sensor)
                if value and value.lstrip("-").isdigit():
                    temps.append(int(value) / 1000)
        for sensor in sorted(mon.glob("fan*_input")):
            value = read(sensor)
            if value and value.isdigit():
                fans.append(int(value))
    return {"cpu_temp_c": max(temps) if temps else None, "fans_rpm": fans}


def battery(root="/sys/class/power_supply"):
    for supply in sorted(Path(root).glob("*")):
        if read(supply / "type") == "Battery":
            capacity = read(supply / "capacity")
            return {"percent": int(capacity) if capacity and capacity.isdigit() else None,
                    "status": read(supply / "status", "Unknown"),
                    "limit": read(supply / "charge_control_end_threshold")}
    return None


def containers(ps_json_lines):
    result = []
    for line in (ps_json_lines or "").splitlines():
        try:
            item = json.loads(line)
        except ValueError:
            continue
        result.append({"name": item.get("Names", ""), "state": item.get("State", ""),
                       "status": item.get("Status", ""),
                       "project": (dict(p.split("=", 1) for p in item.get("Labels", "").split(",") if "=" in p)
                                   .get("com.docker.compose.project", ""))})
    return sorted(result, key=lambda c: (c["project"], c["name"]))


def tailscale(status_text):
    try:
        data = json.loads(status_text or "")
    except ValueError:
        return {"state": "unknown"}
    me = data.get("Self") or {}
    return {"state": data.get("BackendState", "unknown"), "name": (me.get("DNSName") or "").rstrip("."),
            "ips": me.get("TailscaleIPs") or [], "online": bool(me.get("Online"))}


def collect():
    load = os.getloadavg()
    disk = shutil.disk_usage("/")
    conf = parse_env(read(CONF, ""))
    env = parse_env(read(SUPABASE_ENV, ""))
    t2_modules = {m: Path(f"/sys/module/{m}").exists() for m in ("brcmfmac", "applesmc")}
    # The T2 keyboard/trackpad driver: t2bce_vhci since kernel 6.18, apple_bce before.
    t2_modules["keyboard"] = any(Path(f"/sys/module/{m}").exists() for m in ("t2bce_vhci", "apple_bce"))
    upgradable = run("apt-get", "-s", "-o", "Debug::NoLocking=1", "upgrade", timeout=60)
    return {
        "generated_at": int(time.time()),
        "setup_done": Path("/var/lib/macserver/firstboot.done").exists(),
        "host": {
            "model": read("/sys/class/dmi/id/product_name", "unknown"),
            "kernel": os.uname().release,
            "t2_kernel": "t2" in os.uname().release,
            "t2_modules": t2_modules,
            "uptime_s": int(float(read("/proc/uptime", "0 0").split()[0])),
            "load": [round(x, 2) for x in load],
            "cpus": os.cpu_count(),
            "memory": memory(read("/proc/meminfo", "")),
            "disk": {"total": disk.total, "used": disk.used},
            "sensors": hwmon(),
            "battery": battery(),
            "updates_pending": sum(1 for l in (upgradable or "").splitlines() if l.startswith("Inst ")),
            "reboot_required": Path("/run/reboot-required").exists(),
        },
        "tailscale": tailscale(run("tailscale", "status", "--json")),
        "containers": containers(run("docker", "ps", "-a", "--format", "{{json .}}")),
        "public_domain": conf.get("PUBLIC_DOMAIN", ""),
        "site_url": conf.get("SITE_URL", ""),
        "api_url": env.get("SUPABASE_PUBLIC_URL", ""),
        "public_keys": {k: env[k] for k in PUBLIC_KEYS if env.get(k)},
    }


def write_atomic(path, data):
    path.parent.mkdir(mode=0o755, parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, indent=1))
    os.chmod(tmp, 0o644)
    os.replace(tmp, path)


def main():
    write_atomic(Path(sys.argv[1]) if len(sys.argv) > 1 else OUT, collect())


if __name__ == "__main__":
    main()
