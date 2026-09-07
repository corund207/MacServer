#!/usr/bin/env python3
"""Parse the inert admin unit in a disposable systemd root; never start anything.

Executable and dependency stubs satisfy the parser's existence checks only.
This does not test sandbox enforcement or the real Node/Tailscale installation.
"""
from pathlib import Path
import shutil
import subprocess
import tempfile

ROOT = Path(__file__).resolve().parents[1]


def main():
    if not shutil.which("systemd-analyze"):
        raise SystemExit("systemd-analyze required; no validation pass")
    with tempfile.TemporaryDirectory(prefix="macserver-unit-") as temporary:
        root = Path(temporary)
        units = root / "etc/systemd/system"
        units.mkdir(parents=True)
        binary = root / "opt/macserver-runtime/node-v26.8.1-linux-x64/bin/node"
        binary.parent.mkdir(parents=True)
        binary.write_text("#!/bin/sh\nexit 1\n")
        binary.chmod(0o755)
        for name in ("sysinit", "basic", "shutdown", "network-online", "multi-user"):
            (units / f"{name}.target").write_text("[Unit]\nDescription=Parser fixture only\n")
        (units / "tailscaled.service").write_text(
            "[Unit]\nDescription=Parser fixture only\n[Service]\n"
            "ExecStart=/opt/macserver-runtime/node-v26.8.1-linux-x64/bin/node\n")
        destination = units / "macserver-admin.service"
        shutil.copyfile(ROOT / "infra/admin/macserver-admin.service", destination)
        subprocess.run(["systemd-analyze", "verify", "--man=no", "--generators=no",
                        f"--root={root}", str(destination)], check=True)
    print("PASS: admin unit parser with disposable dependency stubs; not runtime qualification")


if __name__ == "__main__":
    main()
