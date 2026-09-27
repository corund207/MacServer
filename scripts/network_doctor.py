#!/usr/bin/env python3
"""Diagnose first-boot networking on the MacBook and optionally repair DNS.

`check` is read-only. `fix-dns` changes /etc/resolv.conf and the detected DHCP
client's configuration only after confirmation, keeping a timestamped backup.
"""
import argparse
import ipaddress
import os
from pathlib import Path
import socket
import sys
import threading
import time

DEFAULT_NAMESERVERS = ("9.9.9.9", "149.112.112.112")
MARKER = "# macserver network_doctor: DNS override"
PROBE_HOST = "deb.debian.org"

KINDS = {
    "ipheth": "iPhone USB tethering",
    "rndis_host": "Android USB tethering",
    "cdc_ncm": "USB Ethernet or phone tethering",
    "cdc_ether": "USB Ethernet or phone tethering",
    "r8152": "USB Ethernet adapter",
    "ax88179_178a": "USB Ethernet adapter",
    "asix": "USB Ethernet adapter",
    "brcmfmac": "internal Broadcom Wi-Fi",
}


def classify(driver, wireless):
    if driver in KINDS:
        return KINDS[driver]
    if wireless:
        return "Wi-Fi"
    return "wired Ethernet" if driver else "virtual"


def interfaces(root):
    result = []
    base = root / "sys/class/net"
    for entry in sorted(base.iterdir()) if base.is_dir() else []:
        driver_link = entry / "device/driver"
        driver = os.path.basename(os.readlink(driver_link)) if driver_link.is_symlink() else ""
        wireless = (entry / "wireless").exists() or (entry / "phy80211").exists()
        try:
            carrier = (entry / "carrier").read_text().strip() == "1"
        except OSError:
            carrier = False
        kind = classify(driver, wireless)
        if entry.name != "lo" and kind != "virtual":
            result.append({"name": entry.name, "driver": driver, "kind": kind, "carrier": carrier})
    return result


def default_route_interface(route_text):
    for line in route_text.splitlines()[1:]:
        fields = line.split()
        if len(fields) > 3 and fields[1] == "00000000" and int(fields[3], 16) & 0x2:
            return fields[0]
    return None


def nameservers(resolv_text):
    found = []
    for line in resolv_text.splitlines():
        fields = line.split()
        if len(fields) >= 2 and fields[0] == "nameserver":
            found.append(fields[1])
    return found


def broadcom_wifi(root):
    """Return (hardware_present, apple_firmware_present)."""
    present = False
    for device in sorted((root / "sys/bus/pci/devices").glob("*")):
        try:
            vendor = (device / "vendor").read_text().strip()
            klass = (device / "class").read_text().strip()
        except OSError:
            continue
        present = present or (vendor == "0x14e4" and klass.startswith("0x0280"))
    firmware = any((root / "lib/firmware/brcm").glob("brcmfmac*-pcie.apple,*"))
    return present, firmware


def dhcp_client(root):
    if (root / "sbin/dhclient").exists() or (root / "usr/sbin/dhclient").exists():
        return "dhclient"
    if (root / "usr/sbin/dhcpcd").exists() or (root / "sbin/dhcpcd").exists():
        return "dhcpcd"
    return None


def _with_timeout(action, seconds):
    outcome = []
    worker = threading.Thread(target=lambda: outcome.append(action()), daemon=True)
    worker.start()
    worker.join(seconds)
    return bool(outcome and outcome[0])


def probe_ip():
    def attempt():
        try:
            with socket.create_connection((DEFAULT_NAMESERVERS[0], 53), timeout=4):
                return True
        except OSError:
            return False
    return _with_timeout(attempt, 6)


def probe_dns():
    def attempt():
        try:
            return bool(socket.getaddrinfo(PROBE_HOST, 443, proto=socket.IPPROTO_TCP))
        except OSError:
            return False
    return _with_timeout(attempt, 10)


def diagnose(facts):
    """Map observed facts to one verdict plus ordered operator steps."""
    links = [i for i in facts["interfaces"] if i["carrier"]]
    steps = []
    if facts["wifi_hardware"] and not facts["wifi_firmware"]:
        steps.append("Internal Wi-Fi needs Apple's firmware files; see 'Optional: internal Wi-Fi' "
                     "in docs/NETWORK.md. Use a wired link or tethering for the install.")
    if not facts["route"]:
        if not facts["interfaces"]:
            steps.append("No usable network device found. Plug in a USB-C Ethernet adapter "
                         "(Realtek RTL8153 or ASIX AX88179) or an Android phone with USB tethering.")
        elif not links:
            steps.append("A network device exists but has no link. Check the cable, or on the "
                         "iPhone unlock it, tap Trust, and turn on Personal Hotspot.")
        else:
            names = " ".join(i["name"] for i in links)
            steps.append(f"Link is up on {names} but no address was obtained. Run: "
                         f"sudo ifdown {links[0]['name']}; sudo ifup {links[0]['name']}  "
                         "(add an 'allow-hotplug NAME' + 'iface NAME inet dhcp' entry to "
                         "/etc/network/interfaces first if ifup does not know it)")
        return "no-network", steps
    if not facts["ip_ok"]:
        steps.append("A default route exists but the Internet is unreachable. Check that the "
                     "phone has mobile data or the router is online.")
        return "no-internet", steps
    if not facts["dns_ok"]:
        steps.append("The Internet works but name lookups fail: the DHCP-supplied DNS server "
                      "(common with iPhone Personal Hotspot) does not answer. Run: "
                      "sudo python3 scripts/network_doctor.py fix-dns")
        return "dns-broken", steps
    return "ok", steps


