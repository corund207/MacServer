from datetime import datetime, timezone
import hashlib
import importlib.util
import json
from pathlib import Path
from types import SimpleNamespace
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("backup", ROOT / "scripts/backup.py")
backup = importlib.util.module_from_spec(spec)
spec.loader.exec_module(backup)


class FakeRunner:
    def __init__(self, root, uuid="TEST-UUID", removable=True, options="rw,nodev,nosuid,noexec"):
        self.root = Path(root)
        self.uuid = uuid
        self.removable = removable
        self.options = options
        self.commands = []

    def json(self, argv, timeout=30):
        self.commands.append(tuple(argv))
        if argv[0].endswith("findmnt"):
            return {"filesystems": [{"target": str(self.root / "drive"), "source": "/dev/sdz1",
                                      "fstype": "ext4", "uuid": self.uuid, "options": self.options}]}
        return {"blockdevices": [{"path": "/dev/sdz1", "type": "part", "rm": self.removable,
                                   "size": 250 * 1024 ** 3, "uuid": self.uuid,
                                   "mountpoints": [str(self.root / "drive")]}]}

    def to_file(self, argv, path, timeout=3600):
        self.commands.append(tuple(argv))
        Path(path).write_bytes(b"synthetic logical export\n")

    def run(self, argv, timeout=3600):
        self.commands.append(tuple(argv))
        if argv[-1] == "init":
            Path(argv[argv.index("--repo") + 1]).mkdir()
        if "restore" in argv:
            target = Path(argv[argv.index("--target") + 1]) / "staged" / "backup-run-test"
            target.mkdir(parents=True)
            database = target / "database.dump"
            globals_dump = target / "globals.sql"
            database.write_bytes(b"synthetic logical export\n")
            globals_dump.write_bytes(b"synthetic logical export\n")
            manifest = {
                "format": 1,
                "database": "postgres",
                "databaseSha256": hashlib.sha256(database.read_bytes()).hexdigest(),
                "globalsSha256": hashlib.sha256(globals_dump.read_bytes()).hexdigest(),
            }
            (target / "backup-manifest.json").write_text(json.dumps(manifest))


