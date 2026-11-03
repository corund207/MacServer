#!/usr/bin/env python3
"""Collect MacServer health into a JSON file for the admin page.

Runs as root from macserver-status.timer (every 2 s). The output holds no secrets: only the
public (anon/publishable) app keys, which are meant to ship inside client apps.

Sections of status.json (each is optional and never costs the others):
  host, tailscale, containers, live, debug   the health snapshot and the live graphs' numbers
  idle, doomsday, claude, macserver_update   what the dashboards' state rows show
  system     the detail pages: host (OS, boot time, sessions), cpu (model, governor, pressure,
             throttle counts), memory, swap, kernel (run queue, open files, conntrack), storage
             (filesystems, disks, Docker's disk use), network (interfaces, Wi-Fi, TCP), sensors
             (every temperature, the T2 fans), power (battery health, time left), docker (per
             compose project), tailscale (every device, route, key expiry), db (Postgres)
Per-container extras (restarts, started, health, pids, memory limit, network and block I/O rates)
are merged into each entry of `containers`; live.cpu.times, live.rates and live.disks add the CPU
time breakdown, kernel rates and per-disk throughput and busy %.
"""
import calendar
import collections
import ipaddress
import json
import os
from pathlib import Path
try:
    import pwd
except ImportError:   # Windows: only the tests are ever run there, never the collector itself
    pwd = None
import re
import shutil
import socket
import stat
import subprocess
import sys
import threading
import time
import urllib.request

from units import parse_size

SUPABASE_ENV = Path("/opt/macserver/supabase/.env")
CONF = Path("/etc/macserver/macserver.conf")
OUT = Path("/run/macserver/status.json")
PUBLIC_KEYS = ("SUPABASE_PUBLISHABLE_KEY", "ANON_KEY")
STATE = Path("/run/macserver/collector-state.json")
CGROUP_ROOT = Path("/sys/fs/cgroup")
UPDATES_TTL_S = 300      # apt-get takes ~1.5 s: too slow for every run
PUBLIC_TTL_S = 20        # a dead public route can take the full timeout to notice


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


def hwmon(root="/sys/class/hwmon", acpi_fans="/sys/bus/acpi/devices/APP0001:00"):
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
    if not fans:   # T2 Macs: the fans are on the SMC's ACPI device, not in hwmon
        fans = [f["rpm"] for f in fan_list(acpi_fans, root)]
    return {"cpu_temp_c": max(temps) if temps else None, "fans_rpm": fans}


def battery(root="/sys/class/power_supply"):
    for supply in sorted(Path(root).glob("*")):
        if read(supply / "type") == "Battery":
            capacity = read(supply / "capacity")
            return {"percent": int(capacity) if capacity and capacity.isdigit() else None,
                    "status": read(supply / "status", "Unknown"),
                    "limit": read(supply / "charge_control_end_threshold")}
    return None


CLAUDE_LOG = Path("/run/macserver-claude/session.log")
ANSI = re.compile(r"\x1b\[[0-9;?]*[ -/]*[@-~]|\x1b\][^\x07\x1b]*(?:\x07|\x1b\\)|\x1b[()][0-9A-Za-z]")
SESSION_URL = re.compile(r"https://claude\.ai/code[/?][A-Za-z0-9_\-/?=&.%]*")


def unit_state(unit):
    """systemctl is-active output (active, activating, failed, inactive, ...)."""
    try:
        done = subprocess.run(["systemctl", "is-active", unit], capture_output=True, text=True, timeout=10, check=False)
    except (OSError, subprocess.TimeoutExpired):
        return "unknown"
    return done.stdout.strip() or "unknown"


def claude_session(log_text, state, installed):
    """Remote Control session started from the admin page: state and link."""
    info = {"installed": installed, "state": state or "inactive", "url": "", "problem": ""}
    text = ANSI.sub("", log_text or "")
    urls = SESSION_URL.findall(text)
    if urls and info["state"] == "active":   # the log outlives the session
        info["url"] = urls[-1].rstrip(".")
    if "must be logged in" in text or "requires a claude.ai subscription" in text:
        info["problem"] = "login"
    elif "Enable Remote Control?" in text and not urls:
        info["problem"] = "consent"
    return info


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


def is_funnel_node(peer):
    """Tailscale's own Funnel ingress relays show up as dozens of online peers; they are not the owner's devices."""
    return peer.get("HostName") == "funnel-ingress-node"


def tailscale(status_text):
    try:
        data = json.loads(status_text or "")
    except ValueError:
        return {"state": "unknown"}
    me = data.get("Self") or {}
    peers = data.get("Peer") or {}
    return {"state": data.get("BackendState", "unknown"), "name": (me.get("DNSName") or "").rstrip("."),
            "ips": me.get("TailscaleIPs") or [], "online": bool(me.get("Online")),
            "peers_online": sum(1 for p in peers.values() if p.get("Online") and not is_funnel_node(p))}


def container_stats(stats_json_lines):
    """CPU % and memory per container from `docker stats --no-stream`."""
    stats = {}
    for line in (stats_json_lines or "").splitlines():
        try:
            item = json.loads(line)
            cpu = float(item.get("CPUPerc", "0").rstrip("%") or 0)
        except ValueError:
            continue
        stats[item.get("Name", "")] = {"cpu": cpu, "mem": parse_size(item.get("MemUsage", "").split("/")[0])}
    return stats


def container_ids(ps_json_lines):
    """Full container ids by name, from `docker ps --no-trunc`."""
    ids = {}
    for line in (ps_json_lines or "").splitlines():
        try:
            item = json.loads(line)
        except ValueError:
            continue
        if item.get("Names") and item.get("ID"):
            ids[item["Names"]] = item["ID"]
    return ids


def cgroup_stats(ids, prev, now, root=CGROUP_ROOT):
    """CPU % and memory per running container, read from cgroup v2.

    The same numbers `docker stats` shows (CPU is a percentage of one core, memory
    excludes the page cache), without its ~2 s sampling. CPU needs the previous
    reading, so the first run reports 0. Returns (stats, sample to pass as `prev` next time).
    """
    stats, cpu_now = {}, {}
    span = now - prev.get("at", now)
    for name, cid in ids.items():
        base = root / "system.slice" / f"docker-{cid}.scope"
        try:
            usec = next(int(l.split()[1]) for l in (base / "cpu.stat").read_text().splitlines()
                        if l.startswith("usage_usec "))
            memory = int((base / "memory.current").read_text())
            cache = next((int(l.split()[1]) for l in (base / "memory.stat").read_text().splitlines()
                          if l.startswith("inactive_file ")), 0)
        except (OSError, ValueError, StopIteration):
            continue   # stopped, or no cgroup v2: the caller falls back to `docker stats`
        cpu_now[name] = usec
        before = prev.get("cpu", {}).get(name)
        cpu = round(max(usec - before, 0) / (span * 1e6) * 100, 2) if before is not None and span > 0 else 0.0
        stats[name] = {"cpu": cpu, "mem": max(memory - cache, 0)}
    return stats, {"at": now, "cpu": cpu_now}


LIVE_PROCS, LIVE_PROCS_BY_MEM = 10, 6   # the busiest by CPU, plus the biggest by memory
SKIP_IFACES = re.compile(r"^(lo|veth\w*)$")
SKIP_DISKS = re.compile(r"^(loop|ram|zram|dm-|sr|md)\d*")


def cpu_times(stat_text):
    """{"cpu": (busy, total), "cpu0": ...} from /proc/stat, in clock ticks."""
    out = {}
    for line in stat_text.splitlines():
        name, _, rest = line.partition(" ")
        if not re.fullmatch(r"cpu\d*", name):
            continue
        f = [int(x) for x in rest.split()[:8]]
        if len(f) < 4:
            continue
        idle = f[3] + (f[4] if len(f) > 4 else 0)   # idle + iowait
        total = sum(f)
        out[name] = (total - idle, total)
    return out


def net_bytes(dev_text):
    out = {}
    for line in dev_text.splitlines()[2:]:
        name, _, rest = line.partition(":")
        name, f = name.strip(), rest.split()
        if len(f) >= 9 and name and not SKIP_IFACES.match(name):
            out[name] = (int(f[0]), int(f[8]))   # rx bytes, tx bytes
    return out


def disk_bytes(diskstats_text, block_root="/sys/block"):
    """Bytes read and written by the whole disks (not partitions, loops or RAM disks)."""
    read_b = write_b = 0
    for line in diskstats_text.splitlines():
        f = line.split()
        if len(f) < 10 or SKIP_DISKS.match(f[2]) or not Path(block_root, f[2]).exists():
            continue
        read_b += int(f[5]) * 512
        write_b += int(f[9]) * 512
    return read_b, write_b


CPU_KINDS = ("user", "nice", "system", "idle", "iowait", "irq", "softirq", "steal")


def cpu_breakdown(stat_text):
    """Cumulative clock ticks of the all-CPU line, by kind of work (/proc/stat's first line)."""
    for line in stat_text.splitlines():
        if line.startswith("cpu "):
            f = [int(x) for x in line.split()[1:9]]
            return dict(zip(CPU_KINDS, f + [0] * (8 - len(f))))
    return {}


def stat_counters(stat_text):
    """Cumulative counters from /proc/stat: context switches, interrupts, forks; boot time, run queue."""
    out = {}
    for line in stat_text.splitlines():
        name, _, rest = line.partition(" ")
        first = rest.split()[0] if rest.split() else ""
        if first.isdigit() and name in ("ctxt", "intr", "processes", "btime", "procs_running", "procs_blocked"):
            out[name] = int(first)
    return out


def vmstat_counters(text):
    """Swap traffic, page faults and OOM kills (cumulative) from /proc/vmstat."""
    out = {}
    for line in text.splitlines():
        name, _, value = line.partition(" ")
        if name in ("pswpin", "pswpout", "pgmajfault", "pgfault", "oom_kill") and value.strip().isdigit():
            out[name] = int(value)
    return out


