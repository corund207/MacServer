#!/usr/bin/env python3
"""Fail-closed MacServer commissioning, update, status, and rollback tool."""
import argparse
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import platform
import re
import shutil
import stat
import subprocess
import sys
import tempfile

RELEASES = Path("/opt/macserver-releases")
CURRENT = Path("/opt/macserver")
UNIT = Path("/etc/systemd/system/macserver-data.service")
ENV = Path("/etc/macserver/supabase.env")
RUNTIME_ENV = Path("/etc/macserver/runtime.env")
APPROVAL = Path("/etc/macserver/DEPLOYMENT-APPROVED")
BACKUP_STATUS = Path("/var/lib/macserver-backup/status.json")
UPDATE_APPROVAL = Path("/etc/macserver/update-approval.json")
EXPECTED_REMOTE = "https://github.com/corund207/MacServer.git"
REQUIRED_CHECKS = {
    "debian13Amd64", "t2Hardware", "encryptedInternalStorage", "consoleRecovery",
    "tailscaleOnlyAdministration", "keyOnlySsh", "hostFirewallNegativeTest",
    "dockerForwardingNegativeTest", "noPublicManagementListeners", "thermalRebootPowerTest",
}
SHA = re.compile(r"[0-9a-f]{40}")


class Refusal(Exception):
    pass


def run(argv, *, cwd=None, output=True):
    result = subprocess.run(argv, cwd=cwd, stdin=subprocess.DEVNULL,
                            stdout=subprocess.PIPE if output else subprocess.DEVNULL,
                            stderr=subprocess.PIPE, text=True, timeout=1800, check=False)
    if result.returncode:
        raise Refusal(f"command failed safely: {Path(argv[0]).name} {argv[1] if len(argv) > 1 else ''}")
    return result.stdout.strip() if output else ""


def private_file(path, owner=None):
    path = Path(path)
    info = path.stat()
    expected_owner = os.geteuid() if owner is None else owner
    if (path.is_symlink() or not stat.S_ISREG(info.st_mode) or info.st_nlink != 1
            or stat.S_IMODE(info.st_mode) != 0o600 or info.st_uid != expected_owner):
        raise Refusal(f"unsafe private file: {path}")
    return path


def load_json(path, owner=None):
    try:
        return json.loads(private_file(path, owner).read_text())
    except (OSError, ValueError, json.JSONDecodeError) as error:
        raise Refusal(f"invalid private JSON: {path}") from error


def load_status(path):
    path = Path(path); info = path.stat()
    if (path.is_symlink() or not stat.S_ISREG(info.st_mode) or info.st_nlink != 1
            or info.st_uid != 0 or stat.S_IMODE(info.st_mode) not in (0o600, 0o640)):
        raise Refusal(f"unsafe status file: {path}")
    try:
        return json.loads(path.read_text())
    except (OSError, json.JSONDecodeError) as error:
        raise Refusal(f"invalid status JSON: {path}") from error


def git_command(source, *arguments):
    return ["git", "-c", f"safe.directory={source}", *arguments]


def source_revision(source, expected):
    source = Path(source).resolve()
    source_info = source.stat()
    sudo_uid = int(os.environ.get("SUDO_UID", os.geteuid()))
    git_dir = source / ".git"
    if (not source.is_dir() or source.is_symlink() or not git_dir.is_dir() or git_dir.is_symlink()
            or source_info.st_uid not in (0, sudo_uid)
            or source_info.st_mode & 0o022):
        raise Refusal("source checkout ownership or permissions are unsafe")
    if not SHA.fullmatch(expected):
        raise Refusal("confirmation must be the full 40-character commit")
    if run(git_command(source, "status", "--porcelain=v1"), cwd=source):
        raise Refusal("source checkout is not clean")
    head = run(git_command(source, "rev-parse", "HEAD"), cwd=source)
    upstream = run(git_command(source, "rev-parse", "@{u}"), cwd=source)
    remote = run(git_command(source, "remote", "get-url", "origin"), cwd=source)
    if head != expected or upstream != expected:
        raise Refusal("confirmed commit is not the clean upstream-synchronized HEAD")
    if remote != EXPECTED_REMOTE:
        raise Refusal("unexpected GitHub origin")
    return source, head


def validate_qualification(value, commit, now=None):
    now = now or datetime.now(timezone.utc)
    try:
        checked = datetime.fromisoformat(value["checkedAt"].replace("Z", "+00:00"))
    except (KeyError, TypeError, ValueError) as error:
        raise Refusal("qualification timestamp invalid") from error
    age = now.timestamp() - checked.timestamp()
    checks = value.get("checks", {})
    if (value.get("format") != 1 or value.get("approvedCommit") != commit
            or not 0 <= age <= 7 * 86400 or set(checks) != REQUIRED_CHECKS
            or not all(checks.values())):
        raise Refusal("target qualification is incomplete, stale, or for another commit")
    return value


