#!/usr/bin/env python3
"""MacServer doomsday protocol: detect intrusion-like behaviour, isolate, recover.

Detection reads the status collector's debug section (flows, listeners, requests)
plus a root-process scan, and scores findings. Trusted activity never scores:
Tailscale SSH and the tailnet, loopback, DNS/NTP/Tailscale control traffic,
Tailscale direct LAN connections (UDP 41641) and ephemeral-port hole-punching,
and the allowlisted AI-agent / owner processes (including the listeners of
allowlisted services, e.g. a self-hosted remote desktop).

Isolation itself lives in host/macserver-doomsday.sh (root shell): this module
only decides *whether* to isolate, verifies the Google Authenticator (TOTP)
unlock codes, and builds the redacted alert payload sent through a GitHub
Action *before* the network is cut.

Standard library only. All pure logic is unit-tested in tests/test_doomsday.py.
"""
import base64
import hashlib
import hmac
import json
import os
import re
import struct
import time
from pathlib import Path

CONF = Path(os.environ.get("MACSERVER_CONF", "/etc/macserver/macserver.conf"))
RUN_STATE = Path(os.environ.get("MACSERVER_DOOMSDAY_RUN", "/run/macserver/doomsday.json"))
KEEP_STATE = Path(os.environ.get("MACSERVER_DOOMSDAY_STATE", "/var/lib/macserver/doomsday.json"))
STATUS = Path(os.environ.get("MACSERVER_STATUS", "/run/macserver/status.json"))
TOTP_FILE = Path(os.environ.get("MACSERVER_DOOMSDAY_TOTP", "/etc/macserver/doomsday-totp"))

# Ports that are normal on the public Internet / infra side. Anything else out
# to a public address scores. Tailscale control + tunnel keepalive never count.
TRUSTED_OUT_PORTS = {53, 80, 123, 443, 465, 587, 993, 3478, 41641}
# Tailscale peers connect directly over the LAN on UDP 41641, then keep
# punching UDP holes on ephemeral ports: that is the tailnet working as
# designed (tailscale status shows them as `direct <lan-ip>:41641`), not an
# intrusion. Client-side ephemeral outbound ports are likewise normal p2p
# traffic (Tailscale direct, WebRTC); server-side odd ports still score.
TAILSCALE_DIRECT_PORT = 41641
EPHEMERAL_PORT_MIN = 32768
TRUSTED_OUT_SVC = {"tailscale", "dns", "ntp", "stun", "https", "http", "smtp", "imap"}
# Listeners that may exist without scoring (loopback is always fine).
EXPECTED_LISTEN_PORTS = {22, 53, 123, 443, 41641, 5432, 6543, 8000, 8080, 8090, 8091, 8443}
# Processes that are part of normal operation (short names, as reported).
DEFAULT_TRUSTED_PROCS = {
    "tailscaled", "tailscale", "systemd", "bash", "sshd", "dbus-daemon", "cron",
    "docker", "dockerd", "containerd", "containerd-shim", "cloudflared", "caddy",
    "claude", "node", "python3", "macserver-admin", "dashboard", "collect_status",
    "fan-control", "idle", "doomsday", "nft", "ss", "ping", "curl", "git",
}
DEFAULT_TRUSTED_USERS = {"root", "macserver-admin", "macserver-console"}

SECRET_PATTERNS = [
    re.compile(r"eyJ[\w-]+\.[\w-]+\.[\w-]*"),
    re.compile(r"(?i)bearer\s+\S+"),
    re.compile(r"(?i)(password|passwd|secret|token|apikey|api_key|authorization)([\"'=: ]+)\S+"),
    re.compile(r"[A-Za-z0-9+/_-]{32,}"),
    re.compile(r"postgres(ql)?://[^\s]+"),
]


def redact(line):
    for pattern in SECRET_PATTERNS:
        if pattern.groups >= 2:
            line = pattern.sub(lambda m: f"{m.group(1)}{m.group(2)}[hidden]", line)
        else:
            line = pattern.sub("[hidden]", line)
    return line


def read_conf(path=None):
    path = CONF if path is None else path
    values = {}
    try:
        text = Path(path).read_text()
    except OSError:
        return values
    for line in text.splitlines():
        if "=" in line and not line.lstrip().startswith("#"):
            key, _, value = line.partition("=")
            values[key.strip()] = value.strip()
    return values


def doomsday_settings(conf):
    """(mode, threshold, consecutive) from macserver.conf."""
    mode = (conf.get("DOOMSDAY_MODE", "on") or "on").strip().lower()
    if mode not in ("on", "off"):
        mode = "on"
    try:
        threshold = int(conf.get("DOOMSDAY_SCORE", "6"))
    except ValueError:
        threshold = 6
    try:
        consecutive = int(conf.get("DOOMSDAY_CONSECUTIVE", "2"))
    except ValueError:
        consecutive = 2
    return mode, min(max(threshold, 1), 100), min(max(consecutive, 1), 10)