def disk_devices(diskstats_text, block_root="/sys/block"):
    """Per whole disk: (sectors read, sectors written, reads, writes, milliseconds busy)."""
    out = {}
    for line in diskstats_text.splitlines():
        f = line.split()
        if len(f) < 13 or SKIP_DISKS.match(f[2]) or not Path(block_root, f[2]).exists():
            continue
        out[f[2]] = (int(f[5]), int(f[9]), int(f[3]), int(f[7]), int(f[12]))
    return out


def process_ticks(proc_root, page_size):
    """{pid: {name, user, ticks, start, rss, state, threads}} for every process. Only the
    short process name is reported: command lines can carry secrets."""
    out = {}
    for entry in Path(proc_root).iterdir():
        if not entry.name.isdigit():
            continue
        try:
            text = (entry / "stat").read_text()
            uid = (entry / "stat").stat().st_uid
        except OSError:
            continue   # it exited while we looked
        head, _, tail = text.rpartition(")")    # the name may contain spaces and parentheses
        f = tail.split()
        if len(f) < 22:
            continue
        try:
            user = pwd.getpwuid(uid).pw_name
        except (KeyError, AttributeError):
            user = str(uid)
        out[int(entry.name)] = {"name": head.partition("(")[2][:24], "user": user, "state": f[0],
                                "ticks": int(f[11]) + int(f[12]), "start": int(f[19]),
                                "rss": int(f[21]) * page_size, "threads": int(f[17])}
    return out


def meminfo_fields(text):
    fields = {}
    for line in text.splitlines():
        name, _, rest = line.partition(":")
        if rest.split():
            fields[name] = int(rest.split()[0]) * 1024
    return fields


def cpu_freqs(root="/sys/devices/system/cpu"):
    out = []
    for n in range(os.cpu_count() or 1):
        khz = read(f"{root}/cpu{n}/cpufreq/scaling_cur_freq")
        out.append(round(int(khz) / 1000) if khz and khz.strip().isdigit() else None)
    return out


def live_stats(prev, now, proc_root="/proc", sys_root="/sys", tick_hz=None, page_size=None):
    """What a btop-style view needs: CPU % (total and per core), network and disk rates,
    memory and swap, and the busiest processes. Rates need the previous reading, so the
    first run reports zeros. Returns (stats, reading to pass as `prev` next time)."""
    tick_hz = tick_hz or os.sysconf("SC_CLK_TCK")
    page_size = page_size or os.sysconf("SC_PAGE_SIZE")
    p = Path(proc_root)
    stat_text = read(p / "stat", "")
    cpu = cpu_times(stat_text)
    kinds, counters, vm = cpu_breakdown(stat_text), stat_counters(stat_text), vmstat_counters(read(p / "vmstat", ""))
    net = net_bytes(read(p / "net/dev", ""))
    disk = disk_bytes(read(p / "diskstats", ""), f"{sys_root}/block")
    disks_now = disk_devices(read(p / "diskstats", ""), f"{sys_root}/block")
    procs = process_ticks(p, page_size)
    span = now - prev.get("at", now)
    known = span > 0

    def pct(name):
        before = prev.get("cpu", {}).get(name)
        if not known or before is None or cpu[name][1] <= before[1]:
            return 0.0
        return round(100 * (cpu[name][0] - before[0]) / (cpu[name][1] - before[1]), 1)

    def rate(after, before):
        return round(max(after - before, 0) / span) if known and before is not None else 0

    cores = sorted((n for n in cpu if n != "cpu"), key=lambda n: int(n[3:]))
    rx = tx = 0
    ifaces = {}
    for name, (r, t) in net.items():
        old = prev.get("net", {}).get(name)
        ifaces[name] = {"rx": rate(r, old[0] if old else None), "tx": rate(t, old[1] if old else None)}
        rx += ifaces[name]["rx"]
        tx += ifaces[name]["tx"]
    old_disk = prev.get("disk")
    mem = meminfo_fields(read(p / "meminfo", ""))
    mem_total = mem.get("MemTotal", 0)
    busy = []
    for pid, info in procs.items():
        old = prev.get("procs", {}).get(str(pid))
        cpu_pct = 0.0
        if known and old and old[1] == info["start"]:
            cpu_pct = round(100 * max(info["ticks"] - old[0], 0) / tick_hz / span, 1)
        busy.append({"pid": pid, "name": info["name"], "user": info["user"], "state": info["state"],
                     "cpu": cpu_pct, "rss": info["rss"], "threads": info["threads"],
                     "mem": round(100 * info["rss"] / mem_total, 1) if mem_total else 0.0})
    busy.sort(key=lambda x: (-x["cpu"], -x["rss"]))

    def times():
        """Where the CPU's time went since the last reading, in % (nice counts as user, softirq as irq)."""
        before = prev.get("cpu_kinds")
        if not known or not before:
            return {}
        d = {k: max(kinds.get(k, 0) - before.get(k, 0), 0) for k in CPU_KINDS}
        total = sum(d.values())
        if not total:
            return {}
        share = {"user": d["user"] + d["nice"], "system": d["system"], "iowait": d["iowait"],
                 "irq": d["irq"] + d["softirq"], "steal": d["steal"]}
        return {k: round(100 * v / total, 1) for k, v in share.items()}

    def per_second(table, before, name, scale=1):
        if not known or name not in table or name not in (before or {}):
            return 0
        return round(max(table[name] - before[name], 0) * scale / span)

    disks = {}
    for name, now_d in disks_now.items():
        old = (prev.get("disks") or {}).get(name)
        if known and old:
            disks[name] = {"read": round(max(now_d[0] - old[0], 0) * 512 / span),
                           "write": round(max(now_d[1] - old[1], 0) * 512 / span),
                           "riops": round(max(now_d[2] - old[2], 0) / span),
                           "wiops": round(max(now_d[3] - old[3], 0) / span),
                           "busy": round(min(100.0, max(now_d[4] - old[4], 0) / (span * 10)), 1)}
        else:
            disks[name] = {"read": 0, "write": 0, "riops": 0, "wiops": 0, "busy": 0.0}
    stats = {
        "span_s": round(span, 2) if known else 0,
        "cpu": {"total": pct("cpu") if "cpu" in cpu else 0.0, "cores": [pct(n) for n in cores],
                "freq_mhz": cpu_freqs(f"{sys_root}/devices/system/cpu"), "times": times()},
        "rates": {"ctxt": per_second(counters, prev.get("stat"), "ctxt"),
                  "intr": per_second(counters, prev.get("stat"), "intr"),
                  "forks": per_second(counters, prev.get("stat"), "processes"),
                  "swap_in": per_second(vm, prev.get("vm"), "pswpin", page_size),
                  "swap_out": per_second(vm, prev.get("vm"), "pswpout", page_size),
                  "major_faults": per_second(vm, prev.get("vm"), "pgmajfault")},
        "run_queue": {"running": counters.get("procs_running", 0), "blocked": counters.get("procs_blocked", 0)},
        "disks": disks,
        "mem": {"total": mem_total, "used": mem_total - mem.get("MemAvailable", mem_total),
                "cached": mem.get("Cached", 0), "buffers": mem.get("Buffers", 0)},
        "swap": {"total": mem.get("SwapTotal", 0), "used": mem.get("SwapTotal", 0) - mem.get("SwapFree", 0)},
        "net": {"rx": rx, "tx": tx, "ifaces": ifaces},
        "disk_io": {"read": rate(disk[0], old_disk[0] if old_disk else None),
                    "write": rate(disk[1], old_disk[1] if old_disk else None)},
        "tasks": {"total": len(procs), "running": sum(1 for i in procs.values() if i["state"] == "R"),
                  "threads": sum(i["threads"] for i in procs.values())},
        "procs": busy[:LIVE_PROCS] + sorted(busy[LIVE_PROCS:], key=lambda x: -x["rss"])[:LIVE_PROCS_BY_MEM],
    }
    reading = {"at": now, "cpu": cpu, "net": net, "disk": disk, "cpu_kinds": kinds, "stat": counters, "vm": vm,
               "disks": disks_now, "procs": {str(pid): (i["ticks"], i["start"]) for pid, i in procs.items()}}
    return stats, reading


def cached(state, key, ttl, now, produce):
    """Reuse a slow value for `ttl` seconds; `state` is kept between runs (STATE)."""
    hit = state.setdefault("cache", {}).get(key)
    if hit and 0 <= now - hit["at"] < ttl:
        return hit["value"]
    value = produce()
    state["cache"][key] = {"at": now, "value": value}
    return value


def load_state(path=STATE):
    try:
        data = json.loads(Path(path).read_text())
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def save_state(state, path=STATE):
    path = Path(path)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(state))
    os.chmod(tmp, 0o600)
    os.replace(tmp, path)


SECRET_PATTERNS = [   # order matters: "Authorization: Bearer X" must lose X, not "Bearer"
    re.compile(r"eyJ[\w-]+\.[\w-]+\.[\w-]*"),                                   # JWTs (API keys, sessions)
    re.compile(r"(?i)bearer\s+\S+"),
    re.compile(r"(?i)(password|passwd|secret|token|apikey|api_key|authorization)([\"'=: ]+)\S+"),
    re.compile(r"[A-Za-z0-9+/_-]{32,}"),                                      # long keys and hashes
    re.compile(r"postgres(ql)?://[^\s]+"),
]


def redact(line):
    for pattern in SECRET_PATTERNS:
        if pattern.groups >= 2:
            line = pattern.sub(lambda m: f"{m.group(1)}{m.group(2)}[hidden]", line)
        else:
            line = pattern.sub("[hidden]", line)
    return line


def recent_logs(name, lines=8):
    """Last lines of a container's log, cleaned of secrets, for the incident view."""
    out = run("docker", "logs", "--tail", str(lines), name, timeout=10)
    if out is None:
        try:
            done = subprocess.run(["docker", "logs", "--tail", str(lines), name], capture_output=True,
                                  text=True, timeout=10, check=False)
            out = done.stdout + done.stderr
        except (OSError, subprocess.TimeoutExpired):
            return []
    return [redact(line)[:200] for line in out.splitlines()[-lines:] if line.strip()]


