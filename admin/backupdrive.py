#!/usr/bin/env python3
"""USB backup drive: which disk may be erased for it, and a plain-language description of it.

`macserver backup-drive setup` (installer/steps/65-backup-drive.sh) erases a disk, so the
decision "this disk is allowed" lives here, apart from the shell that does the erasing, and
is deliberately narrow: a whole disk that is connected over USB, big enough to be a backup
drive, writable, with nothing on it in use. The Mac's own disk, a partition, a loop device,
swap and anything mounted are refused with a reason.

    backupdrive.py list [--eligible]   one line per disk: PATH<TAB>description (or the reason)
    backupdrive.py check DISK          exit 0 when DISK may be erased, else print why and exit 1
    backupdrive.py describe DISK       what is on DISK, for the "type ERASE" prompt

Standard library only. It never writes to a disk; it only reads `lsblk`.
"""
import argparse
import json
import subprocess
import sys

MIN_SIZE = 8 * 2**30     # smaller than this is not a drive meant for backups
LSBLK = ["lsblk", "-J", "-b", "-o", "NAME,PATH,TYPE,SIZE,MODEL,VENDOR,TRAN,RM,RO,FSTYPE,LABEL,MOUNTPOINTS"]


def gib(size):
    size = int(size)
    return f"{size / 2**30:.0f} GB" if size >= 2**30 else f"{size / 2**20:.0f} MB"


def walk(node):
    """The node and everything below it (partitions, LUKS mappings, LVM volumes)."""
    yield node
    for child in node.get("children") or []:
        yield from walk(child)


def mounts(node):
    """Every mount point under the node. Newer lsblk says `mountpoints` (a list), older `mountpoint`."""
    found = []
    for item in walk(node):
        values = item.get("mountpoints")
        if values is None:
            values = [item.get("mountpoint")]
        found += [m for m in values if m]
    return found


def truthy(value):
    return value in (True, 1, "1", "true")


def report(disk):
    """One top-level lsblk entry as {path, name, size, tran, parts, eligible, why}."""
    path = disk.get("path") or "/dev/" + disk.get("name", "?")
    size = int(disk.get("size") or 0)
    name = " ".join(x for x in ((disk.get("vendor") or "").strip(), (disk.get("model") or "").strip()) if x)
    parts = [{"path": p.get("path") or p.get("name"), "fstype": p.get("fstype") or "", "label": p.get("label") or "",
              "size": int(p.get("size") or 0)} for p in walk(disk) if p is not disk and p.get("type") == "part"]
    used = mounts(disk)
    why = None
    if disk.get("type") != "disk":
        why = f"it is a {disk.get('type') or 'device'}, not a whole disk"
    elif disk.get("tran") != "usb":
        why = f"it is not a USB disk (it is connected as {disk.get('tran') or 'something else'})"
    elif truthy(disk.get("ro")):
        why = "it is read-only"
    elif size < MIN_SIZE:
        why = f"it is only {gib(size)}; a backup drive needs at least {gib(MIN_SIZE)}"
    elif used:
        why = "it is in use (" + ", ".join(sorted(set(used))) + "); unmount it first"
    return {"path": path, "name": name or "unknown model", "size": size, "tran": disk.get("tran") or "",
            "parts": parts, "eligible": why is None, "why": why or ""}


def disks(lsblk_json):
    return [report(d) for d in (lsblk_json or {}).get("blockdevices", [])]


def find(lsblk_json, path):
    """The report for a top-level disk, or None. A partition (/dev/sda1) is not found on purpose."""
    return next((d for d in disks(lsblk_json) if d["path"] == path), None)


def check(lsblk_json, path):
    """(ok, reason) for erasing `path`."""
    found = find(lsblk_json, path)
    if found is None:
        return False, f"{path} is not a whole disk on this Mac (give the disk, like /dev/sda, not a partition)"
    return found["eligible"], found["why"]


def describe(found):
    lines = [f"{found['path']}  {found['name']}, {gib(found['size'])}"]
    for p in found["parts"]:
        what = " ".join(x for x in (p["fstype"], f"\"{p['label']}\"" if p["label"] else "") if x) or "unknown contents"
        lines.append(f"    {p['path']}  {gib(p['size'])}  {what}")
    if not found["parts"]:
        lines.append("    (no partitions)")
    return "\n".join(lines)


def read_lsblk():
    out = subprocess.run(LSBLK, capture_output=True, text=True, timeout=20, check=True).stdout
    return json.loads(out)


def main(argv=None):
    parser = argparse.ArgumentParser(description="which USB disk may be erased for backups")
    sub = parser.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("list")
    p.add_argument("--eligible", action="store_true", help="only the disks that may be erased")
    for name in ("check", "describe"):
        sub.add_parser(name).add_argument("disk")
    args = parser.parse_args(argv)
    try:
        data = read_lsblk()
    except (OSError, ValueError, subprocess.SubprocessError) as error:
        print(f"could not list the disks: {error}", file=sys.stderr)
        return 2
    if args.cmd == "list":
        for d in disks(data):
            if args.eligible and not d["eligible"]:
                continue
            print(f"{d['path']}\t{d['name']}, {gib(d['size'])}" + ("" if d["eligible"] else f"  [refused: {d['why']}]"))
        return 0
    if args.cmd == "check":
        ok, why = check(data, args.disk)
        if not ok:
            print(why)
        return 0 if ok else 1
    found = find(data, args.disk)
    if found is None:
        print(f"{args.disk} is not a whole disk on this Mac", file=sys.stderr)
        return 1
    print(describe(found))
    return 0


if __name__ == "__main__":
    sys.exit(main())
