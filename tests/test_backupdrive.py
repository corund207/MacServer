"""Which disk may be erased for the USB backup drive (admin/backupdrive.py)."""
from pathlib import Path
import sys
import unittest
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "admin"))
import backupdrive  # noqa: E402

GB = 2**30


def disk(name, tran="usb", size=233 * GB, children=(), mountpoints=(), ro=False, kind="disk"):
    return {"name": name, "path": f"/dev/{name}", "type": kind, "size": size, "tran": tran, "ro": ro, "rm": tran == "usb",
            "model": "Test Drive", "vendor": "TEST", "fstype": None, "label": None, "mountpoints": list(mountpoints),
            "children": list(children)}


def part(name, fstype="ext4", label=None, size=GB, mountpoints=()):
    return {"name": name, "path": f"/dev/{name}", "type": "part", "size": size, "fstype": fstype, "label": label,
            "mountpoints": list(mountpoints)}


# What the real Mac reports with the owner's SanDisk plugged in (trimmed): the internal disk
# holds / and /boot, zram is swap, and the stick has a Jetson installer on it.
MAC = {"blockdevices": [
    disk("sda", children=[part("sda1", "iso9660", "jetsoninstaller-r39.2.1", 4 * GB), part("sda2", "vfat", None, 2**29),
                          part("sda4", "ext4", "writable", 228 * GB)]),
    disk("zram0", tran=None, size=4 * GB, mountpoints=["[SWAP]"]),
    disk("nvme0n1", tran="nvme", children=[part("nvme0n1p1", "vfat", "EFI", 2**29, ["/boot/efi"]),
                                           part("nvme0n1p3", "crypto_LUKS", None, 232 * GB,
                                                [])]),
]}


class BackupDriveTests(unittest.TestCase):
    def verdicts(self, data):
        return {d["path"]: d["eligible"] for d in backupdrive.disks(data)}

    def test_only_the_usb_stick_may_be_erased_on_the_real_mac(self):
        self.assertEqual(self.verdicts(MAC), {"/dev/sda": True, "/dev/zram0": False, "/dev/nvme0n1": False})
        ok, why = backupdrive.check(MAC, "/dev/nvme0n1")
        self.assertFalse(ok)
        self.assertIn("not a USB disk", why)

    def test_a_partition_or_an_unknown_path_is_never_accepted(self):
        for path in ("/dev/sda1", "/dev/sda4", "/dev/nvme0n1p3", "/dev/sdz", "sda", ""):
            with self.subTest(path=path):
                self.assertFalse(backupdrive.check(MAC, path)[0])

    def test_a_disk_in_use_is_refused_with_where(self):
        mounted = {"blockdevices": [disk("sdb", children=[part("sdb1", mountpoints=["/mnt/photos"])])]}
        ok, why = backupdrive.check(mounted, "/dev/sdb")
        self.assertFalse(ok)
        self.assertIn("/mnt/photos", why)
        # A USB disk the system itself runs from is refused the same way.
        system = {"blockdevices": [disk("sdc", children=[part("sdc1", mountpoints=["/"])])]}
        self.assertFalse(backupdrive.check(system, "/dev/sdc")[0])

    def test_older_lsblk_with_a_single_mountpoint_is_understood(self):
        child = part("sdb1")
        child.pop("mountpoints")
        child["mountpoint"] = "/media/usb"
        top = disk("sdb", children=[child])
        top.pop("mountpoints")
        self.assertFalse(backupdrive.check({"blockdevices": [top]}, "/dev/sdb")[0])

    def test_too_small_read_only_and_non_disk_devices_are_refused(self):
        cases = {"small": disk("sdb", size=4 * GB), "readonly": disk("sdb", ro=True),
                 "loop": disk("loop0", tran=None), "sata": disk("sdb", tran="sata"),
                 "lvm": disk("dm-0", kind="lvm")}
        for label, d in cases.items():
            with self.subTest(label):
                self.assertFalse(backupdrive.check({"blockdevices": [d]}, d["path"])[0])

    def test_describe_lists_what_would_be_lost(self):
        text = backupdrive.describe(backupdrive.find(MAC, "/dev/sda"))
        for wanted in ("/dev/sda", "233 GB", "jetsoninstaller-r39.2.1", "writable", "512 MB"):
            self.assertIn(wanted, text)
        self.assertIn("(no partitions)", backupdrive.describe(backupdrive.find({"blockdevices": [disk("sdb")]}, "/dev/sdb")))

    def test_command_line_exit_codes(self):
        with mock.patch.object(backupdrive, "read_lsblk", return_value=MAC):
            self.assertEqual(backupdrive.main(["check", "/dev/sda"]), 0)
            self.assertEqual(backupdrive.main(["check", "/dev/nvme0n1"]), 1)
            self.assertEqual(backupdrive.main(["describe", "/dev/sda1"]), 1)
            self.assertEqual(backupdrive.main(["list", "--eligible"]), 0)

    def test_no_disks_listed_is_not_a_crash(self):
        self.assertEqual(backupdrive.disks({}), [])
        self.assertFalse(backupdrive.check({}, "/dev/sda")[0])


if __name__ == "__main__":
    unittest.main()
