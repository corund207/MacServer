#!/usr/bin/env python3
"""Encrypted appliance backup and isolated restore tooling with fail-closed gates."""
import argparse
from contextlib import contextmanager
from datetime import datetime, timezone
import fcntl
import hashlib
import json
import os
from pathlib import Path
import secrets
import shutil
import stat
import subprocess
import sys
import tempfile
import time

ROOT = Path(__file__).resolve().parents[1]
LOCK = json.loads((ROOT / "infra/backup/release.lock.json").read_text())
RESTIC = LOCK["restic"]["installed_path"]
RESTORE_IMAGE = LOCK["postgres_restore_image"]
SENTINEL = ".macserver-backup.json"
MAX_JSON = 256 * 1024
ALLOWED_SOURCE_PREFIXES = ("/srv/macserver", "/etc/macserver", "/opt/macserver/infra")
ALLOWED_FILESYSTEMS = {"ext4", "btrfs", "xfs"}


class BackupError(Exception):
    """Expected refusal with a safe operator-facing code."""


class Runner:
    def run(self, argv, timeout=3600):
        result = subprocess.run(argv, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                                stderr=subprocess.DEVNULL, timeout=timeout, check=False,
                                env={"PATH": "/usr/sbin:/usr/bin:/sbin:/bin", "LANG": "C.UTF-8"})
        if result.returncode:
            raise BackupError("COMMAND_FAILED")

    def json(self, argv, timeout=30):
        result = subprocess.run(argv, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                                stderr=subprocess.DEVNULL, timeout=timeout, check=False,
                                env={"PATH": "/usr/sbin:/usr/bin:/sbin:/bin", "LANG": "C.UTF-8"})
        if result.returncode or len(result.stdout) > MAX_JSON:
            raise BackupError("COMMAND_FAILED")
        try:
            return json.loads(result.stdout)
        except json.JSONDecodeError as error:
            raise BackupError("INVALID_COMMAND_OUTPUT") from error

    def to_file(self, argv, path, timeout=3600):
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
        with os.fdopen(fd, "wb") as output:
            result = subprocess.run(argv, stdin=subprocess.DEVNULL, stdout=output,
                                    stderr=subprocess.DEVNULL, timeout=timeout, check=False,
                                    env={"PATH": "/usr/sbin:/usr/bin:/sbin:/bin", "LANG": "C.UTF-8"})
            output.flush(); os.fsync(output.fileno())
        if result.returncode or Path(path).stat().st_size == 0:
            raise BackupError("DATABASE_EXPORT_FAILED")


def safe_json(path, require_private=True):
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    try:
        metadata = os.fstat(fd)
        if not stat.S_ISREG(metadata.st_mode) or metadata.st_nlink != 1 or metadata.st_size > MAX_JSON:
            raise BackupError("UNSAFE_JSON_FILE")
        if require_private and metadata.st_mode & 0o077:
            raise BackupError("UNSAFE_JSON_PERMISSIONS")
        raw = os.read(fd, MAX_JSON + 1)
        value = json.loads(raw)
        if not isinstance(value, dict):
            raise BackupError("INVALID_JSON")
        return value
    except json.JSONDecodeError as error:
        raise BackupError("INVALID_JSON") from error
    finally:
        os.close(fd)


def real_path(path, kind="any"):
    value = Path(path)
    if not value.is_absolute() or value == Path("/") or ".." in value.parts:
        raise BackupError("UNSAFE_PATH")
    current = Path("/")
    for part in value.parts[1:]:
        current /= part
        metadata = current.lstat()
        if stat.S_ISLNK(metadata.st_mode):
            raise BackupError("SYMLINK_PATH")
    if kind == "directory" and not value.is_dir():
        raise BackupError("DIRECTORY_REQUIRED")
    if kind == "file" and not value.is_file():
        raise BackupError("FILE_REQUIRED")
    return value