def with_logs(items):
    """Stopped or unhealthy services get their recent log lines."""
    for item in items:
        if item.get("state") != "running" or "unhealthy" in item.get("status", ""):
            item["logs"] = recent_logs(item["name"])
    return items


def with_stats(items, stats):
    for item in items:
        item.update(stats.get(item["name"], {"cpu": None, "mem": None}))
    return items


def self_update():
    """What the MacServer self-updater last did (installer/self-update.sh)."""
    for path in (Path("/run/macserver/self-update.json"), Path("/var/lib/macserver/self-update.json")):
        try:
            data = json.loads(path.read_text())
        except (OSError, ValueError):
            continue
        return {k: data.get(k, "") for k in ("state", "message", "current", "latest", "auto")}
    return None


def public_ok(domain):
    """Does the public API route answer? None when there is no public route."""
    if not domain:
        return None
    try:
        with urllib.request.urlopen(f"https://{domain}/auth/v1/health", timeout=3) as response:
            return response.status == 200
    except (OSError, ValueError):
        return False


# --- debug data for the dashboard's incident pages -------------------------------------
# Gathered as root, capped in size and cleaned of secrets (query strings, tokens) before it
# reaches status.json. Slow parts are cached in `state` (see cached()).

MONTHS = {m: i for i, m in enumerate("Jan Feb Mar Apr May Jun Jul Aug Sep Oct Nov Dec".split(), 1)}
ACCESS = re.compile(r'^(\S+) \S+ \S+ \[(\d+)/(\w{3})/(\d+):(\d+):(\d+):(\d+) ([+-])(\d\d)(\d\d)\] '
                    r'"(\S+) (\S+)[^"]*" (\d{3}) (\d+|-) "[^"]*" "([^"]*)"')
ID_SEGMENT = re.compile(r"/(?:[0-9a-fA-F]{8}-[0-9a-fA-F-]{27}|\d{2,})(?=/|$)")
PORT_NAMES = {22: "ssh", 53: "dns", 80: "http", 123: "ntp", 443: "https", 465: "smtp", 587: "smtp",
              993: "imap", 3478: "stun", 5432: "postgres", 6543: "pooler", 8000: "api", 8090: "admin",
              8443: "studio", 41641: "tailscale"}
TAILNET = ipaddress.ip_network("100.64.0.0/10")
TAILNET6 = ipaddress.ip_network("fd7a:115c:a1e0::/48")
WATCHED_FILES = (
    ("/opt/macserver/supabase/docker-compose.yml", "Supabase services (managed: do not edit)"),
    ("/opt/macserver/supabase/.env", "Supabase settings and secrets (never print it)"),
    ("/opt/macserver/supabase/volumes/functions", "Edge Functions source"),
    ("/opt/macserver/supabase/volumes/db", "Postgres data"),
    ("/opt/macserver/gateway/compose.yml", "public route: Caddy and cloudflared"),
    ("/opt/macserver/gateway/Caddyfile", "public path filter"),
    ("/etc/macserver/macserver.conf", "MacServer settings"),
    ("/etc/nftables.conf", "firewall rules"),
    ("/etc/docker/daemon.json", "Docker daemon settings"),
    ("/var/log/macserver-firstboot.log", "installer log"),
    ("/var/log/macserver-self-update.log", "self-update log"),
    ("/var/log/macserver-setup.log", "USB installer log"),
    ("/var/backups/macserver", "database backups"),
    ("/run/macserver/status.json", "this screen's data (2 s)"),
    ("/usr/local/lib/macserver/collect_status.py", "the collector"),
    ("/usr/local/lib/macserver/dashboard", "this dashboard"),
    ("/opt/macserver/admin/server.py", "admin page server"),
)


def ip_kind(text):
    """loopback | tailnet | lan | public for an address ('' when it is not one)."""
    try:
        ip = ipaddress.ip_address(str(text).strip("[]").split("%")[0])
    except ValueError:
        return ""
    if ip.is_loopback:
        return "loopback"
    if ip in TAILNET or ip in TAILNET6:
        return "tailnet"
    if ip.is_private or ip.is_link_local:
        return "lan"
    return "public"


def agent_kind(user_agent):
    ua = (user_agent or "").lower()
    for needle, name in (("supabaseedgeruntime", "edge function"), ("deno", "edge function"),
                         ("supabase-js", "supabase-js"), ("cfnetwork", "iOS app"), ("darwin", "iOS app"),
                         ("curl", "curl"), ("postgrest", "postgrest"), ("gotrue", "auth"),
                         ("mozilla", "browser"), ("python", "python"), ("go-http", "go client")):
        if needle in ua:
            return name
    return ((user_agent or "").split("/")[0] or "-")[:14]


UUID = re.compile(r"[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}")


def clean_path(path):
    """A request path without the query, with token-like segments hidden (ids stay readable)."""
    def segment(part):
        secret = part.startswith("eyJ") or (len(part) >= 40 and re.fullmatch(r"[A-Za-z0-9_+=-]+", part)
                                            and not UUID.fullmatch(part))
        return "[hidden]" if secret else part
    return "/".join(segment(part) for part in path.split("?", 1)[0].split("/"))


def parse_access(line):
    """One Envoy access-log line (combined format) -> request dict, or None."""
    m = ACCESS.match(line)
    if not m:
        return None
    ip, day, mon, year, hh, mm, ss, sign, tzh, tzm, method, target, status, size, agent = m.groups()
    try:
        t = calendar.timegm((int(year), MONTHS[mon], int(day), int(hh), int(mm), int(ss)))
    except (KeyError, ValueError, OverflowError):
        return None
    t -= (1 if sign == "+" else -1) * (int(tzh) * 3600 + int(tzm) * 60)
    return {"t": t, "ip": ip, "method": method, "path": clean_path(target)[:90],
            "status": int(status), "bytes": 0 if size == "-" else int(size), "agent": agent_kind(agent)}


def client_label(ip, names, peers):
    """Who an address is: a container, this Mac, a tailnet device, a LAN host."""
    if ip in names:
        return names[ip].replace("supabase-", "")
    if ip in peers:
        return "tailnet " + peers[ip]
    kind = ip_kind(ip)
    if kind == "loopback" or (ip.startswith("172.") and ip.endswith(".0.1")):
        return "this Mac"
    if kind == "tailnet":
        return "tailnet " + ip
    return f"LAN {ip}" if kind == "lan" else ip