def qualification(path, commit):
    return validate_qualification(load_json(path, 0), commit)


def validate_backup(value, now=None):
    now = (now or datetime.now(timezone.utc)).timestamp()
    try:
        backup_age = now - datetime.fromisoformat(value["lastSuccess"]).timestamp()
        drill_age = now - datetime.fromisoformat(value["lastRestoreDrill"]).timestamp()
    except (KeyError, TypeError, ValueError) as error:
        raise Refusal("backup evidence is incomplete") from error
    if value.get("state") != "healthy" or not 0 <= backup_age <= 86400 or not 0 <= drill_age <= 30 * 86400:
        raise Refusal("update requires a healthy backup under 24h and restore drill under 30d")


def backup_ready(path=BACKUP_STATUS):
    validate_backup(load_status(path))


def validate_update_approval(value, current, target, now=None):
    now = now or datetime.now(timezone.utc)
    try:
        checked = datetime.fromisoformat(value["checkedAt"].replace("Z", "+00:00"))
    except (KeyError, TypeError, ValueError) as error:
        raise Refusal("update approval timestamp invalid") from error
    age = now.timestamp() - checked.timestamp()
    if (value.get("format") != 1 or value.get("currentCommit") != current
            or value.get("targetCommit") != target or value.get("writersDrained") is not True
            or value.get("dataCompatibilityReviewed") is not True or not 0 <= age <= 3600):
        raise Refusal("update approval is incomplete, stale, or for different releases")


def tracked_files(source):
    raw = subprocess.run(git_command(source, "ls-files", "-z"), cwd=source, stdout=subprocess.PIPE,
                         stderr=subprocess.PIPE, timeout=30, check=True).stdout
    paths = []
    for item in raw.split(b"\0"):
        if not item:
            continue
        relative = Path(os.fsdecode(item))
        candidate = source / relative
        if (relative.is_absolute() or ".." in relative.parts or candidate.is_symlink()
                or not candidate.is_file()):
            raise Refusal("tracked source contains an unsafe path")
        paths.append(relative)
    return paths


def install_release(source, commit, releases=RELEASES):
    releases.mkdir(mode=0o755, parents=True, exist_ok=True)
    target = releases / commit
    if target.exists():
        metadata = target / ".macserver-release.json"
        if (not target.is_dir() or target.is_symlink() or metadata.is_symlink() or not metadata.is_file()
                or json.loads(metadata.read_text()).get("commit") != commit):
            raise Refusal("existing release path is unsafe")
        return target
    temporary = Path(tempfile.mkdtemp(prefix=".macserver-", dir=releases))
    try:
        for relative in tracked_files(source):
            destination = temporary / relative
            destination.parent.mkdir(mode=0o755, parents=True, exist_ok=True)
            shutil.copyfile(source / relative, destination)
            executable = (source / relative).stat().st_mode & 0o111
            destination.chmod(0o755 if executable else 0o644)
        (temporary / ".macserver-release.json").write_text(json.dumps(
            {"format": 1, "commit": commit, "source": EXPECTED_REMOTE}, separators=(",", ":")) + "\n")
        (temporary / ".macserver-release.json").chmod(0o644)
        temporary.chmod(0o755)
        temporary.rename(target)
    except Exception:
        shutil.rmtree(temporary, ignore_errors=True)
        raise
    return target


def runtime_profiles():
    lines = [line.strip() for line in RUNTIME_ENV.read_text().splitlines()
             if line.strip() and not line.lstrip().startswith("#")]
    if len(lines) != 1:
        raise Refusal("runtime profile file is invalid")
    value = lines[0]
    if not re.fullmatch(r"COMPOSE_PROFILES=(core)(,(functions|storage|realtime|management))*", value):
        raise Refusal("runtime profile file is invalid")
    return value.split("=", 1)[1].split(",")


def compose(release):
    command = ["/usr/bin/docker", "compose", "--env-file", str(ENV),
               "-f", str(release / "infra/supabase/compose.json")]
    for profile in runtime_profiles():
        command.extend(("--profile", profile))
    return command


def target_preflight(release, commit, evidence):
    if os.geteuid() != 0:
        raise Refusal("commissioning and updates require sudo")
    info = platform.freedesktop_os_release()
    if info.get("ID") != "debian" or info.get("VERSION_ID") != "13" or platform.machine() != "x86_64":
        raise Refusal("target must be Debian 13 amd64")
    qualification(evidence, commit)
    private_file(ENV, 0); private_file(RUNTIME_ENV, 0)
    runtime_profiles()
    lock = json.loads((release / "infra/supabase/release.lock.json").read_text())
    for package, metadata in lock["docker_packages"].items():
        installed = run(["/usr/bin/dpkg-query", "-W", "-f=${Version}", package])
        if installed != metadata["Version"]:
            raise Refusal(f"package version mismatch: {package}")
    if run(["/usr/bin/tailscale", "version"]).splitlines()[0] != "1.102.3":
        raise Refusal("Tailscale must match the reviewed 1.102.3 identity contract")
    run(compose(release) + ["config", "--quiet"])