def load_config(path):
    config = safe_json(real_path(path, "file"))
    required = {"format", "mountpoint", "filesystem_uuid", "repository_directory",
                "minimum_device_bytes", "maximum_device_bytes", "minimum_free_bytes",
                "compose_file", "compose_env_file", "staging_parent", "restore_parent", "status_file",
                "sources", "retention"}
    if set(config) != required or config["format"] != 1:
        raise BackupError("INVALID_CONFIG_FIELDS")
    if (not isinstance(config["filesystem_uuid"], str) or
            not 4 <= len(config["filesystem_uuid"]) <= 80 or
            config["filesystem_uuid"].startswith("REPLACE_")):
        raise BackupError("INVALID_FILESYSTEM_UUID")
    if (not isinstance(config["repository_directory"], str) or
            not 1 <= len(config["repository_directory"]) <= 64 or
            not all(character.isalnum() or character in "-_" for character in config["repository_directory"])):
        raise BackupError("INVALID_REPOSITORY_NAME")
    for name in ("minimum_device_bytes", "maximum_device_bytes", "minimum_free_bytes"):
        if not isinstance(config[name], int) or config[name] <= 0:
            raise BackupError("INVALID_SIZE_GATE")
    if config["minimum_device_bytes"] >= config["maximum_device_bytes"]:
        raise BackupError("INVALID_SIZE_GATE")
    if not isinstance(config["sources"], list) or not 1 <= len(config["sources"]) <= 16 or len(set(config["sources"])) != len(config["sources"]):
        raise BackupError("INVALID_SOURCES")
    for source in config["sources"]:
        if (not isinstance(source, str) or not Path(source).is_absolute() or
                not any(os.path.commonpath((source, prefix)) == prefix for prefix in ALLOWED_SOURCE_PREFIXES)):
            raise BackupError("SOURCE_OUTSIDE_ALLOWLIST")
    retention = config["retention"]
    if set(retention) != {"daily", "weekly", "monthly"} or any(not isinstance(retention[name], int) or not 1 <= retention[name] <= 365 for name in retention):
        raise BackupError("INVALID_RETENTION")
    for name in ("mountpoint", "compose_file", "compose_env_file", "staging_parent", "restore_parent", "status_file"):
        if not isinstance(config[name], str) or not Path(config[name]).is_absolute():
            raise BackupError("UNSAFE_PATH")
    return config


def flatten_devices(devices):
    for device in devices:
        yield device
        yield from flatten_devices(device.get("children", []))


def inspect_drive(config, runner, require_sentinel=True, disk_usage=shutil.disk_usage):
    mountpoint = real_path(config["mountpoint"], "directory")
    mounted = runner.json(["/usr/bin/findmnt", "--json", "--target", str(mountpoint),
                           "--output", "TARGET,SOURCE,FSTYPE,UUID,OPTIONS"])
    rows = mounted.get("filesystems", [])
    if len(rows) != 1:
        raise BackupError("MOUNT_NOT_UNIQUE")
    row = rows[0]
    if row.get("target") != str(mountpoint) or row.get("uuid") != config["filesystem_uuid"]:
        raise BackupError("MOUNT_IDENTITY_MISMATCH")
    if row.get("fstype") not in ALLOWED_FILESYSTEMS:
        raise BackupError("FILESYSTEM_NOT_ALLOWED")
    options = set(str(row.get("options", "")).split(","))
    if not {"rw", "nodev", "nosuid", "noexec"}.issubset(options):
        raise BackupError("MOUNT_OPTIONS_UNSAFE")
    devices = runner.json(["/usr/bin/lsblk", "--json", "--bytes", "--output", "PATH,TYPE,RM,SIZE,UUID,MOUNTPOINTS"])
    matches = [item for item in flatten_devices(devices.get("blockdevices", [])) if item.get("uuid") == config["filesystem_uuid"]]
    if len(matches) != 1 or matches[0].get("rm") not in (True, 1):
        raise BackupError("DEVICE_NOT_REMOVABLE")
    size = matches[0].get("size")
    if not isinstance(size, int) or not config["minimum_device_bytes"] <= size <= config["maximum_device_bytes"]:
        raise BackupError("DEVICE_SIZE_MISMATCH")
    if disk_usage(mountpoint).free < config["minimum_free_bytes"]:
        raise BackupError("INSUFFICIENT_BACKUP_SPACE")
    sentinel = mountpoint / SENTINEL
    if require_sentinel:
        value = safe_json(sentinel)
        if value != {"format": 1, "filesystem_uuid": config["filesystem_uuid"], "purpose": "macserver-encrypted-backup"}:
            raise BackupError("SENTINEL_MISMATCH")
    return mountpoint / config["repository_directory"]