def read_allow(conf):
    """(trusted_procs, trusted_users) including the owner's account and extras."""
    procs = set(DEFAULT_TRUSTED_PROCS)
    for name in (conf.get("DOOMSDAY_TRUSTED_PROCS", "") or "").split(","):
        name = name.strip()[:32]
        if name:
            procs.add(name)
    users = set(DEFAULT_TRUSTED_USERS)
    for name in (conf.get("DOOMSDAY_TRUSTED_USERS", "") or "").split(","):
        name = name.strip()[:64]
        if name:
            users.add(name)
    owner = (conf.get("ADMIN_USER", "") or "").strip()
    if owner:
        users.add(owner)
    return procs, users


def load_state(path=KEEP_STATE):
    try:
        data = json.loads(Path(path).read_text())
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def save_state(state, path=KEEP_STATE):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(state, indent=1))
    os.chmod(tmp, 0o600)
    os.replace(tmp, path)


def score_findings(data, trusted_procs, trusted_users):
    """Score intrusion-like behaviour. Returns (score, [reasons]).

    `data` is shaped like the collector output: debug.flows.{in,out},
    debug.listeners, debug.requests.classes, live.procs. Trusted tailnet /
    loopback / infra traffic never scores; AI-agent and owner processes on
    the allowlist never score.
    """
    score, reasons = 0, []
    debug = data.get("debug") or {}
    flows = debug.get("flows") or {}
    for entry in flows.get("in") or []:
        kind = entry.get("kind")
        if kind in ("loopback", "tailnet", ""):
            continue  # Tailscale SSH and local traffic are verified, not unusual
        try:
            port = int(entry.get("port", 0) or 0)
        except (TypeError, ValueError):
            continue
        if port == TAILSCALE_DIRECT_PORT:
            continue  # Tailscale direct peer connection from the LAN
        if port == 22 and kind == "lan":
            score += 1  # LAN SSH is the emergency path: note it, do not fire on it alone
            reasons.append(f"LAN SSH connection from {entry.get('src', '?')}")
            continue
        n = int(entry.get("n", 1) or 1)
        score += 3
        reasons.append(f"inbound {kind} connection to port {port} from {entry.get('src', '?')} (x{n})")
    for entry in flows.get("out") or []:
        if entry.get("kind") != "public":
            continue
        who = entry.get("who", "")
        if who in ("tailscaled", "this Mac") and entry.get("svc") in TRUSTED_OUT_SVC:
            continue
        try:
            port = int(entry.get("port", 0) or 0)
        except (TypeError, ValueError):
            continue
        if port in TRUSTED_OUT_PORTS:
            continue  # HTTPS/DNS/NTP/mail/Tailscale to the Internet is normal
        if port >= EPHEMERAL_PORT_MIN:
            continue  # client-side ephemeral port: Tailscale direct, WebRTC, not a server C2 port
        if who in trusted_procs:
            continue  # verified agent / service process
        score += 2
        reasons.append(f"unexpected outbound {who or '?'} -> {entry.get('dst', '?')}:{port}")
    for row in debug.get("listeners") or []:
        scope = row.get("scope")
        if scope in ("loopback", "tailnet"):
            continue
        if (row.get("proc") or "") in trusted_procs:
            continue  # owner-allowlisted service (e.g. a self-hosted remote desktop)
        try:
            port = int(row.get("port", 0) or 0)
        except (TypeError, ValueError):
            continue
        if port in EXPECTED_LISTEN_PORTS:
            continue
        if scope in ("all", "public", "lan"):
            score += 2
            reasons.append(f"listener on {row.get('addr', '?')}:{port} ({row.get('proc', '?')}) reachable from {scope}")
    classes = ((debug.get("requests") or {}).get("classes")) or {}
    try:
        bad = int(classes.get("5xx", 0) or 0)
    except (TypeError, ValueError):
        bad = 0
    if bad >= 3:
        score += 2
        reasons.append(f"{bad} API 5xx errors in the last minute")
    for proc in ((data.get("live") or {}).get("procs")) or []:
        if proc.get("user") != "root":
            continue
        name = (proc.get("name") or "")[:32]
        if name in trusted_procs:
            continue
        score += 3
        reasons.append(f"unknown root process: {name} (pid {proc.get('pid', '?')})")
        break  # one is enough to report; the shell scan lists the rest
    _ = trusted_users  # users gate the shell-side process scan, not the JSON snapshot
    return score, reasons[:8]


