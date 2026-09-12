#!/usr/bin/env python3
"""Parse the inert data-service unit in a disposable root; never start it."""
from pathlib import Path
import shutil
import subprocess
import tempfile

ROOT = Path(__file__).resolve().parents[1]


def main():
    if not shutil.which("systemd-analyze"):
        raise SystemExit("systemd-analyze required; no validation pass")
    with tempfile.TemporaryDirectory(prefix="macserver-deploy-unit-") as temporary:
        root = Path(temporary)
        units = root / "etc/systemd/system"
        units.mkdir(parents=True)
        docker = root / "usr/bin/docker"
        docker.parent.mkdir(parents=True)
        docker.write_text("#!/bin/sh\nexit 1\n"); docker.chmod(0o755)
        for name in ("sysinit", "basic", "shutdown", "multi-user", "network-online"):
            (units / f"{name}.target").write_text("[Unit]\nDescription=Parser fixture only\n")
        for name in ("docker", "tailscaled"):
            (units / f"{name}.service").write_text("[Unit]\nDescription=Parser fixture only\n[Service]\nExecStart=/usr/bin/docker\n")
        target = units / "macserver-data.service"
        shutil.copyfile(ROOT / "infra/deploy/macserver-data.service", target)
        subprocess.run(["systemd-analyze", "verify", "--man=no", "--generators=no",
                        f"--root={root}", str(target)], check=True)
    print("PASS: deployment unit parser with disposable stubs; not runtime qualification")


if __name__ == "__main__":
    main()