def password_file(explicit=None):
    value = explicit
    if not value and os.environ.get("CREDENTIALS_DIRECTORY"):
        value = str(Path(os.environ["CREDENTIALS_DIRECTORY"]) / "restic-password")
    if not value:
        raise BackupError("PASSWORD_FILE_REQUIRED")
    path = real_path(value, "file")
    metadata = path.stat()
    if metadata.st_mode & 0o077 or metadata.st_nlink != 1 or not 32 <= metadata.st_size <= 1024:
        raise BackupError("UNSAFE_PASSWORD_FILE")
    return path


def restic(repository, password, *arguments):
    return [RESTIC, "--no-cache", "--repo", str(repository), "--password-file", str(password), *arguments]


def write_json_atomic(path, value, mode=0o640):
    target = Path(path)
    parent = real_path(target.parent, "directory")
    payload = (json.dumps(value, separators=(",", ":")) + "\n").encode()
    if len(payload) > MAX_JSON:
        raise BackupError("STATUS_TOO_LARGE")
    fd, temporary = tempfile.mkstemp(prefix=".macserver-", dir=parent)
    try:
        os.fchmod(fd, mode); os.write(fd, payload); os.fsync(fd); os.close(fd); fd = -1
        os.replace(temporary, target)
        directory_fd = os.open(parent, os.O_RDONLY | os.O_DIRECTORY)
        try: os.fsync(directory_fd)
        finally: os.close(directory_fd)
    finally:
        if fd >= 0: os.close(fd)
        try: os.unlink(temporary)
        except FileNotFoundError: pass


def write_status(config, state, detail, now=None, **extra):
    now = now or datetime.now(timezone.utc)
    previous = {}
    try: previous = safe_json(config["status_file"], require_private=False)
    except (OSError, BackupError): pass
    value = {"format": 1, "state": state, "detail": detail[:160],
             "updatedAt": now.isoformat(), "lastSuccess": previous.get("lastSuccess"),
             "lastRestoreDrill": previous.get("lastRestoreDrill"), **extra}
    write_json_atomic(config["status_file"], value)


@contextmanager
def exclusive_lock(path):
    fd = os.open(path, os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW, 0o600)
    try:
        metadata = os.fstat(fd)
        if (not stat.S_ISREG(metadata.st_mode) or metadata.st_nlink != 1 or
                metadata.st_mode & 0o077):
            raise BackupError("UNSAFE_LOCK_FILE")
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        yield
    finally:
        os.close(fd)


def docker_prefix(config):
    return ["/usr/bin/docker", "compose", "--env-file", config["compose_env_file"], "-f", config["compose_file"]]


