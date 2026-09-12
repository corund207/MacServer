#!/usr/bin/env python3
"""Parse inert dashboard units in a disposable root; never start them."""
from pathlib import Path
import shutil
import subprocess
import tempfile

ROOT = Path(__file__).resolve().parents[1]


def main():
    if not shutil.which("systemd-analyze"):
        raise SystemExit("systemd-analyze required; no validation pass")
    with tempfile.TemporaryDirectory(prefix="macserver-dashboard-units-") as temporary:
        root = Path(temporary)
        system = root / "etc/systemd/system"
        user = root / "etc/systemd/user"
        system.mkdir(parents=True); user.mkdir(parents=True)
        fixtures = {
            "opt/macserver-runtime/node-v26.8.1-linux-x64/bin/node": "#!/bin/sh\nexit 1\n",
            "usr/bin/python3": "#!/bin/sh\nexit 1\n",
            "usr/bin/chromium": "#!/bin/sh\nexit 1\n",
        }
        for name, content in fixtures.items():
            path = root / name; path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(content); path.chmod(0o755)
        for name in ("sysinit", "basic", "shutdown", "network", "network-online", "multi-user", "timers", "graphical-session"):
            for directory in (system, user):
                (directory / f"{name}.target").write_text("[Unit]\nDescription=Parser fixture only\n")
        for name in ("docker", "tailscaled"):
            (system / f"{name}.service").write_text("[Unit]\nDescription=Parser fixture only\n[Service]\nExecStart=/usr/bin/python3\n")
        sources = list((ROOT / "infra/dashboard").glob("*.service")) + list((ROOT / "infra/dashboard").glob("*.timer"))
        system_destinations = []
        for source in sources:
            # Parse user-unit syntax in the disposable system search path. This is
            # syntax validation only; the target graphical session remains a user unit.
            destination = system / source.name
            shutil.copyfile(source, destination)
            system_destinations.append(destination)
        subprocess.run(["systemd-analyze", "verify", "--man=no", "--generators=no",
                        f"--root={root}", *map(str, system_destinations)], check=True)
    print("PASS: dashboard unit parsers with disposable stubs; not runtime qualification")


if __name__ == "__main__":
    main()