class BackupTests(unittest.TestCase):
    def fixture(self, root):
        root = Path(root)
        paths = {name: root / name for name in ("drive", "source", "staging", "restore", "status")}
        for path in paths.values():
            path.mkdir()
        (paths["drive"] / "repo").mkdir()
        (paths["source"] / "object").write_text("data")
        compose = root / "compose.json"; compose.write_text("{}")
        environment = root / "supabase.env"; environment.write_text("SECRET=fixture")
        password = root / "restic-password"; password.write_text("x" * 40); password.chmod(0o600)
        sentinel = paths["drive"] / backup.SENTINEL
        sentinel.write_text(json.dumps({"format": 1, "filesystem_uuid": "TEST-UUID",
                                        "purpose": "macserver-encrypted-backup"}))
        sentinel.chmod(0o600)
        config = {
            "format": 1, "mountpoint": str(paths["drive"]), "filesystem_uuid": "TEST-UUID",
            "repository_directory": "repo", "minimum_device_bytes": 200 * 1024 ** 3,
            "maximum_device_bytes": 300 * 1024 ** 3, "minimum_free_bytes": 1,
            "compose_file": str(compose), "compose_env_file": str(environment),
            "staging_parent": str(paths["staging"]), "restore_parent": str(paths["restore"]),
            "status_file": str(paths["status"] / "status.json"),
            "sources": [str(paths["source"])], "retention": {"daily": 7, "weekly": 5, "monthly": 12},
        }
        return config, password

    @staticmethod
    def ample_space(_path):
        return SimpleNamespace(free=100 * 1024 ** 3)

    def test_drive_identity_capacity_and_mount_options_are_gates(self):
        with tempfile.TemporaryDirectory() as directory:
            config, _ = self.fixture(directory)
            self.assertEqual(backup.inspect_drive(config, FakeRunner(directory), disk_usage=self.ample_space),
                             Path(directory) / "drive" / "repo")
            for runner, code in ((FakeRunner(directory, uuid="WRONG"), "MOUNT_IDENTITY_MISMATCH"),
                                 (FakeRunner(directory, removable=False), "DEVICE_NOT_REMOVABLE"),
                                 (FakeRunner(directory, options="rw,nodev,nosuid"), "MOUNT_OPTIONS_UNSAFE")):
                with self.subTest(code=code), self.assertRaisesRegex(backup.BackupError, code):
                    backup.inspect_drive(config, runner, disk_usage=self.ample_space)

    def test_backup_exports_database_and_checks_encrypted_repository(self):
        with tempfile.TemporaryDirectory() as directory:
            config, password = self.fixture(directory)
            runner = FakeRunner(directory)
            backup.backup(config, password, runner, datetime(2026, 9, 11, tzinfo=timezone.utc))
            flat = "\n".join(" ".join(command) for command in runner.commands)
            self.assertIn("pg_dump -U postgres -d postgres --format=custom --no-owner --no-acl", flat)
            self.assertIn("pg_dumpall -U postgres --globals-only --no-role-passwords", flat)
            self.assertIn("backup --host macserver --tag automated", flat)
            self.assertIn("--no-cache", flat)
            self.assertIn("check --read-data-subset=5%", flat)
            self.assertNotIn("x" * 40, flat)
            status = json.loads(Path(config["status_file"]).read_text())
            self.assertEqual(status["state"], "degraded")
            self.assertEqual(status["lastSuccess"], "2026-09-11T00:00:00+00:00")

    def test_initialization_is_new_only_and_lock_is_private(self):
        with tempfile.TemporaryDirectory() as directory:
            config, password = self.fixture(directory)
            repository = Path(config["mountpoint"]) / config["repository_directory"]
            repository.rmdir()
            (Path(config["mountpoint"]) / backup.SENTINEL).unlink()
            runner = FakeRunner(directory)
            backup.initialize(config, password, "TEST-UUID", runner)
            self.assertTrue(repository.is_dir())
            self.assertEqual((Path(config["mountpoint"]) / backup.SENTINEL).stat().st_mode & 0o777, 0o600)
            with self.assertRaisesRegex(backup.BackupError, "REPOSITORY_ALREADY_PRESENT"):
                backup.initialize(config, password, "TEST-UUID", runner)
            lock = Path(directory) / "lock"
            with backup.exclusive_lock(lock):
                self.assertEqual(lock.stat().st_mode & 0o777, 0o600)
            lock.chmod(0o644)
            with self.assertRaisesRegex(backup.BackupError, "UNSAFE_LOCK_FILE"):
                with backup.exclusive_lock(lock):
                    pass

    def test_retention_and_restore_require_exact_confirmation(self):
        with tempfile.TemporaryDirectory() as directory:
            config, password = self.fixture(directory)
            runner = FakeRunner(directory)
            with self.assertRaisesRegex(backup.BackupError, "CONFIRMATION_MISMATCH"):
                backup.apply_retention(config, password, "WRONG", runner)
            backup.apply_retention(config, password, "TEST-UUID", runner)
            target = Path(config["restore_parent"]) / "drill-1"
            backup.restore_drill(config, password, target, "TEST-UUID", runner,
                                 datetime(2026, 9, 12, tzinfo=timezone.utc))
            with self.assertRaisesRegex(backup.BackupError, "RESTORE_TARGET_NOT_NEW"):
                backup.restore_drill(config, password, Path(directory) / "outside", "TEST-UUID", runner)
            flat = "\n".join(" ".join(command) for command in runner.commands)
            self.assertIn("forget --host macserver --tag automated", flat)
            self.assertIn("--prune", flat)
            self.assertIn("--network none", flat)
            self.assertIn("pg_restore", flat)
            self.assertEqual(json.loads(Path(config["status_file"]).read_text())["state"], "healthy")

    def test_config_and_password_file_refuse_unsafe_inputs(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            value = json.loads((backup.ROOT / "infra/backup/config.example.json").read_text())
            config_path = root / "config.json"
            config_path.write_text(json.dumps(value)); config_path.chmod(0o600)
            with self.assertRaisesRegex(backup.BackupError, "INVALID_FILESYSTEM_UUID"):
                backup.load_config(config_path)
            value["filesystem_uuid"] = "TEST-UUID"; value["sources"] = ["relative/path"]
            config_path.write_text(json.dumps(value))
            with self.assertRaisesRegex(backup.BackupError, "SOURCE_OUTSIDE_ALLOWLIST"):
                backup.load_config(config_path)
            password = root / "password"; password.write_text("x" * 40); password.chmod(0o644)
            with self.assertRaisesRegex(backup.BackupError, "UNSAFE_PASSWORD_FILE"):
                backup.password_file(password)


if __name__ == "__main__":
    unittest.main()
