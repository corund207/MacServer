#!/usr/bin/env python3
"""Run repository-only release checks and emit a non-secret verification manifest."""
import argparse
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[1]
BUILD = ROOT / "build"
NPM = shutil.which("npm") or "/usr/bin/npm"


def command_matrix(full=False):
    commands = [
        ("foundation", [sys.executable, "scripts/validate.py"], ROOT),
        ("security-posture", [sys.executable, "scripts/security_posture.py"], ROOT),
        ("compose-render", [sys.executable, "scripts/supabase_config.py"], ROOT),
        ("admin-unit", [sys.executable, "scripts/validate_admin_unit.py"], ROOT),
        ("dashboard-units", [sys.executable, "scripts/validate_dashboard_units.py"], ROOT),
        ("backup-units", [sys.executable, "scripts/validate_backup_units.py"], ROOT),
        ("deploy-unit", [sys.executable, "scripts/validate_deploy_unit.py"], ROOT),
        ("python-tests", [sys.executable, "-m", "unittest", "discover", "-s", "tests", "-v"], ROOT),
    ]
    if full:
        commands.extend([
            ("admin-node", [NPM, "test"], ROOT / "apps/admin"),
            ("admin-browser", [NPM, "run", "test:browser"], ROOT / "apps/admin"),
            ("dashboard-node", [NPM, "test"], ROOT / "apps/dashboard"),
            ("dashboard-browser", [NPM, "run", "test:browser"], ROOT / "apps/dashboard"),
        ])
    return commands


def git_value(*arguments):
    result = subprocess.run(["/usr/bin/git", *arguments], cwd=ROOT, stdout=subprocess.PIPE,
                            stderr=subprocess.DEVNULL, timeout=10, check=False, text=True)
    if result.returncode:
        raise RuntimeError("Git metadata unavailable")
    return result.stdout.strip()


def run_check(argv, cwd, timeout=900):
    environment = {name: value for name, value in os.environ.items()
                   if name in {"PATH", "HOME", "TMPDIR", "CI", "FORCE_COLOR", "NO_COLOR"}}
    environment.update({"CI": "1", "NO_COLOR": "1"})
    result = subprocess.run(argv, cwd=cwd, stdin=subprocess.DEVNULL,
                            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                            timeout=timeout, check=False, env=environment)
    return result.returncode == 0


def manifest(full, runner=run_check, now=None):
    checks = []
    for identifier, argv, cwd in command_matrix(full):
        try:
            passed = runner(argv, cwd)
        except (OSError, subprocess.TimeoutExpired):
            passed = False
        checks.append({"id": identifier, "status": "pass" if passed else "fail"})
    commit = git_value("rev-parse", "HEAD")
    upstream = git_value("rev-parse", "@{u}")
    clean = git_value("status", "--porcelain=v1") == ""
    return {
        "format": 1,
        "generatedAt": (now or datetime.now(timezone.utc)).isoformat(),
        "repositoryCommit": commit,
        "branch": git_value("branch", "--show-current"),
        "workingTreeClean": clean,
        "upstreamSynchronized": commit == upstream,
        "scope": "repository-only; no appliance, daemon, tailnet, drive, database, or public endpoint",
        "targetEvidence": False,
        "publicExposureApproved": False,
        "verdict": "NO-GO",
        "checks": checks,
    }


def output_path(value):
    if BUILD.exists():
        if BUILD.is_symlink() or not BUILD.is_dir():
            raise ValueError("unsafe build directory")
    else:
        BUILD.mkdir(mode=0o700)
    candidate = ROOT / value if not Path(value).is_absolute() else Path(value)
    if ".." in candidate.parts:
        raise ValueError("parent traversal refused")
    target = candidate.absolute()
    if target.parent.resolve() != BUILD.resolve() or target.name in {"", ".", ".."} or target.suffix != ".json":
        raise ValueError("output must be a JSON file directly below the repository build directory")
    if target.exists() and target.is_symlink():
        raise ValueError("symlink output refused")
    return target


def atomic_write(target, value):
    payload = (json.dumps(value, indent=2) + "\n").encode()
    fd, temporary = tempfile.mkstemp(prefix=".release-", dir=target.parent)
    try:
        os.fchmod(fd, 0o600); os.write(fd, payload); os.fsync(fd); os.close(fd); fd = -1
        os.replace(temporary, target)
    finally:
        if fd >= 0:
            os.close(fd)
        try: os.unlink(temporary)
        except FileNotFoundError: pass


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--full", action="store_true", help="also run local Node and browser suites")
    parser.add_argument("--require-clean", action="store_true")
    parser.add_argument("--require-upstream-sync", action="store_true")
    parser.add_argument("--output", default="build/release-verification.json")
    args = parser.parse_args()
    try:
        value = manifest(args.full)
        atomic_write(output_path(args.output), value)
    except (OSError, ValueError, RuntimeError) as error:
        print(f"Release verification refused: {error}", file=sys.stderr); return 2
    failed = [check["id"] for check in value["checks"] if check["status"] != "pass"]
    if (failed or (args.require_clean and not value["workingTreeClean"]) or
            (args.require_upstream_sync and not value["upstreamSynchronized"])):
        print("Release verification failed; inspect the private build manifest.", file=sys.stderr); return 1
    print("PASS: repository checks completed; target evidence remains false and verdict remains NO-GO")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
