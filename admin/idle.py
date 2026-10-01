#!/usr/bin/env python3
"""MacServer idle mode: when nobody is using the server, use less power.

Detection lives in the status collector (admin/collect_status.py): it looks at
the last minute of API traffic, live inbound connections, meaningful outbound
work, an active Claude session and CPU load, and reports
  "idle": {"mode": "auto|on|off", "idle": true|false, "since": ..., "after_s": ...}
in /run/macserver/status.json once nothing has happened for IDLE_AFTER_S.

This script only *applies* that flag, reversibly, and only on this Mac itself:
  - CPU governors switch to IDLE_GOVERNOR (default powersave) while idle, and
    are restored to what they were when activity returns.
  - the screen backlight dims to IDLE_BRIGHTNESS_PCT (default 30) while idle,
    and is restored when activity returns.
  - containers named in IDLE_PAUSE_CONTAINERS (default empty: pause nothing)
    are `docker pause`d while idle and unpaused when activity returns.

It never touches the network, the firewall, Tailscale or the public route:
waking up is just restoring the previous settings. Run from
macserver-idle.timer every 30 s (`idle --check`), or `idle --status` to print
what it would do.

Settings in /etc/macserver/macserver.conf:
  IDLE_MODE=auto             auto = quiet spell -> low power; off = never; on = always
  IDLE_AFTER_S=900           quiet seconds before low power (60..86400)
  IDLE_GOVERNOR=powersave    CPU governor while idle (empty: leave the CPUs alone)
  IDLE_BRIGHTNESS_PCT=30     screen brightness while idle, percent of maximum
                             (empty: leave the screen alone)
  IDLE_PAUSE_CONTAINERS=     comma-separated container names to pause while idle
                             (empty: pause nothing; pausing the database makes
                             apps wait, so only name caches/workers you know)
"""
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import time

CONF = Path(os.environ.get("MACSERVER_CONF", "/etc/macserver/macserver.conf"))
STATUS = Path(os.environ.get("MACSERVER_STATUS", "/run/macserver/status.json"))
STATE = Path(os.environ.get("MACSERVER_IDLE_STATE", "/run/macserver/idle-state.json"))
SYS = Path(os.environ.get("MACSERVER_SYS", "/sys"))
STALE_AFTER_S = 120

NAME_OK = re.compile(r"^[A-Za-z0-9_.-]+$")
# Host/infra chatter that must never keep the Mac awake by itself: Tailscale's
# control plane, the tunnel keepalive, DNS/NTP lookups. Gateway containers all
# carry "gateway" in their name (the macserver-gateway containers); the tunnel
# keepalive and DNS/NTP are matched by service/port below.
IGNORED_WHO = ("tailscaled", "this Mac")
IGNORED_SVC = ("tailscale", "dns", "ntp", "stun")


def read_conf(path=CONF):
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


def idle_settings(conf):
    """(mode, after_s, governor, brightness_pct, pause_names) from macserver.conf."""
    mode = (conf.get("IDLE_MODE", "auto") or "auto").strip().lower()
    if mode not in ("auto", "on", "off"):
        mode = "auto"
    try:
        after = int(conf.get("IDLE_AFTER_S", "900"))
    except ValueError:
        after = 900
    after = min(max(after, 60), 86400)
    governor = (conf.get("IDLE_GOVERNOR", "powersave") or "").strip()
    raw = (conf.get("IDLE_BRIGHTNESS_PCT", "30") or "").strip()
    try:
        brightness = min(max(int(raw), 5), 100) if raw else None
    except ValueError:
        brightness = 30
    pause = [n.strip() for n in (conf.get("IDLE_PAUSE_CONTAINERS", "") or "").split(",")]
    pause = [n for n in pause if n and NAME_OK.fullmatch(n)]
    return mode, after, governor, brightness, pause


def load_status(path=STATUS, now=None):
    try:
        data = json.loads(Path(path).read_text())
    except (OSError, ValueError):
        return None
    now = time.time() if now is None else now
    if now - data.get("generated_at", 0) > STALE_AFTER_S:
        return None  # fail safe: a dead collector must not dim the Mac
    return data


def want_idle(status, mode):
    """Should low power be applied? The collector already honours the mode;
    this only guards a missing/stale flag (stay awake)."""
    if mode == "off":
        return False
    if mode == "on":
        return True
    if not status:
        return False
    return bool((status.get("idle") or {}).get("idle"))


def load_state(path=STATE):
    try:
        data = json.loads(Path(path).read_text())
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def save_state(state, path=STATE):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(state))
    os.chmod(tmp, 0o644)
    os.replace(tmp, path)


def governors(root=SYS):
    """{policy_path: current_governor} for every CPU policy with a governor knob."""
    found = {}
    for gov in sorted((root / "devices/system/cpu/cpufreq").glob("policy*/scaling_governor")):
        try:
            found[str(gov)] = gov.read_text().strip()
        except OSError:
            pass
    return found


def available_governors(policy_path, root=SYS):
    try:
        return Path(policy_path).with_name("scaling_available_governors").read_text().split()
    except OSError:
        return []


