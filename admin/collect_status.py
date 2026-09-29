#!/usr/bin/env python3
"""Collect MacServer health into a JSON file for the admin page.

Runs as root from macserver-status.timer (every 2 s). The output holds no secrets: only the
public (anon/publishable) app keys, which are meant to ship inside client apps.
"""
import calendar
import collections
import ipaddress
import json
import os
import re
from pathlib import Path
import shutil
import socket
import stat
import subprocess
import sys
import threading
import time
import urllib.request

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


def tailscale(status_text):
    try:
        data = json.loads(status_text or "")
    except ValueError:
        return {"state": "unknown"}
    me = data.get("Self") or {}
    peers = data.get("Peer") or {}
    return {"state": data.get("BackendState", "unknown"), "name": (me.get("DNSName") or "").rstrip("."),
            "ips": me.get("TailscaleIPs") or [], "online": bool(me.get("Online")),
            "peers_online": sum(1 for p in peers.values() if p.get("Online"))}


SIZE_UNITS = {"B": 1, "KB": 1e3, "MB": 1e6, "GB": 1e9, "TB": 1e12,
              "KIB": 1024, "MIB": 1024 ** 2, "GIB": 1024 ** 3, "TIB": 1024 ** 4}


def parse_size(text):
    m = re.match(r"\s*([\d.]+)\s*([a-zA-Z]+)", text or "")
    if not m:
        return 0
    return int(float(m.group(1)) * SIZE_UNITS.get(m.group(2).upper(), 1))


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
    try:   # the incident pages' extras must never cost the basic health data
        data["debug"] = debug_info(state, now, ps, ids, data["containers"], tailscale_peers(ts_text))
    except Exception as err:   # noqa: BLE001
        data["debug"] = {"error": repr(err)[:200]}
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