def gather(root):
    try:
        route = (root / "proc/net/route").read_text()
    except OSError:
        route = ""
    try:
        resolv = (root / "etc/resolv.conf").read_text()
    except OSError:
        resolv = ""
    hardware, firmware = broadcom_wifi(root)
    return {"interfaces": interfaces(root), "route": default_route_interface(route),
            "nameservers": nameservers(resolv), "wifi_hardware": hardware,
            "wifi_firmware": firmware}


def check(root):
    facts = gather(root)
    facts["ip_ok"] = bool(facts["route"]) and probe_ip()
    facts["dns_ok"] = facts["ip_ok"] and probe_dns()
    verdict, steps = diagnose(facts)
    print("Network devices:")
    for item in facts["interfaces"] or [{"name": "(none)", "kind": "", "driver": "", "carrier": False}]:
        state = "link up" if item["carrier"] else "no link"
        print(f"  {item['name']:<16} {item['kind']:<32} {state}")
    print(f"Default route:   {facts['route'] or 'none'}")
    print(f"DNS servers:     {' '.join(facts['nameservers']) or 'none'}")
    print(f"Internet by IP:  {'yes' if facts['ip_ok'] else 'no'}")
    print(f"Name lookup:     {'yes' if facts['dns_ok'] else 'no'} ({PROBE_HOST})")
    print(f"Verdict:         {verdict}")
    for number, step in enumerate(steps, 1):
        print(f"  {number}. {step}")
    return 0 if verdict == "ok" else 1


def plan_fix(root, servers, client):
    """Return [(path, new_text, old_text_or_None)] for the DNS override."""
    body = "".join(f"nameserver {s}\n" for s in servers)
    changes = [(root / "etc/resolv.conf", f"{MARKER}\n{body}", None)]
    if client == "dhclient":
        path = root / "etc/dhcp/dhclient.conf"
        line = f"supersede domain-name-servers {', '.join(servers)};\n"
    elif client == "dhcpcd":
        path = root / "etc/dhcpcd.conf"
        line = f"static domain_name_servers={' '.join(servers)}\n"
    else:
        path = None
    if path is not None:
        old = path.read_text() if path.exists() else None
        text = old or ""
        if MARKER not in text:
            separator = "" if text.endswith("\n") or not text else "\n"
            changes.append((path, f"{text}{separator}{MARKER}\n{line}", old))
    resolv = changes[0][0]
    changes[0] = (resolv, changes[0][1], resolv.read_text() if resolv.exists() else None)
    return changes


def apply_fix(root, servers, assume_yes, stream_in=sys.stdin):
    for server in servers:
        ipaddress.ip_address(server)
    resolv = root / "etc/resolv.conf"
    if resolv.is_symlink():
        print("/etc/resolv.conf is managed by another service (symlink). Not changed.\n"
              "If systemd-resolved is in use, run: sudo resolvectl dns INTERFACE "
              + " ".join(servers))
        return 2
    if root == Path("/") and os.geteuid() != 0:
        print("fix-dns must run with sudo.")
        return 2
    client = dhcp_client(root)
    changes = plan_fix(root, servers, client)
    print("This will change:")
    for path, _new, _old in changes:
        print(f"  {path}")
    print(f"DNS servers: {' '.join(servers)} (DHCP client: {client or 'none detected'})")
    if not assume_yes:
        print("Type 'yes' to continue: ", end="", flush=True)
        if stream_in.readline().strip() != "yes":
            print("No changes made.")
            return 1
    stamp = time.strftime("%Y%m%d%H%M%S")
    for path, new, old in changes:
        if old is not None:
            backup = path.with_name(f"{path.name}.macserver-{stamp}")
            counter = 1
            while backup.exists():
                backup = path.with_name(f"{path.name}.macserver-{stamp}-{counter}")
                counter += 1
            backup.write_text(old)
            print(f"Backup: {backup}")
        else:
            print(f"Created: {path} (undo by deleting it)")
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(new)
        os.chmod(path, 0o644)
    print("DNS override applied. Undo by copying each backup over the file it came from.")
    return 0


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", default="/", help=argparse.SUPPRESS)
    sub = parser.add_subparsers(dest="command")
    sub.add_parser("check", help="read-only network diagnosis (default)")
    fix = sub.add_parser("fix-dns", help="use fixed public DNS servers instead of the DHCP ones")
    fix.add_argument("--nameserver", action="append", dest="servers",
                     help=f"DNS server IP (repeatable; default {' '.join(DEFAULT_NAMESERVERS)})")
    fix.add_argument("--yes", action="store_true", help="skip the confirmation prompt")
    args = parser.parse_args(argv)
    root = Path(args.root)
    if args.command == "fix-dns":
        try:
            return apply_fix(root, args.servers or list(DEFAULT_NAMESERVERS), args.yes)
        except ValueError as error:
            print(f"Invalid nameserver: {error}")
            return 2
    return check(root)


if __name__ == "__main__":
    sys.exit(main())