def apply_governors(target, root=SYS):
    """Set every policy's governor to target. Returns {policy: previous}."""
    previous = {}
    for policy, current in governors(root).items():
        if current == target:
            continue
        if target not in available_governors(policy, root):
            continue
        previous[policy] = current
        try:
            Path(policy).write_text(target + "\n")
        except OSError:
            previous.pop(policy, None)
    return previous


def restore_governors(saved, root=SYS):
    for policy, gov in (saved or {}).items():
        try:
            Path(policy).write_text(gov + "\n")
        except OSError:
            pass


def backlights(root=SYS):
    """[(brightness_path, max_brightness)] for every backlight device."""
    found = []
    for dev in sorted((root / "class/backlight").glob("*")):
        bright, maximum = dev / "brightness", dev / "max_brightness"
        if bright.exists() and maximum.exists():
            try:
                found.append((str(bright), int(maximum.read_text().strip())))
            except (OSError, ValueError):
                pass
    return found


def apply_backlight(pct, root=SYS):
    """Dim every backlight to pct% of its maximum. Returns {path: previous}."""
    previous = {}
    for path, maximum in backlights(root):
        try:
            current = int(Path(path).read_text().strip())
        except (OSError, ValueError):
            continue
        want = max(min(round(maximum * pct / 100), maximum), 1)
        if current == want:
            continue
        previous[path] = current
        try:
            Path(path).write_text(f"{want}\n")
        except OSError:
            previous.pop(path, None)
    return previous


def restore_backlight(saved):
    for path, value in (saved or {}).items():
        try:
            Path(path).write_text(f"{value}\n")
        except OSError:
            pass


def run_docker(*args, timeout=30):
    try:
        done = subprocess.run(["docker", *args], capture_output=True, text=True,
                              timeout=timeout, check=False)
    except (OSError, subprocess.TimeoutExpired):
        return None
    return done.stdout if done.returncode == 0 else None


def running_containers():
    out = run_docker("ps", "--format", "{{.Names}}")
    return set((out or "").split()) if out is not None else None


def pause_containers(names):
    """Pause the running ones among names. Returns the ones now paused by us."""
    running = running_containers()
    if running is None:
        return []
    paused = []
    for name in names:
        if name not in running:
            continue
        if run_docker("pause", name) is not None:
            paused.append(name)
    return paused


def unpause_containers(names):
    for name in names or []:
        run_docker("unpause", name)


def describe(status, conf, state, target):
    mode, after, governor, brightness, pause = idle_settings(conf)
    idle = (status or {}).get("idle") or {}
    lines = [f"mode {mode} (quiet {after}s before low power)",
             f"server {'idle' if idle.get('idle') else 'active'}"
             + (f" since {time.strftime('%H:%M', time.localtime(idle['since']))}" if idle.get("since") else ""),
             f"low power {'applied' if state.get('applied') else 'not applied'}"
             + (f" -> {'would apply' if target else 'would restore'} now" if bool(state.get("applied")) != target else ""),
             f"CPUs: {governor or 'left alone'}",
             f"screen: {f'{brightness}% while idle' if brightness else 'left alone'}",
             f"pause while idle: {', '.join(pause) if pause else 'nothing'}"]
    saved = state.get("paused") or []
    if saved:
        lines.append(f"paused by idle mode: {', '.join(saved)}")
    return "\n".join(lines)


def check(conf_path=CONF, status_path=STATUS, state_path=STATE, sys_root=SYS):
    """One controller tick: apply low power when idle, restore when active.
    Returns 0. Only writes on transitions, so the journal stays quiet."""
    conf = read_conf(conf_path)
    mode, _, governor, brightness, pause = idle_settings(conf)
    status = load_status(status_path)
    target = want_idle(status, mode)
    state = load_state(state_path)
    applied = bool(state.get("applied"))
    if target == applied:
        return 0
    if target:
        saved = {}
        if governor:
            saved["governors"] = apply_governors(governor, sys_root)
        if brightness:
            saved["backlight"] = apply_backlight(brightness, sys_root)
        saved["paused"] = pause_containers(pause) if pause else []
        saved["applied"] = True
        saved["at"] = int(time.time())
        save_state(saved, state_path)
        print(f"idle: no connections for a while, low-power mode on"
              f"{f' (CPUs -> {governor})' if governor and saved.get('governors') else ''}"
              f"{f', screen {brightness}%' if brightness and saved.get('backlight') else ''}"
              f"{f', paused: {', '.join(saved['paused'])}' if saved.get('paused') else ''}")
    else:
        restore_governors(state.get("governors"), sys_root)
        restore_backlight(state.get("backlight"))
        unpause_containers(state.get("paused"))
        save_state({}, state_path)
        print("idle: activity is back, full power restored")
    return 0


def main(argv=None):
    argv = sys.argv[1:] if argv is None else argv
    if argv[:1] == ["--check"]:
        return check()
    conf, status, state = read_conf(), load_status(), load_state()
    mode, _, _, _, _ = idle_settings(conf)
    print(describe(status, conf, state, want_idle(status, mode)))
    return 0


if __name__ == "__main__":
    sys.exit(main())