def initialize(config, password, confirmation, runner):
    if confirmation != config["filesystem_uuid"]:
        raise BackupError("CONFIRMATION_MISMATCH")
    repository = inspect_drive(config, runner, require_sentinel=False)
    sentinel = Path(config["mountpoint"]) / SENTINEL
    if repository.exists() or sentinel.exists():
        raise BackupError("REPOSITORY_ALREADY_PRESENT")
    runner.run(restic(repository, password, "init"))
    fd = os.open(sentinel, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    with os.fdopen(fd, "w") as output:
        json.dump({"format": 1, "filesystem_uuid": config["filesystem_uuid"], "purpose": "macserver-encrypted-backup"}, output)
        output.write("\n"); output.flush(); os.fsync(output.fileno())
    directory_fd = os.open(config["mountpoint"], os.O_RDONLY | os.O_DIRECTORY)
    try: os.fsync(directory_fd)
    finally: os.close(directory_fd)


def digest(path):
    value = hashlib.sha256()
    with open(path, "rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""): value.update(chunk)
    return value.hexdigest()


def backup(config, password, runner, now=None):
    now = now or datetime.now(timezone.utc)
    repository = inspect_drive(config, runner)
    if not repository.is_dir():
        raise BackupError("REPOSITORY_MISSING")
    sources = [real_path(path) for path in config["sources"]]
    real_path(config["compose_file"], "file"); real_path(config["compose_env_file"], "file")
    staging_parent = real_path(config["staging_parent"], "directory")
    with tempfile.TemporaryDirectory(prefix="backup-run-", dir=staging_parent) as temporary:
        stage = Path(temporary); database = stage / "database.dump"; globals_dump = stage / "globals.sql"
        prefix = docker_prefix(config) + ["exec", "-T", "db"]
        runner.to_file(prefix + ["pg_dump", "-U", "postgres", "-d", "postgres", "--format=custom", "--no-owner", "--no-acl"], database)
        runner.to_file(prefix + ["pg_dumpall", "-U", "postgres", "--globals-only", "--no-role-passwords"], globals_dump)
        manifest = {"format": 1, "createdAt": now.isoformat(), "database": "postgres",
                    "databaseSha256": digest(database), "globalsSha256": digest(globals_dump),
                    "sources": list(config["sources"])}
        manifest_path = stage / "backup-manifest.json"
        fd = os.open(manifest_path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
        with os.fdopen(fd, "w") as output:
            json.dump(manifest, output); output.write("\n"); output.flush(); os.fsync(output.fileno())
        runner.run(restic(repository, password, "backup", "--host", "macserver", "--tag", "automated", "--exclude-caches", str(stage), *map(str, sources)))
    runner.run(restic(repository, password, "check", "--read-data-subset=5%"))
    drill = None
    try: drill = safe_json(config["status_file"], require_private=False).get("lastRestoreDrill")
    except (OSError, BackupError): pass
    drill_current = False
    if isinstance(drill, str):
        try:
            drill_current = 0 <= now.timestamp() - datetime.fromisoformat(drill).timestamp() <= 30 * 86400
        except ValueError:
            pass
    state = "healthy" if drill_current else "degraded"
    write_status(config, state, "Encrypted snapshot and repository subset verified; isolated restore evidence " + ("current" if state == "healthy" else "missing or older than 30 days"), now, lastSuccess=now.isoformat())


def repository_check(config, password, runner):
    repository = inspect_drive(config, runner)
    runner.run(restic(repository, password, "check", "--read-data-subset=5%"))


def apply_retention(config, password, confirmation, runner):
    if confirmation != config["filesystem_uuid"]:
        raise BackupError("CONFIRMATION_MISMATCH")
    repository = inspect_drive(config, runner); policy = config["retention"]
    runner.run(restic(repository, password, "check", "--read-data-subset=5%"))
    runner.run(restic(repository, password, "forget", "--host", "macserver", "--tag", "automated",
                      "--keep-daily", str(policy["daily"]), "--keep-weekly", str(policy["weekly"]),
                      "--keep-monthly", str(policy["monthly"]), "--prune"), timeout=7200)


def restore_drill(config, password, target, confirmation, runner, now=None):
    now = now or datetime.now(timezone.utc)
    if confirmation != config["filesystem_uuid"]:
        raise BackupError("CONFIRMATION_MISMATCH")
    repository = inspect_drive(config, runner)
    restore_parent = real_path(config["restore_parent"], "directory")
    destination = Path(target)
    if (not destination.is_absolute() or destination.exists() or destination == restore_parent or
            ".." in destination.parts or os.path.commonpath((str(destination), str(restore_parent))) != str(restore_parent)):
        raise BackupError("RESTORE_TARGET_NOT_NEW")
    real_path(destination.parent, "directory"); destination.mkdir(mode=0o700)
    runner.run(restic(repository, password, "restore", "latest", "--host", "macserver", "--tag", "automated", "--target", str(destination), "--verify"), timeout=7200)
    dumps = [path for path in destination.rglob("database.dump") if path.is_file() and not path.is_symlink()]
    globals_dumps = [path for path in destination.rglob("globals.sql") if path.is_file() and not path.is_symlink()]
    manifests = [path for path in destination.rglob("backup-manifest.json") if path.is_file() and not path.is_symlink()]
    if len(dumps) != 1 or len(globals_dumps) != 1 or len(manifests) != 1:
        raise BackupError("RESTORED_MANIFEST_MISMATCH")
    manifest = safe_json(manifests[0], require_private=False)
    if (manifest.get("format") != 1 or manifest.get("database") != "postgres" or
            manifest.get("databaseSha256") != digest(dumps[0]) or
            manifest.get("globalsSha256") != digest(globals_dumps[0])):
        raise BackupError("RESTORED_MANIFEST_MISMATCH")
    name = "macserver-restore-" + secrets.token_hex(6)
    try:
        runner.run(["/usr/bin/docker", "run", "--detach", "--name", name, "--network", "none",
                    "--tmpfs", "/var/lib/postgresql/data:rw,noexec,nosuid,nodev", "--env", "POSTGRES_HOST_AUTH_METHOD=trust", RESTORE_IMAGE])
        ready = False
        for _ in range(30):
            try: runner.run(["/usr/bin/docker", "exec", name, "pg_isready", "-U", "postgres"], timeout=3); ready = True; break
            except BackupError: time.sleep(1)
        if not ready: raise BackupError("RESTORE_DATABASE_NOT_READY")
        runner.run(["/usr/bin/docker", "cp", str(dumps[0]), f"{name}:/tmp/database.dump"])
        runner.run(["/usr/bin/docker", "exec", name, "createdb", "-U", "postgres", "macserver_restore_drill"])
        runner.run(["/usr/bin/docker", "exec", name, "pg_restore", "-U", "postgres", "-d", "macserver_restore_drill", "--no-owner", "--no-acl", "/tmp/database.dump"], timeout=7200)
        runner.run(["/usr/bin/docker", "exec", name, "psql", "-U", "postgres", "-d", "macserver_restore_drill", "-v", "ON_ERROR_STOP=1", "-Atqc", "select count(*) from pg_catalog.pg_class"])
    finally:
        try: runner.run(["/usr/bin/docker", "rm", "--force", name], timeout=30)
        except BackupError: pass
    write_status(config, "healthy", "Encrypted backup restored and database loaded in isolated no-network container", now, lastRestoreDrill=now.isoformat())


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", required=True)
    parser.add_argument("--password-file")
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("preflight")
    init = sub.add_parser("init"); init.add_argument("--confirm-uuid", required=True)
    sub.add_parser("backup"); sub.add_parser("check")
    retention = sub.add_parser("retention"); retention.add_argument("--confirm-uuid", required=True)
    restore = sub.add_parser("restore-drill"); restore.add_argument("--target", required=True); restore.add_argument("--confirm-uuid", required=True)
    args = parser.parse_args(); config = None
    try:
        config = load_config(args.config); runner = Runner(); password = password_file(args.password_file)
        if args.command == "preflight":
            repository = inspect_drive(config, runner)
            print(json.dumps({"eligible": True, "repository": str(repository), "filesystem_uuid": config["filesystem_uuid"]}, indent=2)); return 0
        lock_path = real_path(Path(config["status_file"]).parent, "directory") / "backup.lock"
        with exclusive_lock(lock_path):
            if args.command == "init": initialize(config, password, args.confirm_uuid, runner)
            elif args.command == "backup": backup(config, password, runner)
            elif args.command == "check": repository_check(config, password, runner)
            elif args.command == "retention": apply_retention(config, password, args.confirm_uuid, runner)
            else: restore_drill(config, password, args.target, args.confirm_uuid, runner)
        print(f"{args.command} completed; review private status and logs."); return 0
    except (OSError, ValueError, BackupError, subprocess.TimeoutExpired, BlockingIOError) as error:
        code = str(error) if isinstance(error, BackupError) else "SYSTEM_REFUSAL"
        if config:
            try: write_status(config, "failed", f"Backup operation failed safely: {code}")
            except (OSError, BackupError): pass
        print(f"Backup operation failed safely: {code}", file=sys.stderr); return 2


if __name__ == "__main__":
    sys.exit(main())