def switch_current(target, current=CURRENT):
    link = current.parent / ("." + current.name + ".next")
    try:
        link.unlink()
    except FileNotFoundError:
        pass
    link.symlink_to(target)
    os.replace(link, current)


def deploy(source, commit, evidence, update=False, update_approval=UPDATE_APPROVAL):
    source, commit = source_revision(source, commit)
    release = install_release(source, commit)
    target_preflight(release, commit, evidence)
    previous = CURRENT.resolve() if CURRENT.is_symlink() else None
    if CURRENT.exists() and not CURRENT.is_symlink():
        raise Refusal("/opt/macserver must be absent or a managed symlink")
    if update:
        if previous is None:
            raise Refusal("update requires an installed release")
        backup_ready()
        previous_meta = json.loads((previous / ".macserver-release.json").read_text())
        validate_update_approval(load_json(update_approval, 0), previous_meta["commit"], commit)
    else:
        if previous is not None:
            raise Refusal("use update for an installed appliance")
        if run(compose(release) + ["ps", "--all", "--quiet"]):
            raise Refusal("commissioning requires an empty Compose target")
        if run(["/usr/bin/docker", "volume", "ls", "--quiet", "--filter",
                "label=com.docker.compose.project=macserver-data"]):
            raise Refusal("commissioning refuses existing MacServer volumes")
        if "storage" in runtime_profiles():
            storage = Path("/srv/macserver/storage")
            if not storage.is_dir() or any(storage.iterdir()):
                raise Refusal("commissioning requires an empty Storage directory")
    shutil.copyfile(release / "infra/deploy/macserver-data.service", UNIT)
    UNIT.chmod(0o644)
    run(["/usr/bin/systemctl", "daemon-reload"])
    run(compose(release) + ["pull"], output=False)
    shutil.copyfile(evidence, APPROVAL); APPROVAL.chmod(0o600)
    try:
        switch_current(release)
        run(["/usr/bin/systemctl", "enable", "--now", "macserver-data.service"])
        if update:
            run(["/usr/bin/systemctl", "restart", "macserver-data.service"])
    except Exception:
        if previous:
            switch_current(previous)
            shutil.copyfile(previous / "infra/deploy/macserver-data.service", UNIT)
            UNIT.chmod(0o644)
            run(["/usr/bin/systemctl", "daemon-reload"])
            run(["/usr/bin/systemctl", "restart", "macserver-data.service"])
        else:
            run(["/usr/bin/systemctl", "stop", "macserver-data.service"])
        raise
    print(f"PASS: {'updated' if update else 'commissioned'} private MacServer release {commit}")


def status():
    target = CURRENT.resolve(strict=True)
    meta = json.loads((target / ".macserver-release.json").read_text())
    rows = run(compose(target) + ["ps", "--all", "--format", "json"])
    print(json.dumps({"commit": meta["commit"], "services": [json.loads(x) for x in rows.splitlines() if x]}, indent=2))


def rollback(target_commit, confirmed_current):
    if os.geteuid() != 0 or not SHA.fullmatch(target_commit) or not SHA.fullmatch(confirmed_current):
        raise Refusal("rollback requires sudo and two full commit IDs")
    current = CURRENT.resolve(strict=True)
    meta = json.loads((current / ".macserver-release.json").read_text())
    target = RELEASES / target_commit
    if meta.get("commit") != confirmed_current or not (target / ".macserver-release.json").is_file():
        raise Refusal("rollback confirmation or installed target mismatch")
    backup_ready()
    try:
        switch_current(target)
        run(["/usr/bin/systemctl", "restart", "macserver-data.service"])
    except Exception:
        switch_current(current)
        run(["/usr/bin/systemctl", "restart", "macserver-data.service"])
        raise
    print(f"PASS: rolled back release pointer to {target_commit}; verify data compatibility")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    for name in ("commission", "update"):
        cmd = sub.add_parser(name)
        cmd.add_argument("--source", required=True)
        cmd.add_argument("--confirm-commit", required=True)
        cmd.add_argument("--qualification", default="/etc/macserver/qualification.json")
        if name == "update":
            cmd.add_argument("--update-approval", default=str(UPDATE_APPROVAL))
    sub.add_parser("status")
    back = sub.add_parser("rollback")
    back.add_argument("--to", required=True)
    back.add_argument("--confirm-current", required=True)
    args = parser.parse_args()
    try:
        if args.command == "status": status()
        elif args.command == "rollback": rollback(args.to, args.confirm_current)
        else: deploy(args.source, args.confirm_commit, args.qualification, args.command == "update",
                     getattr(args, "update_approval", UPDATE_APPROVAL))
        return 0
    except (OSError, ValueError, Refusal, subprocess.SubprocessError, json.JSONDecodeError) as error:
        print(f"Refused: {error}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
