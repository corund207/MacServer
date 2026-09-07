#!/usr/bin/env python3
"""Offline preflight and immutable bundle staging. Never applies to host."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import platform
import shutil
import stat
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[1]


def assess(os_info, machine, ram_bytes, free_bytes):
    checks = {
        "debian_13": os_info.get("ID") == "debian" and os_info.get("VERSION_ID") == "13",
        "amd64": machine == "x86_64",
        "ram_at_least_4_gib": ram_bytes >= 4 * 1024**3,
        "free_at_least_40_gib": free_bytes >= 40 * 1024**3,
    }
    return {"checks": checks, "eligible": all(checks.values()),
            "hardware_qualification": "MANUAL: T2 boot, SSD, network, cooling, recovery"}


def preflight(state_parent):
    info = platform.freedesktop_os_release()
    ram = os.sysconf("SC_PAGE_SIZE") * os.sysconf("SC_PHYS_PAGES")
    target = Path(state_parent)
    if not target.is_dir():
        raise ValueError("state parent must be an existing directory on the intended live-state filesystem")
    result = assess(info, platform.machine(), ram, shutil.disk_usage(target).free)
    result["disk_check"] = "Selected state-parent filesystem only; manually verify internal SSD identity"
    print(json.dumps(result, indent=2))
    return 0 if result["eligible"] else 1


def stage(output):
    # Require an existing parent; do not resolve away a destination symlink.
    output = Path(os.path.abspath(output))
    if output.is_symlink():
        raise ValueError("destination must not be a symlink")
    if not output.parent.is_dir() or output.parent.resolve() != output.parent:
        raise ValueError("parent must exist and have no symlink components")
    source = ROOT / "infra"
    entries = [source, *sorted(source.rglob("*"))]
    if any(p.is_symlink() or not (p.is_file() or p.is_dir()) for p in entries):
        raise ValueError("infrastructure source contains a symlink or special file")
    files = {str(p.relative_to(ROOT)): p.read_bytes() for p in entries if p.is_file()}
    manifest = {name: hashlib.sha256(data).hexdigest() for name, data in files.items()}
    files["manifest.json"] = (json.dumps(manifest, indent=2) + "\n").encode()
    if output.exists():
        if not output.is_dir():
            raise ValueError("destination must be a directory")
        if stat.S_IMODE(output.stat().st_mode) != 0o700:
            raise ValueError("existing bundle directory must have mode 0700")
        entries = list(output.rglob("*"))
        if any(p.is_symlink() or not (p.is_file() or p.is_dir()) for p in entries):
            raise ValueError("existing bundle contains a symlink or special file")
        if any(stat.S_IMODE(p.stat().st_mode) != 0o600 for p in entries if p.is_file()):
            raise ValueError("existing bundle files must have mode 0600")
        actual = {str(p.relative_to(output)): p.read_bytes() for p in entries if p.is_file()}
        if actual != files:
            raise ValueError("bundle differs; choose a fresh destination and review changes")
        print("Bundle unchanged; no host configuration applied.")
        return
    temporary = Path(tempfile.mkdtemp(prefix=".macserver-", dir=output.parent))
    try:
        for name, data in files.items():
            target = temporary / name
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(data)
            target.chmod(0o600)
        # Never replace an existing bundle, even an empty one.
        output.mkdir(mode=0o700)
        for entry in temporary.iterdir():
            entry.rename(output / entry.name)
    finally:
        shutil.rmtree(temporary)
    print("Bundle staged; no host configuration applied.")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    pre = sub.add_parser("preflight")
    pre.add_argument("--state-parent", default="/srv",
                     help="existing directory on intended live-state filesystem (default: /srv)")
    staging = sub.add_parser("stage")
    staging.add_argument("--output", required=True)
    args = parser.parse_args()
    try:
        if args.command == "preflight":
            return preflight(args.state_parent)
        stage(args.output)
        return 0
    except (OSError, ValueError) as exc:
        print(f"Refused: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
