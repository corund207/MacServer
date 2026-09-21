#!/usr/bin/env python3
"""Collect bounded, non-secret local appliance status into one atomic snapshot."""
import argparse
import json
import os
from pathlib import Path
import stat
import subprocess
import tempfile
from datetime import datetime, timezone

SERVICES = ("db", "auth", "rest", "api-gw", "functions", "storage", "realtime", "studio", "meta", "imgproxy")
MAX_INPUT = 128 * 1024


def safe_json(path):
    if not path:
        return None
    flags = os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK
    fd = os.open(path, flags)
    try:
        metadata = os.fstat(fd)
        if not stat.S_ISREG(metadata.st_mode) or metadata.st_size > MAX_INPUT or metadata.st_nlink != 1:
            raise ValueError("unsafe status input")
        raw = os.read(fd, MAX_INPUT + 1)
        if len(raw) > MAX_INPUT:
            raise ValueError("status input too large")
        value = json.loads(raw)
        return value if isinstance(value, dict) else None
    finally:
        os.close(fd)


def command(argv, timeout=8):
    result = subprocess.run(argv, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                            stderr=subprocess.DEVNULL, timeout=timeout, check=False,
                            env={"PATH": "/usr/sbin:/usr/bin:/sbin:/bin", "LANG": "C.UTF-8"})
    if result.returncode or len(result.stdout) > MAX_INPUT:
        raise RuntimeError("collector command unavailable")
    if argv[0].endswith("docker"):
        return parse_docker_output(result.stdout)
    return json.loads(result.stdout)


def parse_docker_output(raw):
    if not raw.strip():
        return []
    try:
        value = json.loads(raw)
        return value if isinstance(value, list) else [value]
    except json.JSONDecodeError:
        return [json.loads(line) for line in raw.splitlines() if line.strip()]


def docker_services(compose, runner=command):
    fallback = [{"name": name, "state": "unavailable", "detail": "No container observation"} for name in SERVICES]
    try:
        rows = runner(["/usr/bin/docker", "compose", "--env-file", "/etc/macserver/supabase.env", "-f", compose, "--profile", "*", "ps", "--all", "--format", "json"])
        if not isinstance(rows, list):
            return fallback
        indexed = {str(row.get("Service", "")): row for row in rows if isinstance(row, dict)}
        result = []
        for name in SERVICES:
            row = indexed.get(name)
            if not row:
                result.append({"name": name, "state": "unavailable", "detail": "Container not observed"})
                continue
            runtime = str(row.get("State", "")).lower()
            health = str(row.get("Health", "")).lower()
            state = "healthy" if runtime == "running" and health in ("", "healthy") else "degraded" if runtime == "running" else "failed"
            detail = f"container {runtime or 'unknown'}" + (f", health {health}" if health else "")
            result.append({"name": name, "state": state, "detail": detail[:160]})
        return result
    except (OSError, ValueError, RuntimeError, subprocess.TimeoutExpired, json.JSONDecodeError):
        return fallback


def tailscale(runner=command):
    try:
        value = runner(["/usr/bin/tailscale", "status", "--json"])
        running = value.get("BackendState") == "Running"
        addresses = value.get("TailscaleIPs")
        healthy = running and isinstance(addresses, list) and bool(addresses)
        return {"state": "healthy" if healthy else "degraded",
                "detail": "Daemon running with assigned address" if healthy else "Daemon state or address needs review"}
    except (OSError, ValueError, RuntimeError, subprocess.TimeoutExpired, json.JSONDecodeError):
        return {"state": "unavailable", "detail": "Tailscale status unavailable"}


def external_status(path, kind):
    try:
        value = safe_json(path)
        allowed = {"healthy", "degraded", "failed", "unavailable"}
        state = value.get("state") if value.get("state") in allowed else "unavailable"
        detail = value.get("detail") if isinstance(value.get("detail"), str) else f"Invalid {kind} detail"
        result = {"state": state, "detail": detail[:160]}
        if kind == "backup":
            last = value.get("lastSuccess")
            result["lastSuccess"] = last if isinstance(last, str) else None
        else:
            for name in ("perMinute", "errorsPerMinute"):
                candidate = value.get(name)
                result[name] = candidate if isinstance(candidate, (int, float)) and candidate >= 0 else None
        return result
    except (OSError, ValueError, json.JSONDecodeError, TypeError):
        result = {"state": "unavailable", "detail": f"No valid {kind} evidence"}
        result.update({"lastSuccess": None} if kind == "backup" else {"perMinute": None, "errorsPerMinute": None})
        return result


def snapshot(compose, backup_status, request_status, runner=command, now=None):
    now = now or datetime.now(timezone.utc)
    services = docker_services(compose, runner)
    backup = external_status(backup_status, "backup")
    requests = external_status(request_status, "request")
    tailnet = tailscale(runner)
    alerts = []
    for label, item in (("Tailscale", tailnet), ("Backup", backup), ("Request telemetry", requests)):
        if item["state"] != "healthy":
            alerts.append({"severity": "critical" if item["state"] == "failed" else "warning",
                           "title": f"{label} {item['state']}", "detail": item["detail"], "at": now.isoformat()})
    failed = [service["name"] for service in services if service["state"] == "failed"]
    if failed:
        alerts.append({"severity": "critical", "title": "Service failure reported",
                       "detail": ", ".join(failed)[:240], "at": now.isoformat()})
    return {"format": 1, "observedAt": now.isoformat(), "collector": "macserver local collector",
            "tailscale": tailnet, "backup": backup, "requests": requests,
            "services": services, "alerts": alerts[:20]}


def atomic_write(path, value):
    target = Path(path)
    if not target.is_absolute() or target.name in ("", ".", ".."):
        raise ValueError("output must be an absolute file path")
    parent = target.parent
    if not parent.is_dir() or parent.is_symlink():
        raise ValueError("output parent must be an existing real directory")
    payload = (json.dumps(value, separators=(",", ":")) + "\n").encode()
    if len(payload) > MAX_INPUT:
        raise ValueError("snapshot too large")
    fd, temporary = tempfile.mkstemp(prefix=".status-", dir=parent)
    try:
        os.fchmod(fd, 0o640)
        os.write(fd, payload); os.fsync(fd); os.close(fd); fd = -1
        os.replace(temporary, target)
        directory_fd = os.open(parent, os.O_RDONLY | os.O_DIRECTORY)
        try: os.fsync(directory_fd)
        finally: os.close(directory_fd)
    finally:
        if fd >= 0: os.close(fd)
        try: os.unlink(temporary)
        except FileNotFoundError: pass


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--compose", default="/opt/macserver/infra/supabase/compose.json")
    parser.add_argument("--backup-status", default="/var/lib/macserver-backup/status.json")
    parser.add_argument("--request-status", default="/var/lib/macserver-ingress/status.json")
    parser.add_argument("--output", default="/run/macserver/status.json")
    args = parser.parse_args()
    for value in (args.compose, args.backup_status, args.request_status, args.output):
        if not Path(value).is_absolute():
            parser.error("all paths must be absolute")
    try:
        atomic_write(args.output, snapshot(args.compose, args.backup_status, args.request_status))
    except (OSError, ValueError) as error:
        parser.exit(1, f"Collector failed safely: {error}\n")


if __name__ == "__main__":
    main()