def should_isolate(score, threshold, consecutive, state):
    """Hysteresis: fire only after `consecutive` over-threshold checks in a row."""
    if score >= threshold:
        hits = int(state.get("doomsday_hits", 0) or 0) + 1
    else:
        hits = 0
    state["doomsday_hits"] = hits
    return hits >= consecutive


# --- TOTP (Google Authenticator compatible) ------------------------------------
# RFC 6238, SHA-1, 30 s steps, 6 digits. No dependencies beyond the stdlib.

def generate_secret(nbytes=20):
    import secrets
    return base64.b32encode(secrets.token_bytes(nbytes)).decode().rstrip("=")


def normalize_secret(secret):
    return re.sub(r"[^A-Z2-7]", "", (secret or "").upper().replace(" ", ""))


def totp_code(secret, at=None, step=30, digits=6):
    key = base64.b32decode(normalize_secret(secret) + "=" * (-len(normalize_secret(secret)) % 8))
    counter = int((time.time() if at is None else at) // step)
    mac = hmac.new(key, struct.pack(">Q", counter), hashlib.sha1).digest()
    offset = mac[-1] & 0x0F
    code = struct.unpack(">I", mac[offset:offset + 4])[0] & 0x7FFFFFFF
    return str(code % (10 ** digits)).zfill(digits)


def verify_code(secret, code, at=None, skew=1, step=30):
    """Accept the code for the current step plus `skew` steps each side."""
    want = re.sub(r"\D", "", code or "")
    if len(want) != 6:
        return False
    now = time.time() if at is None else at
    base = int(now // step)
    for delta in range(-skew, skew + 1):
        if hmac.compare_digest(totp_code(secret, at=(base + delta) * step), want):
            return True
    return False


def otpauth_url(secret, account="macserver", issuer="MacServer"):
    return f"otpauth://totp/{issuer}:{account}?secret={normalize_secret(secret)}&issuer={issuer}"


# --- alert payload (sent through the GitHub Action before isolation) -----------

def build_alert(hostname, ips, reasons, score, trigger="auto", now=None):
    """Strings-only payload for the doomsday-alert workflow. Redacted."""
    now = int(time.time() if now is None else now)
    lines = [redact(str(r))[:160] for r in reasons][:8]
    return {
        "hostname": redact(str(hostname))[:120],
        "ip": ", ".join(redact(str(i))[:40] for i in ips)[:300],
        "trigger": redact(str(trigger))[:40],
        "score": str(int(score)),
        "details": " | ".join(lines)[:1200] or "no details captured",
        "timestamp": str(now),
    }


def check(source=STATUS, state=None):
    """One monitor tick. Returns (score, reasons, fire)."""
    state = {} if state is None else state
    conf = read_conf()
    mode, threshold, consecutive = doomsday_settings(conf)
    if mode == "off":
        return 0, [], False
    if str(state.get("state", "normal")) != "normal":
        return 0, [], False  # already isolated: the shell owns the state machine
    try:
        data = json.loads(Path(source).read_text())
    except (OSError, ValueError):
        return 0, [], False  # fail closed-safe: no data, no isolation
    procs, users = read_allow(conf)
    score, reasons = score_findings(data, procs, users)
    return score, reasons, should_isolate(score, threshold, consecutive, state)


def main(argv=None):
    import argparse
    parser = argparse.ArgumentParser(description="doomsday detection, TOTP and alert payload")
    sub = parser.add_subparsers(dest="cmd", required=True)
    sub.add_parser("check")
    p = sub.add_parser("totp-setup")
    p.add_argument("--account", default="macserver")
    p = sub.add_parser("totp-verify")
    p.add_argument("code")
    args = parser.parse_args(argv)
    if args.cmd == "check":
        state = load_state()
        score, reasons, fire = check(state=state)
        print(json.dumps({"score": score, "reasons": reasons, "fire": fire}))
        try:
            save_state(state)
        except OSError:
            pass
        return 0 if not fire else 10
    if args.cmd == "totp-setup":
        secret = generate_secret()
        print(f"secret: {secret}")
        print(f"url: {otpauth_url(secret, args.account)}")
        print("Add it to Google Authenticator (manual entry), then run: macserver doomsday setup-totp <secret>")
        return 0
    if args.cmd == "totp-verify":
        try:
            secret = normalize_secret(Path(TOTP_FILE).read_text())
        except OSError:
            print("no TOTP secret enrolled (macserver doomsday setup-totp)")
            return 1
        print("ok" if verify_code(secret, args.code) else "invalid code")
        return 0 if verify_code(secret, args.code) else 1


if __name__ == "__main__":
    raise SystemExit(main())