def request_summary(lines, now, names, peers, old_errors=(), window_s=60):
    """Inbound API traffic over the last minute, the recent requests, and lasting errors."""
    entries = sorted((e for e in map(parse_access, lines or []) if e), key=lambda e: e["t"])
    window = [e for e in entries if 0 <= now - e["t"] <= window_s]
    classes = {"2xx": 0, "3xx": 0, "4xx": 0, "5xx": 0}
    per_2s = [0] * (window_s // 2)
    for e in window:
        classes[f"{min(max(e['status'] // 100, 2), 5)}xx"] += 1
        per_2s[-1 - min(int((now - e["t"]) // 2), len(per_2s) - 1)] += 1

    def row(e):
        return {"t": e["t"], "method": e["method"], "path": e["path"], "status": e["status"], "bytes": e["bytes"],
                "client": client_label(e["ip"], names, peers), "agent": e["agent"]}

    errors = list(old_errors)
    for e in entries:
        if e["status"] >= 400 and row(e) not in errors:
            errors.append(row(e))
    errors = [e for e in errors if now - e["t"] <= 900][-12:]
    paths = collections.Counter(f"{e['method']} {ID_SEGMENT.sub('/:id', e['path'])}" for e in window)
    clients = collections.Counter(f"{client_label(e['ip'], names, peers)} ({e['agent']})" for e in window)
    return {"window_s": window_s, "total": len(window), "classes": classes, "per_2s": per_2s,
            "recent": [row(e) for e in entries[-40:]][::-1], "errors": errors[::-1],
            "top_paths": paths.most_common(5), "top_clients": clients.most_common(5)}, errors


def parse_conntrack(text):
    """Kernel connection-tracking table -> [{proto, state, src, dst, sport, dport}] (original direction)."""
    flows = []
    for line in (text or "").splitlines():
        parts = line.split()
        if len(parts) < 7 or parts[2] not in ("tcp", "udp"):
            continue
        state = parts[5] if "=" not in parts[5] else ""
        keys = {}
        for part in parts[5:]:
            if "=" in part:
                k, _, v = part.partition("=")
                if k in ("src", "dst", "sport", "dport") and k not in keys:
                    keys[k] = v
        if len(keys) == 4:
            flows.append({"proto": parts[2], "state": state, **keys})
    return flows


def flow_summary(flows, host_ips, names, peers, procs):
    """Live connections in and out of this Mac and its containers, grouped."""
    out, inbound, counts = {}, {}, collections.Counter()
    for f in flows:
        counts[f["state"] or f["proto"]] += 1
        if f["state"] in ("TIME_WAIT", "CLOSE", "CLOSE_WAIT", "LAST_ACK", "FIN_WAIT"):
            continue
        src, dst = f["src"], f["dst"]
        try:
            port = int(f["dport"])
        except ValueError:
            continue
        src_local, dst_local = src in names or src in host_ips, dst in names or dst in host_ips
        svc = PORT_NAMES.get(port, f["proto"])
        if src_local and not dst_local and ip_kind(dst) != "loopback" and dst != "100.100.100.100":
            who = names.get(src, "").replace("supabase-", "") or procs.get((dst, port)) or "this Mac"
            entry = out.setdefault((who, dst, port), {"who": who, "dst": client_label(dst, names, peers),
                                                       "kind": ip_kind(dst), "port": port, "svc": svc, "n": 0})
            entry["n"] += 1
        elif dst_local and not src_local and ip_kind(src) != "loopback":
            entry = inbound.setdefault((src, port), {"src": client_label(src, names, peers), "kind": ip_kind(src),
                                                      "port": port, "svc": svc, "n": 0})
            entry["n"] += 1

    def rank(entry):
        return -entry["n"], entry["port"]

    return {"out": sorted(out.values(), key=rank)[:14], "in": sorted(inbound.values(), key=rank)[:14],
            "tracked": len(flows), "states": dict(counts.most_common(6))}


def parse_ss_listeners(text, published):
    """`ss -tlnpH` -> listening ports with who owns them and how far they reach."""
    rows, seen = [], set()
    for line in (text or "").splitlines():
        parts = line.split(None, 5)
        if len(parts) < 4:
            continue
        addr, _, port = parts[3].rpartition(":")
        addr = addr.strip("[]").split("%")[0]
        if addr in ("*", ""):
            addr = "0.0.0.0"
        if not port.isdigit():
            continue
        port = int(port)
        who = re.search(r'\(\("([^"]+)"', parts[5]) if len(parts) > 5 else None
        proc = who.group(1) if who else "?"
        if proc == "docker-proxy":
            proc = published.get(port, proc)
        scope = "all" if addr in ("0.0.0.0", "::") else ip_kind(addr) or "lan"
        if (port, proc, scope) in seen:
            continue
        seen.add((port, proc, scope))
        rows.append({"port": port, "addr": addr, "scope": scope, "proc": proc, "svc": PORT_NAMES.get(port, "")})
    order = {"all": 0, "public": 0, "lan": 1, "tailnet": 2, "loopback": 3}
    return sorted(rows, key=lambda r: (order.get(r["scope"], 1), r["port"]))[:20]


def parse_ss_established(text):
    """`ss -tnpH state established` -> {(remote ip, port): process name}."""
    procs = {}
    for line in (text or "").splitlines():
        parts = line.split(None, 4)
        if len(parts) < 5:
            continue
        remote, _, port = parts[3].rpartition(":")
        who = re.search(r'\(\("([^"]+)"', parts[4])
        if who and port.isdigit():
            procs[(remote.strip("[]"), int(port))] = who.group(1)
    return procs


def published_ports(ps_json_lines):
    """Host port -> container name for the ports Docker publishes."""
    ports = {}
    for line in (ps_json_lines or "").splitlines():
        try:
            item = json.loads(line)
        except ValueError:
            continue
        for port in re.findall(r"(?:0\.0\.0\.0|\[::\]|127\.0\.0\.1):(\d+)->", item.get("Ports", "")):
            ports[int(port)] = item.get("Names", "")
    return ports


def tailscale_peers(status_text):
    """Tailscale IP -> device name, for labelling connections."""
    try:
        data = json.loads(status_text or "")
    except ValueError:
        return {}
    return {ip: (p.get("HostName") or p.get("DNSName", "").split(".")[0])
            for p in (data.get("Peer") or {}).values() for ip in p.get("TailscaleIPs") or []}


def container_ips(names):
    """Container IP address -> container name."""
    out = run("docker", "inspect", "-f", "{{.Name}} {{range .NetworkSettings.Networks}}{{.IPAddress}} {{end}}",
              *names, timeout=10) if names else None
    ips = {}
    for line in (out or "").splitlines():
        name, *addresses = line.split()
        for ip in addresses:
            ips[ip] = name.lstrip("/")
    return ips


def host_addresses():
    """This Mac's own IPv4 addresses (LAN, tailnet, Docker bridges)."""
    text = run("ip", "-o", "-4", "addr") or ""
    return sorted({m.group(1) for m in re.finditer(r"inet (\d+\.\d+\.\d+\.\d+)/", text)})


def default_gateway(route_text):
    for line in (route_text or "").splitlines()[1:]:
        f = line.split()
        if len(f) > 2 and f[1] == "00000000":
            try:
                return ".".join(str(b) for b in bytes.fromhex(f[2])[::-1])
            except ValueError:
                return None
    return None


def ping_ms(host):
    m = re.search(r"time=([\d.]+) ms", run("ping", "-c", "1", "-W", "1", host, timeout=3) or "")
    return float(m.group(1)) if m else None


def tcp_ms(host, port, timeout=1.0):
    started = time.monotonic()
    try:
        with socket.create_connection((host, port), timeout=timeout):
            pass
    except OSError:
        return None
    return round((time.monotonic() - started) * 1000, 1)


def dns_ms(name="registry-1.docker.io", timeout=1.5):
    result = []

    def lookup():
        started = time.monotonic()
        try:
            socket.getaddrinfo(name, 443, type=socket.SOCK_STREAM)
            result.append(round((time.monotonic() - started) * 1000, 1))
        except OSError:
            result.append(None)

    worker = threading.Thread(target=lookup, daemon=True)
    worker.start()
    worker.join(timeout)
    return result[0] if result else None


def net_checks():
    """Gateway, Internet and DNS reachability, measured at the same time."""
    gateway = default_gateway(read("/proc/net/route", ""))
    found = {"gateway": gateway, "checked_at": int(time.time())}
    jobs = {"gateway_ms": lambda: ping_ms(gateway) if gateway else None,
            "internet_ms": lambda: tcp_ms("1.1.1.1", 443), "dns_ms": dns_ms}

    def go(key, fn):
        found[key] = fn()

    workers = [threading.Thread(target=go, args=item, daemon=True) for item in jobs.items()]
    for w in workers:
        w.start()
    for w in workers:
        w.join(3)
    return found


def inspect_abnormal(items):
    """Exit code, OOM flag, restarts and health output for services that are not fine."""
    names = [i["name"] for i in items if i.get("state") != "running" or "unhealthy" in i.get("status", "")
             or "restarting" in i.get("status", "").lower()][:8]
    if not names:
        return {}
    try:
        data = json.loads(run("docker", "inspect", *names, timeout=10) or "[]")
    except ValueError:
        return {}
    facts = {}
    for d in data:
        state = d.get("State") or {}
        health = state.get("Health") or {}
        last = (health.get("Log") or [{}])[-1].get("Output", "")
        facts[d.get("Name", "").lstrip("/")] = {
            "exit": state.get("ExitCode"), "oom": bool(state.get("OOMKilled")), "restarts": d.get("RestartCount"),
            "started": state.get("StartedAt", "")[:19], "finished": state.get("FinishedAt", "")[:19],
            "error": redact(state.get("Error", ""))[:120], "health": health.get("Status", ""),
            "health_output": redact(" ".join(last.split()))[:160]}
    return facts


ERROR_WORDS = re.compile(r"\b(error|fatal|panic|exception|failed|failure|denied|refused|timed? ?out|critical|traceback)\b",
                         re.IGNORECASE)


def service_errors(names, per_service=3):
    """The last error-looking log lines of each service (15 min), cleaned of secrets."""
    found = {}
    for name in sorted(names):
        try:
            done = subprocess.run(["docker", "logs", "--since", "15m", "--tail", "120", name],
                                  capture_output=True, text=True, timeout=6, check=False)
        except (OSError, subprocess.TimeoutExpired):
            continue
        lines = [line for line in (done.stdout + done.stderr).splitlines()
                 if line.strip() and ERROR_WORDS.search(line) and not ACCESS.match(line)]
        if lines:
            found[name.split(".")[-1].replace("supabase-", "")] = [redact(line)[:200] for line in lines[-per_service:]]
    return found


def watched_files(now):
    files = []
    for path, note in WATCHED_FILES:
        try:
            st = os.stat(path)
        except OSError:
            continue
        files.append({"path": path, "note": note, "age_s": max(int(now - st.st_mtime), 0),
                      "size": None if stat.S_ISDIR(st.st_mode) else st.st_size})
    return files


def backup_info(now, directory="/var/backups/macserver"):
    folder = Path(directory)
    try:
        dumps = sorted(folder.glob("*.sql.gz"), key=lambda p: p.stat().st_mtime)
    except OSError:
        dumps = []
    if not dumps:
        return {"count": 0}
    newest = dumps[-1].stat()
    return {"count": len(dumps), "newest_age_s": max(int(now - newest.st_mtime), 0), "newest_size": newest.st_size}


def system_errors():
    failed = [line.split()[0] for line in (run("systemctl", "--failed", "--no-legend", "--plain") or "").splitlines()
              if line.split()]
    journal = run("journalctl", "-p", "err", "--since", "-6h", "-n", "10", "--no-pager", "-o", "short-iso") or ""
    kernel = run("dmesg", "--level=err,crit,alert,emerg") or ""

    def clean(text, n):
        return [redact(line)[:170] for line in text.splitlines() if line.strip() and not line.startswith("-- ")][-n:]

    return {"failed_units": failed[:8], "journal": clean(journal, 8), "kernel": clean(kernel, 4)}


def kernel_counters():
    vm = dict(line.split() for line in read("/proc/vmstat", "").splitlines() if len(line.split()) == 2)
    throttle = 0
    for path in Path("/sys/devices/system/cpu").glob("cpu*/thermal_throttle/*_throttle_count"):
        try:
            throttle += int(path.read_text())
        except (OSError, ValueError):
            pass
    return {"oom_kills": int(vm.get("oom_kill", 0)), "thermal_throttle": throttle}


def debug_info(state, now, ps, ids, containers_now, peers):
    """Everything the incident pages show beyond the basic health snapshot."""
    names = cached(state, "container_ips:" + ",".join(sorted(ids)), 20, now, lambda: container_ips(sorted(ids)))
    host_ips = cached(state, "host_ips", 30, now, host_addresses)
    established = cached(state, "ss_established", 3, now, lambda: [
        [list(k), v] for k, v in parse_ss_established(run("ss", "-tnpH", "state", "established")).items()])
    procs = {(k[0], k[1]): v for k, v in established}
    flows = flow_summary(parse_conntrack(read("/proc/net/nf_conntrack", "")), set(host_ips), names, peers, procs)
    listeners = cached(state, "listeners", 5, now, lambda: parse_ss_listeners(run("ss", "-tlnpH"), published_ports(ps)))
    envoy = next((n for n in sorted(ids) if re.search(r"envoy|kong|api-gw", n)), None)
    requests = None
    if envoy:
        requests, state["request_errors"] = request_summary(
            (run("docker", "logs", "--since", "65s", envoy, timeout=8) or "").splitlines(), now, names, peers,
            state.get("request_errors", []))
    net = cached(state, "net_checks", 10, now, net_checks)
    fails = state.setdefault("net_fails", {"internet": 0, "dns": 0})
    if net.get("checked_at") != state.get("net_counted"):        # count each fresh measurement once
        state["net_counted"] = net.get("checked_at")
        for name, value in (("internet", net.get("internet_ms")), ("dns", net.get("dns_ms"))):
            fails[name] = fails[name] + 1 if value is None else 0
    return {
        "at": int(now), "requests": requests, "flows": flows, "listeners": listeners,
        "net": {**net, "internet_fails": fails["internet"], "dns_fails": fails["dns"]},
        "inspect": cached(state, "inspect", 3, now, lambda: inspect_abnormal(containers_now)),
        "system": {**cached(state, "system_errors", 10, now, system_errors), **kernel_counters()},
        "service_errors": cached(state, "service_errors:" + ",".join(sorted(ids)), 10, now, lambda: service_errors(ids)),
        "files": cached(state, "files", 20, now, lambda: watched_files(now)),
        "backups": cached(state, "backups", 30, now, lambda: backup_info(now)),
    }


def count_updates():
    upgradable = run("apt-get", "-s", "-o", "Debug::NoLocking=1", "upgrade", timeout=60)
    return sum(1 for l in (upgradable or "").splitlines() if l.startswith("Inst "))


def doomsday_state():
    """Lockdown state for the dashboard badge and `macserver status`.

    Read-only and never fatal: a missing file means normal. Holds no secrets.
    """
    for path in (Path("/run/macserver/doomsday.json"), Path("/var/lib/macserver/doomsday.json")):
        try:
            data = json.loads(path.read_text())
        except (OSError, ValueError):
            continue
        if isinstance(data, dict):
            return {"mode": data.get("mode", "on"), "state": data.get("state", "normal"),
                    "since": data.get("since"), "trigger": data.get("trigger", ""),
                    "score": data.get("score", 0),
                    "reasons": [str(r)[:160] for r in data.get("reasons", [])][:8]}
    return {"mode": "on", "state": "normal"}


# --- idle mode: low power when nobody is using the server ------------------------------
# The collector decides *whether* the Mac is idle; admin/idle.py only applies the
# reversible low-power settings (CPU governor, screen brightness, optional
# container pause). Signals for activity: API traffic in the last minute, live
# inbound connections, meaningful outbound work, an active Claude session, or a
# busy CPU (a backup or migration with no network traffic still keeps it awake).
# Tailscale's control plane, the tunnel keepalive and DNS/NTP lookups never
# count: they chatter even when nobody uses anything.

IDLE_AFTER_DEFAULT_S = 900
IDLE_AFTER_MIN_S = 60
IDLE_AFTER_MAX_S = 86400
IDLE_BUSY_CPU_PCT = 20.0   # total container CPU % that counts as "doing work"
IDLE_IGNORED_WHO = ("tailscaled", "this Mac")
IDLE_IGNORED_SVC = ("tailscale", "dns", "ntp", "stun")


def idle_settings(conf):
    """(mode, after_s) from macserver.conf: auto = quiet spell -> idle."""
    mode = (conf.get("IDLE_MODE", "auto") or "auto").strip().lower()
    if mode not in ("auto", "on", "off"):
        mode = "auto"
    try:
        after = int(conf.get("IDLE_AFTER_S", str(IDLE_AFTER_DEFAULT_S)))
    except ValueError:
        after = IDLE_AFTER_DEFAULT_S
    return mode, min(max(after, IDLE_AFTER_MIN_S), IDLE_AFTER_MAX_S)


def idle_activity(data):
    """Is the server in use right now? Returns (active, [reasons])."""
    reasons = []
    debug = data.get("debug") or {}
    requests = debug.get("requests") or {}
    try:
        total = int(requests.get("total", 0) or 0)
    except (TypeError, ValueError):
        total = 0
    if total > 0:
        reasons.append(f"{total} API request(s) in the last minute")
    flows = debug.get("flows") or {}
    inbound = flows.get("in") or []
    if inbound:
        n = sum(int(i.get("n", 1) or 1) for i in inbound)
        reasons.append(f"{n} inbound connection(s)")
    for entry in flows.get("out") or []:
        who = entry.get("who", "")
        if who in IDLE_IGNORED_WHO or "gateway" in who:
            continue
        if entry.get("svc") in IDLE_IGNORED_SVC or entry.get("port") in (53, 123, 41641, 3478):
            continue
        if entry.get("kind") not in ("tailnet", "lan", "public"):
            continue
        reasons.append(f"{who} -> {entry.get('dst', '?')}:{entry.get('port', '?')}")
        break
    claude = data.get("claude") or {}
    if claude.get("state") in ("active", "activating"):
        reasons.append("Claude session running")
    busy = sum((c.get("cpu") or 0) for c in data.get("containers", []) if c.get("state") == "running")
    try:
        busy = float(busy)
    except (TypeError, ValueError):
        busy = 0.0
    if busy >= IDLE_BUSY_CPU_PCT:
        reasons.append(f"containers busy ({busy:.0f}% CPU)")
    return bool(reasons), reasons[:3]


def idle_track(state, now, active, after_s, mode):
    """Hysteresis for idle mode, kept in the collector's STATE between runs.

    Activity wakes at once (last_active = now); idleness needs `after_s`
    continuous quiet seconds. Returns the "idle" section for status.json."""
    if mode == "off":
        state.pop("idle_last_active", None)
        state.pop("idle_since", None)
        return {"mode": mode, "idle": False, "since": None, "after_s": after_s, "reasons": []}
    if mode == "on":
        since = state.get("idle_since")
        if not isinstance(since, (int, float)):
            since = state["idle_since"] = int(now)
        return {"mode": mode, "idle": True, "since": since, "after_s": after_s, "reasons": []}
    if active:
        state["idle_last_active"] = now
        state.pop("idle_since", None)
        return {"mode": mode, "idle": False, "since": None, "after_s": after_s, "reasons": []}
    last = state.get("idle_last_active")
    if not isinstance(last, (int, float)):
        state["idle_last_active"] = last = now  # first run: grace period starts now
    if now - last >= after_s:
        since = state.get("idle_since")
        if not isinstance(since, (int, float)):
            since = state["idle_since"] = int(last + after_s)
        return {"mode": mode, "idle": True, "since": since, "after_s": after_s, "reasons": []}
    return {"mode": mode, "idle": False, "since": None, "after_s": after_s, "reasons": []}


# --- the detail pages: every stat worth showing, one section each -----------------------------
# Every reader takes already-read text or a path root, so the tests feed it a fake /proc and
# /sys, and every section is guarded in system_info(): one unreadable file never costs the rest.

def part(fn, *args, **kwargs):
    """Run one reader; None when it fails, so that section is simply missing."""
    try:
        return fn(*args, **kwargs)
    except Exception:   # noqa: BLE001
        return None


def iso_epoch(stamp):
    """Epoch seconds from an ISO time like 2026-11-02T15:13:34.1Z; None for empty or year-1 'never' values."""
    try:
        return calendar.timegm(time.strptime((stamp or "")[:19], "%Y-%m-%dT%H:%M:%S")) if (stamp or "")[:4] > "2000" else None
    except ValueError:
        return None


def integer(text):
    """A whole number from text (None when missing or not numeric)."""
    try:
        return int(str(text).strip())
    except (TypeError, ValueError):
        return None


def number(text, scale=1.0):
    """A sysfs number as float (None when missing or not numeric)."""
    try:
        return int(str(text).strip()) / scale
    except (TypeError, ValueError):
        return None


def os_pretty(os_release_text):
    return parse_env(os_release_text).get("PRETTY_NAME", "").strip('"')


def logged_in(who_text):
    """Interactive logins from `who`, without the address they came from: [{user, tty, since}]."""
    sessions = []
    for line in (who_text or "").splitlines():
        f = line.split()
        if len(f) >= 2 and f[1] != "seat0":
            sessions.append({"user": f[0], "tty": f[1], "since": " ".join(f[2:4])})
    return sessions


def cpu_static(cpuinfo_text, sys_root="/sys"):
    """Model, core/thread counts, frequency range and governor: facts that do not change."""
    model, microcode, threads, cores = "", "", 0, set()
    for block in cpuinfo_text.split("\n\n"):
        f = {}
        for line in block.splitlines():
            key, _, value = line.partition(":")
            f[key.strip()] = value.strip()
        if "processor" not in f:
            continue
        threads += 1
        model = model or f.get("model name", "")
        microcode = microcode or f.get("microcode", "")
        cores.add((f.get("physical id", "0"), f.get("core id", str(threads))))
    cpu = Path(sys_root) / "devices/system/cpu"
    freq = cpu / "cpu0/cpufreq"
    no_turbo = read(cpu / "intel_pstate/no_turbo")
    return {"model": re.sub(r"\s+", " ", re.sub(r"\((R|TM)\)|CPU|@.*", "", model)).strip(), "threads": threads,
            "cores": len(cores) or threads, "microcode": microcode,
            "min_mhz": number(read(freq / "cpuinfo_min_freq"), 1000), "max_mhz": number(read(freq / "cpuinfo_max_freq"), 1000),
            "governor": read(freq / "scaling_governor"), "driver": read(freq / "scaling_driver"),
            "turbo": None if no_turbo is None else no_turbo == "0"}


def pressure(text):
    """One /proc/pressure file: how long work stalled waiting for the resource, as 10/60/300 s averages (%)."""
    out = {}
    for line in (text or "").splitlines():
        kind, _, rest = line.partition(" ")
        values = dict(kv.split("=", 1) for kv in rest.split() if "=" in kv)
        if kind in ("some", "full") and "avg10" in values:
            out[kind] = {"avg10": float(values["avg10"]), "avg60": float(values.get("avg60", 0)),
                         "avg300": float(values.get("avg300", 0))}
    return out


def read_pressure(root="/proc/pressure"):
    return {name: pressure(read(Path(root) / name, "")) for name in ("cpu", "memory", "io") if (Path(root) / name).exists()}


MEM_NAMES = {"MemTotal": "total", "MemFree": "free", "MemAvailable": "available", "Buffers": "buffers",
             "Cached": "cached", "SwapCached": "swap_cached", "Active": "active", "Inactive": "inactive",
             "AnonPages": "anon", "Mapped": "mapped", "Shmem": "shmem", "Slab": "slab",
             "SReclaimable": "slab_reclaimable", "KernelStack": "kernel_stack", "PageTables": "page_tables",
             "Dirty": "dirty", "Writeback": "writeback", "SwapTotal": "swap_total", "SwapFree": "swap_free",
             "Committed_AS": "committed", "CommitLimit": "commit_limit"}


def memory_detail(meminfo_text):
    """Where the memory is: free, available, caches, kernel, shared, dirty, swap and commit (bytes)."""
    f = meminfo_fields(meminfo_text)
    out = {name: f.get(key, 0) for key, name in MEM_NAMES.items()}
    out["used"] = out["total"] - out["available"]
    out["swap_used"] = out["swap_total"] - out["swap_free"]
    return out


def swap_devices(swaps_text):
    """[{name, kind, size, used}] in bytes (/proc/swaps lists kilobytes)."""
    rows = []
    for line in (swaps_text or "").splitlines()[1:]:
        f = line.split()
        if len(f) >= 4 and f[2].isdigit() and f[3].isdigit():
            rows.append({"name": f[0], "kind": f[1], "size": int(f[2]) * 1024, "used": int(f[3]) * 1024})
    return rows


def throttle_counts(sys_root="/sys"):
    """Times the CPU slowed itself for heat, by kind. Hyper-threads share their core's count and
    every core shares the package's, so take the largest rather than adding the copies up."""
    found = {"core": 0, "package": 0}
    for kind in found:
        for path in (Path(sys_root) / "devices/system/cpu").glob(f"cpu*/thermal_throttle/{kind}_throttle_count"):
            found[kind] = max(found[kind], number(read(path)) or 0)
    return {k: int(v) for k, v in found.items()}


def kernel_facts(loadavg_text, filenr_text, conntrack_count, conntrack_max):
    """Run queue and thread counts, open file handles and connection-tracking table fill."""
    load = loadavg_text.split()
    running, _, total = (load[3] if len(load) > 3 else "0/0").partition("/")
    files = filenr_text.split()
    return {"load": [float(x) for x in load[:3]] if len(load) >= 3 else [],
            "sched_running": int(running or 0), "sched_total": int(total or 0),
            "files_open": int(files[0]) if files and files[0].isdigit() else None,
            "conntrack": {"count": integer(conntrack_count), "max": integer(conntrack_max)}}


def iface_kind(name, base):
    if name.startswith("tailscale"):
        return "tailscale"
    if name.startswith("veth"):
        return "container"
    if name == "docker0" or name.startswith("br-"):
        return "bridge"
    if (Path(base) / "wireless").exists() or name.startswith("wl"):
        return "wifi"
    if name.startswith(("en", "eth")):
        return "ethernet"
    return "other"


def ip_addresses(ip_json_text):
    """{interface: ["192.168.1.5/24", ...]} from `ip -j addr`, without link-local IPv6."""
    try:
        data = json.loads(ip_json_text or "[]")
    except ValueError:
        return {}
    out = {}
    for item in data:
        addrs = [f"{a['local']}/{a.get('prefixlen', '')}".rstrip("/") for a in item.get("addr_info", [])
                 if a.get("local") and a.get("scope") != "link"]
        if addrs:
            out[item.get("ifname", "")] = addrs
    return out


def interface_stats(dev_text, sys_root="/sys", addresses=None):
    """Every network interface with its counters, link state and addresses. Container veth pairs
    are one summed row: there is one per container and they are not worth a line each."""
    rows, veth = [], None
    for line in dev_text.splitlines()[2:]:
        name, _, rest = line.partition(":")
        name, f = name.strip(), rest.split()
        if not name or name == "lo" or len(f) < 16:
            continue
        base = Path(sys_root) / "class/net" / name
        kind = iface_kind(name, base)
        counters = {"rx_bytes": int(f[0]), "rx_packets": int(f[1]), "rx_errors": int(f[2]), "rx_drops": int(f[3]),
                    "tx_bytes": int(f[8]), "tx_packets": int(f[9]), "tx_errors": int(f[10]), "tx_drops": int(f[11])}
        if kind == "container":
            veth = veth or {"name": "containers", "kind": "container", "state": "up", "count": 0, **{k: 0 for k in counters}}
            veth["count"] += 1
            for k, v in counters.items():
                veth[k] += v
            continue
        speed = integer(read(base / "speed"))
        rows.append({"name": name, "kind": kind, "state": read(base / "operstate", "unknown"),
                     "mtu": integer(read(base / "mtu")), "speed_mbps": speed if speed and speed > 0 else None,
                     "addresses": (addresses or {}).get(name, []), **counters})
    if veth:
        rows.append(veth)
    return rows


def split_nmcli(line):
    """Fields of one `nmcli -t` line: colons and backslashes inside a field are backslash-escaped."""
    parts, current, escaped = [], "", False
    for ch in line:
        if escaped:
            current, escaped = current + ch, False
        elif ch == "\\":
            escaped = True
        elif ch == ":":
            parts.append(current)
            current = ""
        else:
            current += ch
    parts.append(current)
    return parts


def first_number(text):
    m = re.match(r"\s*(\d+)", text or "")
    return int(m.group(1)) if m else None


def wifi_info(nmcli_text, wireless_text="", iface=""):
    """The connected Wi-Fi network: name, band, channel, link rate and live signal."""
    info = None
    for line in (nmcli_text or "").splitlines():
        f = split_nmcli(line)
        if len(f) >= 7 and f[0] == "*":
            freq = first_number(f[4])
            info = {"ssid": f[1], "signal_pct": first_number(f[2]), "rate_mbps": first_number(f[3]), "freq_mhz": freq,
                    "channel": first_number(f[5]), "security": f[6],
                    "band": None if freq is None else "6 GHz" if freq >= 5925 else "5 GHz" if freq >= 4900 else "2.4 GHz"}
            break
    if info is None:
        return None
    for line in (wireless_text or "").splitlines()[2:]:     # live, unlike nmcli's cached scan
        name, _, rest = line.partition(":")
        values = rest.split()
        if name.strip() == iface and len(values) >= 3:
            try:
                info["signal_dbm"] = int(float(values[2]))
                noise = int(float(values[3]))
                info["noise_dbm"] = noise if noise > -200 else None      # the driver reports -256 for "no reading"
            except (ValueError, IndexError):
                pass
    return info


def tcp_stats(snmp_text, sockstat_text):
    """TCP connection counts and health from /proc/net/snmp and /proc/net/sockstat."""
    names = values = None
    for line in (snmp_text or "").splitlines():
        if line.startswith("Tcp:"):
            if names is None:
                names = line.split()[1:]
            else:
                values = line.split()[1:]
    tcp = dict(zip(names or [], (int(v) for v in values or []))) if names and values else {}
    sock = re.search(r"^TCP: inuse (\d+) orphan (\d+) tw (\d+)", sockstat_text or "", re.M)
    return {"established": tcp.get("CurrEstab"), "active_opens": tcp.get("ActiveOpens"),
            "passive_opens": tcp.get("PassiveOpens"), "failed_attempts": tcp.get("AttemptFails"),
            "resets": tcp.get("EstabResets"), "in_segments": tcp.get("InSegs"), "out_segments": tcp.get("OutSegs"),
            "retransmitted": tcp.get("RetransSegs"), "in_errors": tcp.get("InErrs"),
            "in_use": int(sock.group(1)) if sock else None, "time_wait": int(sock.group(3)) if sock else None}


REAL_FILESYSTEMS = {"ext2", "ext3", "ext4", "xfs", "btrfs", "f2fs", "vfat", "exfat", "ntfs", "ntfs3", "zfs"}


def filesystems(mounts_text, statvfs=None):
    """Mounted disk filesystems with space and inode use (Docker's overlays and tmpfs are left out)."""
    statvfs = statvfs or os.statvfs
    seen, rows = set(), []
    for line in (mounts_text or "").splitlines():
        f = line.split()
        if len(f) < 4 or f[2] not in REAL_FILESYSTEMS or f[0] in seen:
            continue
        seen.add(f[0])
        mount = f[1].replace("\\040", " ")
        try:
            st = statvfs(mount)
        except OSError:
            continue
        total = st.f_blocks * st.f_frsize
        rows.append({"mount": mount, "device": f[0], "fstype": f[2], "total": total,
                     "used": total - st.f_bfree * st.f_frsize, "avail": st.f_bavail * st.f_frsize,
                     "inodes": st.f_files, "inodes_used": st.f_files - st.f_ffree,
                     "readonly": "ro" in f[3].split(",")})
    return sorted(rows, key=lambda r: (r["mount"] != "/", r["mount"]))


def block_devices(sys_root="/sys"):
    """Whole disks: model, size, kind and (for NVMe) controller state and firmware."""
    rows = []
    block = Path(sys_root) / "block"
    if not block.exists():
        return rows
    for disk in sorted(block.iterdir()):
        if SKIP_DISKS.match(disk.name):
            continue
        parts = sorted(p.name for p in disk.glob(disk.name + "*") if (p / "partition").exists())
        ctrl = re.match(r"(nvme\d+)n\d+$", disk.name)
        nvme = Path(sys_root) / "class/nvme" / ctrl.group(1) if ctrl else None
        rows.append({"name": disk.name, "model": read(disk / "device/model") or (read(nvme / "model") if nvme else None),
                     "size": (integer(read(disk / "size")) or 0) * 512, "ssd": read(disk / "queue/rotational") == "0",
                     "removable": read(disk / "removable") == "1", "partitions": len(parts),
                     "state": read(nvme / "state") if nvme else None,
                     "firmware": read(nvme / "firmware_rev") if nvme else None})
    return rows


def sensor_list(root="/sys/class/hwmon"):
    """Every temperature sensor with its own limits: [{chip, label, temp_c, max_c, crit_c}]."""
    rows = []
    for mon in sorted(Path(root).glob("hwmon*")):
        chip = read(mon / "name", "") or mon.name
        for sensor in sorted(mon.glob("temp*_input")):
            value = number(read(sensor), 1000)
            if value is None or not 0 < value < 125:
                continue
            base = sensor.name[: -len("_input")]
            rows.append({"chip": chip, "label": read(mon / f"{base}_label", base), "temp_c": value,
                         "max_c": number(read(mon / f"{base}_max"), 1000), "crit_c": number(read(mon / f"{base}_crit"), 1000)})
    return rows


def fan_list(acpi_root="/sys/bus/acpi/devices/APP0001:00", hwmon_root="/sys/class/hwmon"):
    """Fans: [{rpm, min, max, target}]. The T2 Macs' fans sit on the SMC's ACPI device, not in hwmon."""
    fans = []
    for base in (Path(acpi_root), *sorted(Path(hwmon_root).glob("hwmon*"))):
        for sensor in sorted(base.glob("fan*_input")):
            n = sensor.name[: -len("_input")]
            rpm = integer(read(sensor))
            if rpm is not None:
                fans.append({"rpm": rpm, "min": integer(read(base / f"{n}_min")), "max": integer(read(base / f"{n}_max")),
                             "target": integer(read(base / f"{n}_output"))})
        if fans:
            break
    return fans


def battery_detail(root="/sys/class/power_supply"):
    """Battery and mains: charge, health, wear, power flow and the time left; None without a battery."""
    supplies = sorted(Path(root).glob("*"))
    ac = any(read(s / "type") == "Mains" and read(s / "online") == "1" for s in supplies)
    for s in supplies:
        if read(s / "type") != "Battery":
            continue
        status = read(s / "status", "Unknown")
        volts, amps, watts = number(read(s / "voltage_now"), 1e6), number(read(s / "current_now"), 1e6), number(read(s / "power_now"), 1e6)
        unit = "charge" if read(s / "charge_full") else "energy"      # microamp-hours or microwatt-hours
        now, full, design = (number(read(s / f"{unit}_{k}")) for k in ("now", "full", "full_design"))
        if watts is None and volts is not None and amps is not None:
            watts = volts * abs(amps)
        # the rate in the same unit as `now`: microwatts for energy (µWh), microamps for charge (µAh)
        flow = (watts * 1e6 if unit == "energy" and watts is not None
                else abs(amps) * 1e6 if unit == "charge" and amps is not None else None)
        left = None
        if flow and now is not None and full is not None:
            hours = (now / flow if status == "Discharging" else (full - now) / flow if status == "Charging" else None)
            left = int(hours * 3600) if hours is not None and hours >= 0 else None
        volt_design = number(read(s / "voltage_min_design"), 1e6)
        wh = (lambda v: None if v is None else round(v / 1e6 * (volt_design or volts or 0) if unit == "charge" else v / 1e6, 1))
        percent, cycles = number(read(s / "capacity")), number(read(s / "cycle_count"))
        return {"percent": None if percent is None else int(percent), "status": status, "ac_online": ac,
                "cycles": None if cycles is None else int(cycles), "technology": read(s / "technology"),
                "health_pct": round(100 * full / design, 1) if full and design else None,
                "capacity_wh": wh(full), "design_wh": wh(design),
                "voltage_v": volts, "current_a": amps, "power_w": None if watts is None else round(watts, 2),
                "temp_c": number(read(s / "temp"), 10), "seconds_left": left,
                "limit": read(s / "charge_control_end_threshold")}
    return None


def project_rollup(items):
    """One row per compose project: containers, how many run, and their summed CPU and memory."""
    projects = {}
    for c in items:
        p = projects.setdefault(c.get("project") or "-", {"name": c.get("project") or "-", "total": 0, "running": 0,
                                                          "unhealthy": 0, "cpu": 0.0, "mem": 0})
        p["total"] += 1
        p["running"] += c.get("state") == "running"
        p["unhealthy"] += "unhealthy" in c.get("status", "")
        p["cpu"] = round(p["cpu"] + (c.get("cpu") or 0), 2)
        p["mem"] += c.get("mem") or 0
    return sorted(projects.values(), key=lambda p: (-p["mem"], p["name"]))


def docker_inspect_lines(text):
    """`docker inspect --format` lines (name|restarts|started|health|pid|policy|image) by container name."""
    out = {}
    for line in (text or "").splitlines():
        f = line.split("|")
        if len(f) < 7:
            continue
        image = f[6]
        out[f[0].lstrip("/")] = {"restarts": integer(f[1]), "started": iso_epoch(f[2]), "health": f[3], "pid": integer(f[4]),
                                 "policy": f[5], "image": re.sub(r"@sha256:[0-9a-f]+", "", image)}
    return out


def docker_df(text):
    """`docker system df --format '{{json .}}'`: what Docker's images, volumes and build cache take."""
    out = {}
    for line in (text or "").splitlines():
        try:
            row = json.loads(line)
        except ValueError:
            continue
        kind = {"Images": "images", "Containers": "containers", "Local Volumes": "volumes", "Build Cache": "build_cache"}.get(row.get("Type"))
        if kind:
            out[kind] = {"count": integer(row.get("TotalCount")), "active": integer(row.get("Active")),
                         "size": parse_size(row.get("Size", "")), "reclaimable": parse_size((row.get("Reclaimable") or "").split(" ")[0])}
    return out


def cgroup_extras(ids, prev, now, root=CGROUP_ROOT):
    """Per container: process count, memory limit and block-IO rates (cgroup v2). The first run has no rates."""
    out, io_now = {}, {}
    span = now - prev.get("at", now)
    for name, cid in ids.items():
        base = root / "system.slice" / f"docker-{cid}.scope"
        try:
            pids = int((base / "pids.current").read_text())
        except (OSError, ValueError):
            continue
        limit = read(base / "memory.max")
        rbytes = wbytes = 0
        for line in (read(base / "io.stat", "") or "").splitlines():
            for field in line.split()[1:]:
                key, _, value = field.partition("=")
                if key == "rbytes" and value.isdigit():
                    rbytes += int(value)
                elif key == "wbytes" and value.isdigit():
                    wbytes += int(value)
        io_now[name] = [rbytes, wbytes]
        old = prev.get("io", {}).get(name)
        rates = {"blk_read": 0, "blk_write": 0}
        if old and span > 0:
            rates = {"blk_read": round(max(rbytes - old[0], 0) / span), "blk_write": round(max(wbytes - old[1], 0) / span)}
        out[name] = {"pids": pids, "mem_limit": int(limit) if limit and limit.isdigit() else None, **rates}
    return out, {"at": now, "io": io_now}


def container_net(pid, proc_root="/proc"):
    """Bytes received and sent by a container (its eth interfaces), via its main process's /proc/<pid>/net/dev."""
    rx = tx = 0
    for line in (read(Path(proc_root) / str(pid) / "net/dev", "") or "").splitlines()[2:]:
        name, _, rest = line.partition(":")
        f = rest.split()
        if name.strip() != "lo" and len(f) >= 9:
            rx, tx = rx + int(f[0]), tx + int(f[8])
    return rx, tx


DB_QUERY = """select json_build_object(
 'version', current_setting('server_version'),
 'size', pg_database_size(current_database()),
 'uptime_s', extract(epoch from now() - pg_postmaster_start_time())::bigint,
 'connections', (select count(*) from pg_stat_activity where backend_type = 'client backend' and pid <> pg_backend_pid()),
 'active', (select count(*) from pg_stat_activity where backend_type = 'client backend' and state = 'active' and pid <> pg_backend_pid()),
 'idle_in_transaction', (select count(*) from pg_stat_activity where state like 'idle in transaction%'),
 'max_connections', current_setting('max_connections')::int,
 'commits', d.xact_commit, 'rollbacks', d.xact_rollback, 'deadlocks', d.deadlocks, 'temp_bytes', d.temp_bytes,
 'blocks_hit', d.blks_hit, 'blocks_read', d.blks_read,
 'rows_returned', d.tup_returned, 'rows_inserted', d.tup_inserted, 'rows_updated', d.tup_updated, 'rows_deleted', d.tup_deleted,
 'users', case when to_regclass('auth.users') is not null then (select count(*) from auth.users) end,
 'storage_objects', case when to_regclass('storage.objects') is not null then (select count(*) from storage.objects) end
) from pg_stat_database d where d.datname = current_database()"""


def db_stats(raw, prev, now):
    """Postgres numbers from DB_QUERY's JSON, with transactions per second and the cache hit ratio.
    Returns (stats, reading to pass as `prev` next time)."""
    try:
        d = json.loads(raw)
    except (TypeError, ValueError):
        return {"error": "no answer from the database"}, prev
    commits = (d.get("commits") or 0) + (d.get("rollbacks") or 0)
    span = now - prev.get("at", now)
    d["tps"] = round(max(commits - prev["commits"], 0) / span, 1) if prev.get("commits") is not None and span > 0 else 0.0
    reads = (d.get("blocks_hit") or 0) + (d.get("blocks_read") or 0)
    d["cache_hit_pct"] = round(100 * (d.get("blocks_hit") or 0) / reads, 2) if reads else None
    return d, {"at": now, "commits": commits}


def db_container(ids):
    """The Postgres container's name among the running containers (supabase-db)."""
    return next((n for n in sorted(ids) if re.search(r"(^|[-_.])db($|[-_.]\d)", n) and "supabase" in n), None)


def tailscale_detail(status_text, now):
    """Version, health warnings, key expiry and every device on the tailnet (no addresses beyond Tailscale's own)."""
    try:
        data = json.loads(status_text or "")
    except ValueError:
        return {}
    me = data.get("Self") or {}

    peers, funnel = [], 0
    for p in (data.get("Peer") or {}).values():
        if is_funnel_node(p):
            funnel += 1
            continue
        route = "direct" if p.get("CurAddr") else f"relay {p['Relay']}" if p.get("Relay") else "idle"
        peers.append({"name": p.get("HostName") or (p.get("DNSName") or "").split(".")[0], "os": p.get("OS", ""),
                      "ip": (p.get("TailscaleIPs") or [""])[0], "online": bool(p.get("Online")),
                      "last_seen": iso_epoch(p.get("LastSeen")), "rx_bytes": p.get("RxBytes", 0), "tx_bytes": p.get("TxBytes", 0),
                      "route": route if p.get("Online") else "offline", "exit_node": bool(p.get("ExitNode"))})
    return {"version": (data.get("Version") or "").split("-")[0], "health": [str(h)[:160] for h in data.get("Health") or []],
            "key_expiry": iso_epoch(me.get("KeyExpiry")), "tailnet": (data.get("CurrentTailnet") or {}).get("Name", ""),
            "update_available": bool(data.get("ClientVersion") and not data["ClientVersion"].get("RunningLatest", True)),
            "funnel_relays": funnel,
            "peers": sorted(peers, key=lambda p: (not p["online"], p["name"]))}


def system_info(state, now, containers_now, ids, ts_text, proc_root="/proc", sys_root="/sys"):
    """The detail sections: host facts, CPU, memory, storage, network, power, Docker, database."""
    p = Path(proc_root)
    meminfo = read(p / "meminfo", "")
    uptime = number((read(p / "uptime", "0") or "0").split()[0].split(".")[0]) or 0
    addresses = cached(state, "ip_addr", 30, now, lambda: ip_addresses(run("ip", "-j", "addr")))
    interfaces = part(interface_stats, read(p / "net/dev", ""), sys_root, addresses)
    wifi_iface = next((i["name"] for i in interfaces or [] if i["kind"] == "wifi"), "")
    uname = os.uname() if hasattr(os, "uname") else None
    inspected = cached(state, "docker_inspect", 10, now, lambda: docker_inspect_lines(run(
        "docker", "inspect", "--format",
        "{{.Name}}|{{.RestartCount}}|{{.State.StartedAt}}|{{if .State.Health}}{{.State.Health.Status}}{{end}}|"
        "{{.State.Pid}}|{{.HostConfig.RestartPolicy.Name}}|{{.Config.Image}}", *ids.values(), timeout=10) if ids else ""))
    extras, state["cgroup_extras"] = cgroup_extras(ids, state.get("cgroup_extras", {}), now)
    for item in containers_now:          # per-container details, merged into the container rows
        name = item["name"]
        info = inspected.get(name, {})
        if info.get("pid") and item.get("state") == "running":
            prev = state.setdefault("net_prev", {}).get(name)
            rx, tx = container_net(info["pid"], proc_root)
            span = now - prev["at"] if prev else 0
            item["net_rx"] = round(max(rx - prev["rx"], 0) / span) if prev and span > 0 else 0
            item["net_tx"] = round(max(tx - prev["tx"], 0) / span) if prev and span > 0 else 0
            state["net_prev"][name] = {"at": now, "rx": rx, "tx": tx}
        item.update({k: v for k, v in info.items() if k != "pid"})
        item.update(extras.get(name, {}))
    db_name = db_container(ids)
    db = None
    if db_name:
        raw = cached(state, "db_raw", 10, now, lambda: run(
            "docker", "exec", "-e", "PGOPTIONS=-c statement_timeout=2000 -c default_transaction_read_only=on", db_name,
            "psql", "-U", "postgres", "-d", "postgres", "-X", "-A", "-t", "-c", DB_QUERY, timeout=8))
        db, state["db"] = db_stats(raw, state.get("db", {}), now) if raw else ({"error": "the database did not answer"}, state.get("db", {}))
    return {
        "host": {"os": part(os_pretty, read("/etc/os-release", "")), "hostname": socket.gethostname(),
                 "arch": uname.machine if uname else "", "firmware": read("/sys/class/dmi/id/bios_version"),
                 "boot_time": int(now - uptime), "sessions": cached(state, "who", 20, now, lambda: logged_in(run("who") or "")),
                 "timezone": cached(state, "timezone", 600, now, lambda: (run("timedatectl", "show", "-p", "Timezone", "--value") or "").strip()),
                 "docker": cached(state, "docker_version", 600, now, lambda: (run("docker", "version", "--format", "{{.Server.Version}}") or "").strip())},
        "cpu": {**(part(cpu_static, read(p / "cpuinfo", ""), sys_root) or {}), "pressure": part(read_pressure, p / "pressure"),
                "throttle": part(throttle_counts, sys_root)},
        "memory": part(memory_detail, meminfo), "swap": part(swap_devices, read(p / "swaps", "")),
        "kernel": part(kernel_facts, read(p / "loadavg", ""), read(p / "sys/fs/file-nr", ""),
                       read(p / "sys/net/netfilter/nf_conntrack_count"), read(p / "sys/net/netfilter/nf_conntrack_max")),
        "storage": {"filesystems": part(filesystems, read(p / "mounts", "")), "disks": part(block_devices, sys_root),
                    "docker": cached(state, "docker_df", 900, now, lambda: docker_df(run(
                        "docker", "system", "df", "--format", "{{json .}}", timeout=20)))},
        "network": {"interfaces": interfaces,
                    "wifi": cached(state, "wifi", 30, now, lambda: wifi_info(run(
                        "nmcli", "-t", "-f", "IN-USE,SSID,SIGNAL,RATE,FREQ,CHAN,SECURITY", "dev", "wifi", "list", "--rescan", "no"),
                        read(p / "net/wireless", ""), wifi_iface)) if wifi_iface else None,
                    "tcp": part(tcp_stats, read(p / "net/snmp", ""), read(p / "net/sockstat", ""))},
        "sensors": {"temps": part(sensor_list, f"{sys_root}/class/hwmon"), "fans": part(fan_list, f"{sys_root}/bus/acpi/devices/APP0001:00", f"{sys_root}/class/hwmon")},
        "power": part(battery_detail, f"{sys_root}/class/power_supply"),
        "docker": {"projects": project_rollup(containers_now)},
        "tailscale": part(tailscale_detail, ts_text, now),
        "db": db,
    }


def collect(state=None):
    """One health snapshot. `state` carries what a 2-second cadence must not redo every run:
    the last cgroup reading (for CPU %) and cached slow values (apt, the public route)."""
    state = {} if state is None else state
    now = time.time()
    load = os.getloadavg()
    disk = shutil.disk_usage("/")
    conf = parse_env(read(CONF, ""))
    env = parse_env(read(SUPABASE_ENV, ""))
    t2_modules = {m: Path(f"/sys/module/{m}").exists() for m in ("brcmfmac", "applesmc")}
    # The T2 keyboard/trackpad driver: t2bce_vhci since kernel 6.18, apple_bce before.
    t2_modules["keyboard"] = any(Path(f"/sys/module/{m}").exists() for m in ("t2bce_vhci", "apple_bce"))
    ps = run("docker", "ps", "-a", "--no-trunc", "--format", "{{json .}}")
    ids = container_ids(ps)
    stats, state["cgroup"] = cgroup_stats(ids, state.get("cgroup", {}), now)
    if ids and not stats:   # no cgroup v2 numbers: `docker stats` is slow (~2 s) but always there
        stats = container_stats(run("docker", "stats", "--no-stream", "--format", "{{json .}}", timeout=30))
    domain = conf.get("PUBLIC_DOMAIN", "")
    ts_text = run("tailscale", "status", "--json")
    data = {
        "generated_at": int(now),
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
            "updates_pending": cached(state, "updates", UPDATES_TTL_S, now, count_updates),
            "reboot_required": Path("/run/reboot-required").exists(),
        },
        "tailscale": tailscale(ts_text),
        "containers": with_logs(with_stats(containers(ps), stats)),
        "public_domain": domain,
        "public_ok": cached(state, f"public:{domain}", PUBLIC_TTL_S, now, lambda: public_ok(domain)),
        "macserver_update": self_update(),
        "clock_synced": (run("timedatectl", "show", "-p", "NTPSynchronized", "--value") or "").strip() == "yes",
        "site_url": conf.get("SITE_URL", ""),
        "api_url": env.get("SUPABASE_PUBLIC_URL", ""),
        "public_keys": {k: env[k] for k in PUBLIC_KEYS if env.get(k)},
        "claude": claude_session(read(CLAUDE_LOG, ""), unit_state("macserver-claude.service"),
                                 Path("/usr/local/bin/claude").exists()),
    }
    mode, after_s = idle_settings(conf)
    try:   # likewise the live graphs: a failure here leaves the health data intact
        data["live"], state["live"] = live_stats(state.get("live", {}), now)
    except Exception as err:   # noqa: BLE001
        data["live"] = {"error": repr(err)[:200]}
    try:   # the detail pages' sections likewise
        data["system"] = system_info(state, now, data["containers"], ids, ts_text)
    except Exception as err:   # noqa: BLE001
        data["system"] = {"error": repr(err)[:200]}
    try:   # the incident pages' extras must never cost the basic health data
        data["debug"] = debug_info(state, now, ps, ids, data["containers"], tailscale_peers(ts_text))
    except Exception as err:   # noqa: BLE001
        data["debug"] = {"error": repr(err)[:200]}
    active, reasons = idle_activity(data)
    data["idle"] = idle_track(state, now, active, after_s, mode)
    data["idle"]["reasons"] = reasons if active else []
    try:   # lockdown badge only; never costs the health data
        data["doomsday"] = doomsday_state()
    except Exception:   # noqa: BLE001
        data["doomsday"] = {"mode": "on", "state": "normal"}
    return data


def write_atomic(path, data):
    path.parent.mkdir(mode=0o755, parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, indent=1))
    os.chmod(tmp, 0o644)
    os.replace(tmp, path)


def main():
    state = load_state()
    write_atomic(Path(sys.argv[1]) if len(sys.argv) > 1 else OUT, collect(state))
    try:
        save_state(state)
    except OSError:
        pass   # the next run just starts without the previous reading


if __name__ == "__main__":
    main()
