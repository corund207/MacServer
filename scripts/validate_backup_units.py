#!/usr/bin/env python3
"""Parse inert backup units in a disposable root; never start them."""
from pathlib import Path
import shutil
import subprocess
import tempfile

ROOT = Path(__file__).resolve().parents[1]


def main():
    if not shutil.which("systemd-analyze"):
        raise SystemExit("systemd-analyze required; no validation pass")
    with tempfile.TemporaryDirectory(prefix="macserver-backup-units-") as temporary:
        root = Path(temporary)
        units = root / "etc/systemd/system"
        units.mkdir(parents=True)
        python = root / "usr/bin/python3"
        python.parent.mkdir(parents=True); python.write_text("#!/bin/sh\nexit 1\n"); python.chmod(0o755)
        for name in ("sysinit", "basic", "shutdown", "multi-user", "timers"):
            (units / f"{name}.target").write_text("[Unit]\nDescription=Parser fixture only\n")
        (units / "docker.service").write_text("[Unit]\nDescription=Parser fixture only\n[Service]\nExecStart=/usr/bin/python3\n")
        destinations = []
        for source in sorted((ROOT / "infra/backup").glob("macserver-*.*")):
            destination = units / source.name
            shutil.copyfile(source, destination); destinations.append(destination)
        subprocess.run(["systemd-analyze", "verify", "--man=no", "--generators=no",
                        f"--root={root}", *map(str, destinations)], check=True)
    print("PASS: backup unit parsers with disposable stubs; not runtime qualification")


if __name__ == "__main__":
    main()
