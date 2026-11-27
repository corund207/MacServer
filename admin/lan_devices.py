#!/usr/bin/env python3
"""Who is on the local network, exactly: the name, model, system and maker of every device, what it
exposes, and an alert when something new or suspicious appears.

    lan_devices.py watch                the service (macserver-devices.service): listen, look every 10 min
    lan_devices.py scan                 one look now, without the service (debugging)
    lan_devices.py show [who]           the device list, or everything known about one device
    lan_devices.py name <who> <name>    give a device your own name (root)
    lan_devices.py trust <who|all>      mark devices as yours: their "new device" alert stops (root)
    lan_devices.py untrust <who>        (root)
    lan_devices.py accept-router        you replaced the router: accept its new hardware address (root)

<who> is an address (192.168.18.4), a hardware address (d8:31:34:67:b6:38) or part of a name.

How a device is identified. Every source is described where it is implemented below.
  listening  DHCP (a device joining names itself and its system: "iPhone", "android-dhcp-14", "MSFT 5.0"),
             Bonjour/mDNS announcements, UPnP/SSDP notices, NetBIOS broadcasts, ARP. A packet socket
             with a kernel filter for those protocols only: it reads broadcast and multicast traffic,
             opens no port and answers nothing. It also sees the answers to the Mac's own multicast
             questions, before the firewall drops them. The firewall is unchanged.
  asking     a ping sweep of the subnet; Bonjour and UPnP questions (multicast, and unicast to each
             device); the router's DNS; NetBIOS; the UPnP description of every device that offers one.
  checking   a TCP connect to telltale ports (62078 = iPhone/iPad, 8060 = Roku...), and every few days an
             nmap service scan of each device (open ports, versions, TLS certificates, Windows names):
             Debian's nmap, connect scan only, no privileges.
  registry   the maker of the hardware address, from systemd's offline copy of the IEEE registry.

Security. A device first seen after the first half hour raises a "new device" alert until you trust
it. A second hardware address answering as the router (ARP spoofing), a second DHCP server and a flood
of hardware addresses raise critical ones; so does a device with Telnet or Android debugging open.
What devices say is untrusted: every parser is bounds-checked, every string is cut to printable text,
UPnP descriptions are fetched only from the device's own private address, without redirects. The
service runs as a throwaway user with CAP_NET_RAW only (for the packet socket).
"""
import ctypes
import html
import ipaddress
import json
import os
import queue
import random
import re
import shutil
import signal
import socket
import struct
import subprocess
import sys
import threading
import time
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

PROC = Path(os.environ.get("MACSERVER_PROC", "/proc"))
STATE = Path(os.environ.get("STATE_DIRECTORY", "/var/lib/macserver-devices")) / "devices.json"
KNOWN = Path(os.environ.get("MACSERVER_DEVICES_KNOWN", "/etc/macserver/devices-known.json"))
STATUS = Path("/run/macserver/status.json")
OUI_FILES = (Path("/usr/lib/udev/hwdb.d/20-OUI.hwdb"), Path("/lib/udev/hwdb.d/20-OUI.hwdb"))

LOOK_S = 600                 # an active look every 10 minutes (listening never stops)
MAX_SWEEP = 1024             # a bigger subnet is not a home network: look only at the devices already known
FORGET_S = 30 * 86400        # a device not seen for a month leaves the list
BASELINE_S = 1800            # devices found in the first half hour are the household, not intruders
NEW_S = 86400                # a new device is flagged this long, unless trusted sooner
PORTS_EVERY_S = 6 * 3600     # telltale ports, per device
UPNP_EVERY_S = 86400         # UPnP description, per device
DEEP_EVERY_S = 3 * 86400     # nmap service scan, per device
DEEP_PER_LOOK = 3            # nmap scans per look, new devices first
MAX_DEVICES = 512            # more than this many hardware addresses is a flood, not a home
NAME_MAX = 80

A, PTR, TXT, SRV = 1, 12, 16, 33
IP_RECVTTL, IP_TTL = 12, 2   # Linux socket option numbers (socket.IP_RECVTTL is missing on some builds)


# --- small helpers ---------------------------------------------------------------------------------

def clean(text, limit=NAME_MAX):
    """Printable text only, one line, at most `limit` characters: a device chooses its own name."""
    text = "".join(ch if ch.isprintable() else " " for ch in str(text or ""))
    return " ".join(text.split())[:limit].strip()


def private_mac(mac):
    """A locally administered (randomised) address: phones and laptops use one per network."""
    try:
        return bool(int(mac[:2], 16) & 2)
    except ValueError:
        return False


def usable_mac(mac):
    """A device's own address: not broadcast, multicast or all zeros."""
    try:
        return not int(mac[:2], 16) & 1 and mac != "00:00:00:00:00:00"
    except ValueError:
        return False


def fmt_mac(raw):
    return ":".join(f"{b:02x}" for b in raw)


def read(path, default=""):
    try:
        return Path(path).read_text()
    except OSError:
        return default


def load_json(path):
    try:
        data = json.loads(Path(path).read_text())
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def ip_order(ip):
    try:
        return [int(n) for n in str(ip).split(".")]
    except ValueError:
        return [999]


def ago(seconds):
    seconds = max(0, int(seconds))
    if seconds < 90:
        return "now"
    if seconds < 3600:
        return f"{seconds // 60} min ago"
    if seconds < 86400:
        return f"{seconds // 3600} h ago"
    return f"{seconds // 86400} d ago"


# --- the network the Mac is on ---------------------------------------------------------------------

def default_route(route_text):
    """(interface, gateway, network) of the default route, from /proc/net/route; None when there is none."""
    rows = [line.split() for line in route_text.splitlines()[1:] if len(line.split()) >= 8]
    hexip = lambda h: socket.inet_ntoa(struct.pack("<I", int(h, 16)))
    for f in rows:
        if f[1] == "00000000" and f[7] == "00000000":
            iface, gateway = f[0], hexip(f[2])
            for g in rows:
                if g[0] == iface and g[1] != "00000000" and g[2] == "00000000":
                    net = ipaddress.ip_network(f"{hexip(g[1])}/{hexip(g[7])}", strict=False)
                    if ipaddress.ip_address(gateway) in net:
                        return iface, gateway, net
            return iface, gateway, ipaddress.ip_network(f"{gateway}/24", strict=False)
    return None


def own_address(gateway):
    """The Mac's address on the way to the router (no packet is sent)."""
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
        try:
            s.connect((gateway, 9))
            return s.getsockname()[0]
        except OSError:
            return None


def neighbours(arp_text, iface):
    """{ip: mac} of the complete entries on this interface in the kernel's neighbour table."""
    out = {}
    for line in arp_text.splitlines()[1:]:
        f = line.split()
        if len(f) >= 6 and f[5] == iface and f[2] != "0x0" and f[3] != "00:00:00:00:00:00":
            out[f[0]] = f[3].lower()
    return out


# --- asking 1: the sweep, ping everyone and note the reply's TTL --------------------------------------

def sweep(addresses, wait=2.5):
    """{ip: ttl} of the addresses that answered a ping. Pinging makes the kernel find every device's
    hardware address (ARP), so afterwards its neighbour table lists every device that is on. Uses an
    unprivileged ping socket (net.ipv4.ping_group_range); without one, a UDP packet still makes it ARP."""
    ttls = {}
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM, socket.IPPROTO_ICMP)
    except OSError:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as u:
            for ip in addresses:
                try:
                    u.sendto(b"", (ip, 9))
                except OSError:
                    pass
        time.sleep(wait)
        return ttls
    with s:
        s.setsockopt(socket.IPPROTO_IP, IP_RECVTTL, 1)
        for n, ip in enumerate(addresses):
            try:
                s.sendto(struct.pack("!BBHHH", 8, 0, 0, 0, n & 0xFFFF) + b"macserver", (ip, 0))
            except OSError:
                pass
            if n % 64 == 63:
                time.sleep(0.02)                 # a gentle sweep, not a burst
        deadline = time.monotonic() + wait
        while (left := deadline - time.monotonic()) > 0:
            s.settimeout(left)
            try:
                data, anc, _, addr = s.recvmsg(256, socket.CMSG_SPACE(4))
            except (socket.timeout, OSError):
                break
            if data[:1] != b"\x00":              # an echo reply
                continue
            for level, kind, value in anc:
                if level == socket.IPPROTO_IP and kind == IP_TTL and len(value) >= 4:
                    ttls[addr[0]] = struct.unpack("i", value[:4])[0]
    return ttls


# --- DNS messages (the router's DNS and mDNS speak the same format) -----------------------------------

def encode_name(name):
    out = b""
    for label in name.rstrip(".").split("."):
        raw = label.encode("utf-8")
        if not 0 < len(raw) < 64:
            raise ValueError(f"bad label in {name!r}")
        out += bytes([len(raw)]) + raw
    return out + b"\x00"


def dns_query(questions, qid, recursion=False):
    """A query message: questions = [(name, type)]."""
    head = struct.pack("!HHHHHH", qid, 0x0100 if recursion else 0, len(questions), 0, 0, 0)
    return head + b"".join(encode_name(name) + struct.pack("!HH", qtype, 1) for name, qtype in questions)


def read_name(buf, pos):
    """A (possibly compressed) name at `pos`: (name, position after it). Raises ValueError on a bad message."""
    labels, end, jumps = [], None, 0
    while True:
        if pos >= len(buf):
            raise ValueError("name runs past the end")
        n = buf[pos]
        if n == 0:
            pos += 1
            break
        if n & 0xC0 == 0xC0:
            if pos + 1 >= len(buf):
                raise ValueError("cut pointer")
            if end is None:
                end = pos + 2
            pos = ((n & 0x3F) << 8) | buf[pos + 1]
            jumps += 1
            if jumps > 32:
                raise ValueError("pointer loop")
            continue
        if n & 0xC0:
            raise ValueError("bad label type")
        labels.append(buf[pos + 1:pos + 1 + n].decode("utf-8", "replace"))
        pos += 1 + n
        if len(labels) > 64:
            raise ValueError("too many labels")
    return ".".join(labels), end if end is not None else pos


def txt_items(rdata):
    """A TXT record's key=value strings as a dict (keys lower-case)."""
    out, pos = {}, 0
    while pos < len(rdata):
        n = rdata[pos]
        item = rdata[pos + 1:pos + 1 + n].decode("utf-8", "replace")
        pos += 1 + n
        key, _, value = item.partition("=")
        if key and len(out) < 64:
            out[key.lower()] = value
    return out


def parse_dns(buf):
    """(id, [(name, type, value)]): PTR and SRV values are names, TXT a dict, A an address."""
    if len(buf) < 12:
        raise ValueError("short message")
    qid, _, qd, an, ns, ar = struct.unpack_from("!HHHHHH", buf)
    pos = 12
    for _ in range(min(qd, 64)):
        _, pos = read_name(buf, pos)
        pos += 4
    records = []
    for _ in range(min(an + ns + ar, 256)):
        name, pos = read_name(buf, pos)
        if pos + 10 > len(buf):
            raise ValueError("cut record")
        rtype, _, _, length = struct.unpack_from("!HHIH", buf, pos)
        pos += 10
        start, pos = pos, pos + length
        if pos > len(buf):
            raise ValueError("record runs past the end")
        value = None
        if rtype == PTR:
            value = read_name(buf, start)[0]
        elif rtype == SRV and length > 6:
            value = read_name(buf, start + 6)[0]
        elif rtype == TXT:
            value = txt_items(buf[start:pos])
        elif rtype == A and length == 4:
            value = socket.inet_ntoa(buf[start:pos])
        records.append((name, rtype, value))
    return qid, records


def ask(ip, port, message, qid, timeout):
    """Send one DNS message to ip:port and return the records of its answer ([] when none comes)."""
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
        s.settimeout(timeout)
        try:
            s.sendto(message, (ip, port))
            deadline = time.monotonic() + timeout
            while (left := deadline - time.monotonic()) > 0:
                s.settimeout(left)
                data, addr = s.recvfrom(9000)
                if addr[0] != ip:
                    continue
                try:
                    rid, records = parse_dns(data)
                except (ValueError, struct.error, IndexError):
                    return []
                if rid == qid:
                    return records
        except OSError:
            pass
    return []


def reverse_name(ip):
    return ".".join(reversed(ip.split("."))) + ".in-addr.arpa"


# --- asking 2: the router's DNS (many routers name devices after their DHCP host name) ------------------

def router_name(gateway, ip, timeout=0.8):
    qid = random.randrange(65536)
    for name, rtype, value in ask(gateway, 53, dns_query([(reverse_name(ip), PTR)], qid, recursion=True), qid, timeout):
        if rtype == PTR and value:
            host = value.split(".")[0]
            if host and not re.fullmatch(r"[\d-]+|(ip|host|dhcp|unknown|localhost)[\w-]*", host, re.I):
                return clean(host)
    return ""


# --- asking 3: Bonjour (mDNS), unicast to the device and multicast to everyone --------------------------

MDNS_SERVICES = ("_companion-link._tcp", "_device-info._tcp", "_airplay._tcp", "_raop._tcp", "_googlecast._tcp",
                 "_hap._tcp", "_spotify-connect._tcp", "_rdlink._tcp", "_apple-mobdev2._tcp", "_amzn-wplay._tcp",
                 "_ipp._tcp", "_ipps._tcp", "_printer._tcp", "_pdl-datastream._tcp", "_smb._tcp", "_sleep-proxy._udp",
                 "_androidtvremote2._tcp", "_sonos._tcp", "_matter._tcp", "_workstation._tcp", "_ssh._tcp", "_rfb._tcp",
                 "_adisk._tcp", "_touch-able._tcp", "_mediaremotetv._tcp", "_airport._tcp", "_nvstream._tcp",
                 "_http._tcp", "_hue._tcp", "_viziocast._tcp", "_daap._tcp", "_homekit._tcp")


def mdns(ip, timeout=0.7):
    """[(name, type, value)] the device's own mDNS answers to: its reverse name, its service list, and
    the instances of the services that carry a name or a model."""
    qid = random.randrange(65536)
    first = ask(ip, 5353, dns_query([(reverse_name(ip), PTR), ("_services._dns-sd._udp.local", PTR)], qid), qid, timeout)
    if not first:
        return []
    services = {v.removesuffix(".local").removesuffix(".") for n, t, v in first
                if t == PTR and v and n.startswith("_services._dns-sd")}
    wanted = [s for s in MDNS_SERVICES if s in services][:8] + ["_device-info._tcp"]
    qid = random.randrange(65536)
    return first + ask(ip, 5353, dns_query([(f"{s}.local", PTR) for s in dict.fromkeys(wanted)], qid), qid, timeout)


def multicast_socket(me):
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    if me:
        s.setsockopt(socket.IPPROTO_IP, socket.IP_MULTICAST_IF, socket.inet_aton(me))
    s.setsockopt(socket.IPPROTO_IP, socket.IP_MULTICAST_TTL, 1)
    s.setsockopt(socket.IPPROTO_IP, socket.IP_MULTICAST_LOOP, 0)
    return s


def mdns_multicast(me):
    """Ask everyone (224.0.0.251) for the services above. The query comes from an ordinary port, so
    every device answers by unicast (RFC 6762, legacy unicast). Those answers are not replies the
    firewall knows, so it drops them; the listener has already read them."""
    types = ("_services._dns-sd._udp",) + MDNS_SERVICES
    with multicast_socket(me) as s:
        for i in range(0, len(types), 10):
            try:
                s.sendto(dns_query([(f"{t}.local", PTR) for t in types[i:i + 10]], 0), ("224.0.0.251", 5353))
            except OSError:
                pass


def ssdp_search(me):
    """Ask every UPnP device (239.255.255.250) to say what it is; answers reach the listener."""
    with multicast_socket(me) as s:
        for target in ("ssdp:all", "upnp:rootdevice"):
            msg = (f'M-SEARCH * HTTP/1.1\r\nHOST: 239.255.255.250:1900\r\nMAN: "ssdp:discover"\r\n'
                   f"MX: 2\r\nST: {target}\r\nUSER-AGENT: MacServer/1 UPnP/1.1 device-check/1\r\n\r\n")
            try:
                s.sendto(msg.encode(), ("239.255.255.250", 1900))
            except OSError:
                pass


# --- asking 4: NetBIOS, a Windows PC's computer name ----------------------------------------------------

def nb_decode(enc):
    """A NetBIOS first-level-encoded name (32 letters): (name, suffix byte)."""
    raw = bytes(((enc[i] - 0x41) << 4 | (enc[i + 1] - 0x41)) & 0xFF for i in range(0, 32, 2))
    return clean(raw[:15].decode("ascii", "replace"), 15), raw[15]


def netbios(ip, timeout=0.6):
    """The computer name a Windows PC (or a Samba server) gives in a NetBIOS node status answer."""
    qid = random.randrange(65536)
    wildcard = b"".join(bytes([0x41 + (c >> 4), 0x41 + (c & 15)]) for c in b"*" + b"\x00" * 15)
    message = struct.pack("!HHHHHH", qid, 0, 1, 0, 0, 0) + bytes([32]) + wildcard + b"\x00" + struct.pack("!HH", 0x21, 1)
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
        s.settimeout(timeout)
        try:
            s.sendto(message, (ip, 137))
            data, addr = s.recvfrom(2048)
        except OSError:
            return ""
    try:
        if addr[0] != ip or struct.unpack_from("!H", data)[0] != qid:
            return ""
        pos = 12 + 34 + 10                       # header, the echoed name, type/class/ttl/length
        for n in range(min(data[pos], 64)):
            entry = data[pos + 1 + 18 * n:pos + 19 + 18 * n]
            if len(entry) < 18:
                break
            name, suffix, flags = entry[:15].decode("ascii", "replace").strip(), entry[15], struct.unpack("!H", entry[16:18])[0]
            if suffix == 0 and not flags & 0x8000 and name:
                return clean(name)
    except (IndexError, struct.error):
        pass
    return ""


# --- asking 5: the UPnP description (a device's own statement of its exact model) -----------------------

class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):
        return None


UPNP_TAGS = ("friendlyName", "manufacturer", "modelName", "modelNumber", "modelDescription", "deviceType")


def upnp_allowed(location, ip):
    """Fetch a description only over http, only from the device's own private address (a device may
    put any URL in its notice: never let it point the Mac at the Internet or at itself)."""
    try:
        u = urllib.parse.urlsplit(location)
        return (u.scheme == "http" and u.hostname == ip and ipaddress.ip_address(ip).is_private
                and not ipaddress.ip_address(ip).is_loopback)
    except ValueError:
        return False


def upnp_describe(location, ip, timeout=3):
    if not upnp_allowed(location, ip):
        return {}
    opener = urllib.request.build_opener(_NoRedirect, urllib.request.ProxyHandler({}))
    try:
        with opener.open(urllib.request.Request(location, headers={"User-Agent": "MacServer device check"}),
                         timeout=timeout) as r:
            text = r.read(65536).decode("utf-8", "replace")
    except (OSError, ValueError):
        return {}
    return upnp_fields(text)


def upnp_fields(text):
    """The first device's identity tags from a UPnP description (a regex, not an XML parser: the
    document is untrusted and only these flat tags matter)."""
    out = {}
    for tag in UPNP_TAGS:
        m = re.search(rf"<{tag}>([^<]{{1,200}})</{tag}>", text)
        if m:
            out[tag] = clean(html.unescape(m.group(1)))
    return out


# --- checking 1: telltale ports (a TCP connect only; nothing is sent) -----------------------------------

TELLTALE = {62078: "iPhone/iPad sync service (lockdownd)", 7000: "AirPlay", 8009: "Google Cast", 8008: "Google Cast",
            8060: "Roku remote control (ECP)", 1400: "Sonos", 445: "Windows file sharing (SMB)", 3389: "Remote Desktop",
            22: "SSH", 23: "Telnet", 21: "FTP", 80: "web page", 443: "web page (HTTPS)", 554: "video stream (RTSP)",
            5555: "Android debugging (adb)", 631: "printing (IPP)", 9100: "printing (raw)", 8883: "MQTT over TLS",
            1883: "MQTT", 990: "FTP over TLS", 6000: "camera stream", 5900: "VNC screen sharing", 32400: "Plex",
            8080: "web page (8080)", 49152: "UPnP"}


def open_ports(targets, ports=TELLTALE, timeout=0.7):
    """{ip: [open ports]} for every ip in `targets`, connecting to all of them in parallel."""
    def check(pair):
        ip, port = pair
        with socket.socket() as s:
            s.settimeout(timeout)
            try:
                return pair if s.connect_ex((ip, port)) == 0 else None
            except OSError:
                return None
    out = {ip: [] for ip in targets}
    pairs = [(ip, port) for ip in targets for port in ports]
    with ThreadPoolExecutor(max_workers=48) as pool:
        for hit in pool.map(check, pairs):
            if hit:
                out[hit[0]].append(hit[1])
    return {ip: sorted(p) for ip, p in out.items()}


# --- checking 2: nmap, every few days per device ----------------------------------------------------------

NMAP_PORTS = ("21,22,23,25,53,80,81,88,110,111,135,139,143,389,443,445,515,548,554,631,873,990,1400,1883,2000,3000,"
              "3306,3389,3689,5000,5001,5357,5432,5555,5900,6000,6466,6467,6668,7000,7100,8000,8001,8002,8008,8009,"
              "8060,8080,8081,8443,8554,8883,8888,9000,9080,9100,9999,10001,32400,49152,49153,55443,62078")
NMAP_SCRIPTS = "ssl-cert,http-title,http-server-header,upnp-info,nbstat,smb-os-discovery,rdp-ntlm-info"


def nmap_scan(ip, timeout=180):
    """nmap's view of one device: a connect scan (no privileges) of the ports home devices use, the
    service and version behind each open one, and safe scripts that read names and certificates."""
    nmap = shutil.which("nmap")
    if not nmap:
        return None
    try:
        out = subprocess.run([nmap, "-n", "-Pn", "-sT", "-sV", "--version-light", "-T4", "--max-retries", "1",
                              "--unprivileged", "--host-timeout", "150s", "--script", NMAP_SCRIPTS, "--script-timeout", "20s",
                              "-p", NMAP_PORTS, "-oX", "-", ip],
                             capture_output=True, timeout=timeout, check=False)
    except (OSError, subprocess.TimeoutExpired):
        return None
    return parse_nmap(out.stdout[:4_000_000].decode("utf-8", "replace"))


def _elem(node, path):
    found = node.find(path) if node is not None else None
    return clean(found.text) if found is not None and found.text else ""


def parse_nmap(xml_text):
    """{"ports": [...], "names": [...], "certs": [...], "titles": [...], "os": ""} from nmap's XML."""
    out = {"ports": [], "names": [], "certs": [], "titles": [], "os": "", "servers": []}
    try:
        root = ET.fromstring(xml_text)
    except ET.ParseError:
        return out
    host = root.find("host")
    if host is None:
        return out
    for p in host.iter("port"):
        state = p.find("state")
        if state is None or state.get("state") != "open":
            continue
        svc = p.find("service")
        get = (lambda k: clean(svc.get(k), 60)) if svc is not None else (lambda k: "")
        out["ports"].append({"port": int(p.get("portid", 0)), "service": get("name"),
                             "product": clean(" ".join(x for x in (get("product"), get("version"), get("extrainfo")) if x), 100),
                             "tls": get("tunnel") == "ssl"})
        for sc in p.findall("script"):
            sid = sc.get("id")
            if sid == "ssl-cert":
                cert = {"cn": _elem(sc, "table[@key='subject']/elem[@key='commonName']"),
                        "issuer": _elem(sc, "table[@key='issuer']/elem[@key='organizationName']")
                        or _elem(sc, "table[@key='issuer']/elem[@key='commonName']")}
                if any(cert.values()) and cert not in out["certs"]:
                    out["certs"].append(cert)
            elif sid == "http-title" and sc.get("output"):
                title = clean(sc.get("output"))
                if title and not title.lower().startswith(("site doesn't have a title", "did not follow redirect")):
                    out["titles"].append(title)
            elif sid == "http-server-header" and sc.get("output"):
                out["servers"].append(clean(sc.get("output")))
            elif sid == "rdp-ntlm-info":
                for key in ("DNS_Computer_Name", "NetBIOS_Computer_Name"):
                    if _elem(sc, f"elem[@key='{key}']"):
                        out["names"].append(_elem(sc, f"elem[@key='{key}']"))
                version = _elem(sc, "elem[@key='Product_Version']")
                if version:
                    out["os"] = windows_from_build(version)
    for sc in host.findall("hostscript/script"):
        sid = sc.get("id")
        if sid == "nbstat":
            m = re.search(r"NetBIOS name: ([^,\s]+)", sc.get("output") or "")
            if m:
                out["names"].append(clean(m.group(1)))
        elif sid == "smb-os-discovery":
            for key in ("fqdn", "server", "NetBIOS computer name"):
                if _elem(sc, f"elem[@key='{key}']"):
                    out["names"].append(_elem(sc, f"elem[@key='{key}']").split("\\")[0])
            if _elem(sc, "elem[@key='os']"):
                out["os"] = _elem(sc, "elem[@key='os']")
    for key in ("names", "titles", "servers"):
        out[key] = list(dict.fromkeys(x for x in out[key] if x))[:6]
    out["ports"] = out["ports"][:40]
    out["certs"] = out["certs"][:6]
    return out


def windows_from_build(version):
    """ "10.0.22631" -> "Windows 11 (build 22631)". Windows 11 still says 10.0; the build tells them apart."""
    m = re.match(r"(\d+)\.(\d+)\.(\d+)", version or "")
    if not m:
        return f"Windows {version}".strip()
    major, minor, build = map(int, m.groups())
    if (major, minor) == (10, 0):
        return f"Windows {'11' if build >= 22000 else '10'} (build {build})"
    return {(6, 3): "Windows 8.1", (6, 2): "Windows 8", (6, 1): "Windows 7"}.get((major, minor), f"Windows {major}.{minor}")


# --- the registry: who made the hardware ----------------------------------------------------------------

def makers(macs, files=OUI_FILES):
    """{mac: maker} from the IEEE registry systemd ships. Longer (MA-M, MA-S) assignments win."""
    wanted = {}
    for mac in macs:
        if not private_mac(mac):
            digits = mac.replace(":", "").upper()
            for size in (9, 7, 6):
                wanted.setdefault(digits[:size], []).append(mac)
    out, best = {}, {}
    if not wanted:
        return out
    for path in files:
        text = read(path)
        if not text:
            continue
        key = None
        for line in text.splitlines():
            if line.startswith("OUI:"):
                key = line[4:].rstrip("*")
            elif key and line.startswith(" ID_OUI_FROM_DATABASE=") and key in wanted:
                for mac in wanted[key]:
                    if len(key) > best.get(mac, 0):
                        out[mac], best[mac] = clean(line.split("=", 1)[1], 64), len(key)
        break
    return out


# --- listening: a packet socket for discovery traffic only --------------------------------------------

CAPTURE_PORTS = (67, 68, 137, 138, 1900, 2021, 5353, 5355)
SOL_PACKET, PACKET_ADD_MEMBERSHIP, PACKET_MR_ALLMULTI, PACKET_OUTGOING = 263, 1, 2, 4
SO_ATTACH_FILTER = 26
DHCP_MAGIC = b"\x63\x82\x53\x63"


def bpf_program(ports=CAPTURE_PORTS):
    """A classic BPF filter, so the kernel hands over only ARP and unfragmented IPv4 UDP to or from the
    discovery ports, never the Mac's own traffic: (bytes, instruction count)."""
    ins = []
    add = lambda code, k, jt=0, jf=0: ins.append([code, jt, jf, k])
    add(0x28, 12)                        # ldh [12]          ethertype
    add(0x15, 0x0806, "accept", 0)       # jeq ARP
    add(0x15, 0x0800, 0, "drop")         # jeq IPv4, else drop
    add(0x30, 23)                        # ldb [23]          IP protocol
    add(0x15, 17, 0, "drop")             # jeq UDP
    add(0x28, 20)                        # ldh [20]          flags and fragment offset
    add(0x45, 0x1FFF, "drop", 0)         # jset: a fragment
    add(0xB1, 14)                        # ldxb 4*([14]&0xf) IP header length
    for offset in (14, 16):              # source port, then destination port
        add(0x48, offset)                # ldh [x+offset]
        for port in ports:
            add(0x15, port, "accept", 0)
    labels = {"drop": len(ins), "accept": len(ins) + 1}
    ins += [[0x06, 0, 0, 0], [0x06, 0, 0, 0x40000]]
    out = b""
    for i, (code, jt, jf, k) in enumerate(ins):
        jt = labels[jt] - i - 1 if isinstance(jt, str) else jt
        jf = labels[jf] - i - 1 if isinstance(jf, str) else jf
        out += struct.pack("HBBI", code, jt, jf, k)
    return out, len(ins)


class Listener:
    """Reads discovery traffic on one interface in a thread; the main loop takes the frames."""

    def __init__(self, iface):
        self.iface = iface
        self.frames = queue.Queue(maxsize=20000)
        self.closed = False
        self.sock = socket.socket(socket.AF_PACKET, socket.SOCK_RAW, socket.htons(3))
        try:
            program, count = bpf_program()
            self._filter = ctypes.create_string_buffer(program)
            self.sock.setsockopt(socket.SOL_SOCKET, SO_ATTACH_FILTER, struct.pack("HL", count, ctypes.addressof(self._filter)))
            self.sock.bind((iface, 0))
            # all multicast on this interface: mDNS and SSDP announcements are multicast
            self.sock.setsockopt(SOL_PACKET, PACKET_ADD_MEMBERSHIP,
                                 struct.pack("iHH8s", socket.if_nametoindex(iface), PACKET_MR_ALLMULTI, 0, b""))
        except OSError:
            self.sock.close()
            raise
        threading.Thread(target=self._run, daemon=True).start()

    def _run(self):
        while not self.closed:
            try:
                frame, addr = self.sock.recvfrom(9216)
            except OSError:
                if self.closed:
                    return
                time.sleep(0.5)
                continue
            if addr[2] == PACKET_OUTGOING:
                continue
            try:
                self.frames.put_nowait((time.time(), frame))
            except queue.Full:
                pass

    def take(self):
        while True:
            try:
                yield self.frames.get_nowait()
            except queue.Empty:
                return

    def close(self):
        self.closed = True
        self.sock.close()


def dhcp_options(p):
    if len(p) < 240 or p[236:240] != DHCP_MAGIC:
        return None
    opts, i = {}, 240
    while i < len(p) and len(opts) < 128:
        code = p[i]
        if code == 255:
            break
        if code == 0:
            i += 1
            continue
        if i + 1 >= len(p):
            break
        opts.setdefault(code, p[i + 2:i + 2 + p[i + 1]])
        i += 2 + p[i + 1]
    return opts


def dhcp_fqdn(raw):
    """Option 81 (client FQDN): flags, two legacy bytes, then the name, wire format when flag E (4) is set."""
    if len(raw) < 4:
        return ""
    if raw[0] & 4:
        try:
            return clean(read_name(raw[3:] + b"\x00", 0)[0])
        except (ValueError, IndexError):
            return ""
    return clean(raw[3:].decode("utf-8", "replace"))


def text(raw, limit=NAME_MAX):
    return clean(raw.decode("utf-8", "replace"), limit)


def parse_frame(frame):
    """What one captured Ethernet frame says: a list of observations (empty when it says nothing)."""
    try:
        return _parse_frame(frame)
    except (IndexError, ValueError, struct.error, UnicodeDecodeError, OSError):
        return []


def _parse_frame(frame):
    if len(frame) < 42:
        return []
    src = fmt_mac(frame[6:12])
    if not usable_mac(src):
        return []
    etype = struct.unpack_from("!H", frame, 12)[0]
    if etype == 0x0806:
        htype, ptype, hlen, plen, op = struct.unpack_from("!HHBBH", frame, 14)
        if (htype, ptype, hlen, plen) != (1, 0x0800, 6, 4):
            return []
        sha, spa = fmt_mac(frame[22:28]), socket.inet_ntoa(frame[28:32])
        if not usable_mac(sha):
            return []
        return [{"kind": "arp", "mac": sha, "ip": None if spa == "0.0.0.0" else spa, "op": op}]
    if etype != 0x0800 or frame[14] >> 4 != 4:
        return []
    ihl = (frame[14] & 15) * 4
    if ihl < 20 or frame[23] != 17 or struct.unpack_from("!H", frame, 20)[0] & 0x3FFF:
        return []
    sip = socket.inet_ntoa(frame[26:30])
    u = 14 + ihl
    sport, dport, ulen = struct.unpack_from("!HHH", frame, u)
    p = frame[u + 8:u + max(ulen, 8)]
    base = {"mac": src, "ip": None if sip == "0.0.0.0" else sip}
    if dport == 67 and sport == 68 and p[:1] == b"\x01":                  # a device asking for an address
        opts = dhcp_options(p)
        if opts is None:
            return []
        client = fmt_mac(p[28:34])
        req = opts.get(50, b"")
        ip = socket.inet_ntoa(req) if len(req) == 4 else (socket.inet_ntoa(p[12:16]) if p[12:16] != b"\0\0\0\0" else None)
        return [{"kind": "dhcp", "mac": client, "frame_mac": src, "ip": ip,
                 "type": opts[53][0] if opts.get(53) else 0, "hostname": text(opts.get(12, b""), 64),
                 "vendor": text(opts.get(60, b""), 120), "prl": ",".join(str(b) for b in opts.get(55, b"")[:64]),
                 "fqdn": dhcp_fqdn(opts.get(81, b""))}]
    if sport == 67 and dport == 68 and p[:1] == b"\x02":                  # a DHCP server answering
        opts = dhcp_options(p)
        if opts is None:
            return []
        sid = opts.get(54, b"")
        return [{"kind": "dhcp-server", **base, "server": socket.inet_ntoa(sid) if len(sid) == 4 else sip,
                 "type": opts[53][0] if opts.get(53) else 0}]
    if 5353 in (sport, dport):
        if len(p) < 12 or not p[2] & 0x80:                                 # queries say nothing about the asker
            return []
        _, records = parse_dns(p)
        return [{"kind": "mdns", **base, "records": records}] if records else []
    if sport in (1900, 2021) or dport in (1900, 2021):
        lines = p[:6000].decode("utf-8", "replace").split("\r\n")
        first = lines[0].upper()
        headers = {}
        for line in lines[1:40]:
            key, _, value = line.partition(":")
            if value and key.strip():
                headers.setdefault(clean(key, 40).lower(), clean(value, 200))
        if first.startswith("M-SEARCH"):
            return [{"kind": "ssdp-search", **base, "agent": headers.get("user-agent", "")}]
        if not (first.startswith("NOTIFY") or first.startswith("HTTP/1.1 200")):
            return []
        return [{"kind": "ssdp", **base, "headers": headers}]
    if dport == 137 and len(p) >= 46 and not p[2] & 0x80 and p[12] == 32:  # a NetBIOS name registration
        opcode = (p[2] >> 3) & 15
        name, suffix = nb_decode(p[13:45])
        group = len(p) >= 64 and struct.unpack_from("!H", p, 62)[0] & 0x8000
        if opcode in (5, 8, 9) and suffix in (0x00, 0x20) and name and not group:
            return [{"kind": "netbios", **base, "name": name}]
        return []
    if dport == 138 and len(p) >= 48 and p[14] == 32:                     # a NetBIOS datagram (browser announcements)
        name, suffix = nb_decode(p[15:47])
        out = {"kind": "netbios", **base, "name": name if suffix in (0x00, 0x20) else ""}
        k = p.find(b"\\MAILSLOT\\BROWSE\x00")
        if k > 0 and len(p) >= k + 17 + 24 and p[k + 17] in (1, 15):
            b = p[k + 17:]
            out["os"] = f"{b[22]}.{b[23]}"
            out["name"] = out["name"] or clean(b[6:22].split(b"\x00")[0].decode("ascii", "replace"), 15)
            out["comment"] = clean(b[32:96].split(b"\x00")[0].decode("ascii", "replace"), 60)
        return [out] if out["name"] else []
    if dport == 5355:
        return [{"kind": "llmnr", **base}]
    return []


# --- the device list: where observations land -------------------------------------------------------------

def device_key(state, mac, ip):
    """A device is its hardware address. Behind a Wi-Fi extender that swaps every client's address for
    its own (MAC translation), one address carries many devices: those are told apart by IP."""
    if mac in state.get("shared_macs", {}):
        return f"{mac}@{ip}" if ip else None
    return mac


def ensure(state, mac, ip, now):
    """The device record for (mac, ip), created when new. None when it cannot be placed."""
    if not usable_mac(mac) or mac == state.get("my_mac"):
        return None
    key = device_key(state, mac, ip)
    if key is None:
        return None
    devices = state.setdefault("devices", {})
    d = devices.get(key)
    if d is None:
        if len(devices) >= MAX_DEVICES:
            state["flood"] = now
            return None
        parent = (devices.get(mac) if key != mac else None) or roamed(state, mac, ip, now)
        d = devices[key] = {"key": key, "mac": mac, "ip": ip, "ips": [], "first_seen": now, "last_seen": now, "obs": {}}
        if key != mac:
            d["via"] = mac
        if parent:                    # the same device, now seen through (or no longer through) an extender
            d.update(first_seen=parent.get("first_seen", now), new_at=parent.get("new_at"),
                     baseline=parent.get("baseline") or parent.get("trusted"))
        elif now < state.get("baseline_until", 0):
            d["baseline"] = True
        else:
            d["new_at"] = now
            event(state, now, "warn", f"new device joined: {mac}" + (f" at {ip}" if ip else ""))
    if ip:
        d["ip"] = ip
        if ip not in d["ips"]:
            d["ips"] = (d["ips"] + [ip])[-8:]
    d["last_seen"] = now
    return d


def roamed(state, mac, ip, now):
    """When the Mac (or a device) moves between the router and a MAC-translating extender, a known
    device reappears under the extender's address, or back under its own: same IP, a few hours apart.
    Only when an extender is involved, so a stranger cannot inherit a household device's standing."""
    shared = state.get("shared_macs", {})
    if not ip:
        return None
    for d in state.get("devices", {}).values():
        if (d.get("ip") == ip and d.get("mac") != mac and now - d.get("last_seen", 0) < 6 * 3600
                and (mac in shared or d.get("via") or d.get("mac") in shared)):
            return d
    return None


def event(state, now, level, text):
    state["events"] = (state.get("events", []) + [{"at": now, "level": level, "text": clean(text, 160)}])[-100:]


def mark_shared(state, mac, ips, now):
    """One hardware address answering from several live IPs: an extender's MAC translation. Its old
    single record is split into one per IP (keeping when it was first seen, and whether it is new)."""
    if not usable_mac(mac) or mac == state.get("my_mac"):
        return
    shared = state.setdefault("shared_macs", {})
    first = mac not in shared
    entry = shared.setdefault(mac, {"since": now, "ips": []})
    entry["ips"] = sorted(set(entry["ips"]) | set(ips), key=ip_order)[-16:]
    entry["last"] = now
    if first:
        old = state.get("devices", {}).pop(mac, None)
        if old:
            for ip in ips:
                d = ensure(state, mac, ip, now)
                if d:
                    d.update(first_seen=old.get("first_seen", now), baseline=old.get("baseline"), new_at=old.get("new_at"))
                    if ip == old.get("ip"):
                        d["obs"] = old.get("obs", {})


def note_ip(state, recent, mac, ip, now):
    """Capture-side MAC translation check: the same address sending as A, then B, then A again within 15
    minutes (a phone that changes address goes A then B, and stays)."""
    if not ip or mac in state.get("shared_macs", {}):
        return
    seq = [x for x in recent.get(mac, []) if now - x[1] < 900]
    if not seq or seq[-1][0] != ip:
        seq.append((ip, now))
    recent[mac] = seq[-6:]
    ips = [x[0] for x in seq]
    for i in range(len(ips) - 2):
        if ips[i] == ips[i + 2] and ips[i] != ips[i + 1]:
            mark_shared(state, mac, [ips[i], ips[i + 1]], now)
            return


def ip_owner(state, ip):
    """The device that holds `ip` (None when no record has it)."""
    for d in state.get("devices", {}).values():
        if d.get("ip") == ip:
            return d
    return None


def obs(d, source):
    return d.setdefault("obs", {}).setdefault(source, {})


def add_list(target, key, values, limit=16):
    target[key] = list(dict.fromkeys(list(target.get(key, [])) + [v for v in values if v]))[-limit:]


def observe(state, ob, now, recent=None):
    """Fold one observation into the device list (and the network-wide security checks)."""
    kind = ob.get("kind")
    gateway = state.get("gateway")
    if kind == "arp":
        if ob.get("ip") and ob["ip"] == gateway:
            note_gateway(state, ob["mac"], now)
        if recent is not None:
            note_ip(state, recent, ob["mac"], ob.get("ip"), now)
        ensure(state, ob["mac"], ob.get("ip"), now)
        return
    if kind == "dhcp-server":
        note_dhcp_server(state, ob, now)
        return
    if kind == "dhcp":
        mac = ob["mac"]
        if ob.get("frame_mac") and ob["frame_mac"] != mac:           # relayed: by a translating extender?
            if ob["frame_mac"] not in state.get("shared_macs", {}):
                return
            mac = ob["frame_mac"]
        d = ensure(state, mac, ob.get("ip"), now)
        if d:
            o = obs(d, "dhcp")
            for key in ("hostname", "vendor", "prl", "fqdn"):
                if ob.get(key):
                    o[key] = ob[key]
            o["at"] = now
        return
    if recent is not None and kind != "dhcp":
        note_ip(state, recent, ob["mac"], ob.get("ip"), now)
    d = ensure(state, ob["mac"], ob.get("ip"), now)
    if d is None:
        return
    if kind == "mdns":
        observe_mdns(state, d, ob, now)
    elif kind == "ssdp":
        h = ob.get("headers", {})
        o = obs(d, "ssdp")
        for key in ("server", "usn", "devname.bambu.com", "devmodel.bambu.com", "devversion.bambu.com",
                    "devconnect.bambu.com", "devbind.bambu.com", "x-user-agent"):
            if h.get(key):
                o[key] = h[key]
        add_list(o, "types", [h.get("nt", ""), h.get("st", "")], 12)
        loc = h.get("location", "")
        if loc.startswith("http") and upnp_allowed(loc, d.get("ip")):
            o["location"] = loc
        o["at"] = now
    elif kind == "ssdp-search":
        o = obs(d, "ssdp")
        o["searches"] = now
        if ob.get("agent"):
            o["agent"] = ob["agent"]
    elif kind == "netbios":
        o = obs(d, "netbios")
        if ob.get("name"):
            o["name"] = ob["name"]
        if ob.get("os"):
            o["os"], o["comment"] = ob["os"], ob.get("comment", "")
    elif kind == "llmnr":
        obs(d, "llmnr")["at"] = now


def observe_mdns(state, d, ob, now):
    """mDNS answers name their owner by address: a record goes to the device whose A record it is (a
    Bonjour sleep proxy answers for sleeping Macs), else to the sender."""
    records = ob.get("records") or []
    host_ip = {n.lower(): v for n, t, v in records if t == A and v}
    target = {n: v for n, t, v in records if t == SRV and v}

    def owner(instance=None, host=None):
        ip = host_ip.get((host or target.get(instance, "")).lower())
        if not ip or ip == d.get("ip"):
            return d
        return (ip_owner(state, ip) or d) if ipaddress.ip_address(ip).is_private else d

    for name, rtype, value in records:
        if rtype == A and value:
            dev = owner(host=name)
            if dev:
                add_list(obs(dev, "mdns"), "hosts", [clean(name.removesuffix(".local"), 63)], 6)
        elif rtype == PTR and value and name.endswith(".in-addr.arpa"):
            add_list(obs(d, "mdns"), "hosts", [clean(value.removesuffix(".").removesuffix(".local"), 63)], 6)
        elif rtype == PTR and value and name.startswith("_services._dns-sd"):
            add_list(obs(d, "mdns"), "services", [clean(value.removesuffix(".local").removesuffix("."), 48)], 32)
        elif rtype == PTR and value and name.startswith("_") and "._sub." not in name:
            service = clean(name.removesuffix(".").removesuffix(".local"), 48)
            instance = value[:-(len(name) + 1)] if value.endswith("." + name) else value.split("._")[0]
            dev = owner(instance=value)
            if dev:
                o = obs(dev, "mdns")
                add_list(o, "services", [service], 32)
                inst = o.setdefault("instances", {})
                inst[service] = list(dict.fromkeys(inst.get(service, []) + [clean(instance.replace("\\032", " "))]))[-4:]
        elif rtype == TXT and isinstance(value, dict):
            dev = owner(instance=name)
            if dev:
                txt = obs(dev, "mdns").setdefault("txt", {})
                for key in ("model", "md", "fn", "ty", "am", "rpmd", "osxvers", "manufacturer", "product", "usb_mfg",
                            "usb_mdl", "srcvers", "mf", "mn", "ve", "rs", "vs", "ov", "os", "nm", "ca", "cn"):
                    if value.get(key):
                        txt[key] = clean(value[key], 80)
    o = obs(d, "mdns")
    o["at"] = now


def merge_unicast(state, d, records, now):
    """Answers to the unicast questions use the same records as announcements."""
    if records:
        observe_mdns(state, d, {"mac": d["mac"], "ip": d.get("ip"), "records": records}, now)


def note_gateway(state, mac, now):
    """The router's hardware address. Another one answering for the router's IP is how ARP spoofing
    (a man in the middle) starts: flag it. A change that holds for a day is taken as a new router."""
    if not usable_mac(mac):
        return
    known = state.get("gateway_mac")
    if not known:
        state["gateway_mac"] = mac
        return
    if mac == known:
        state["gateway_seen"] = now
        conflict = state.get("gateway_conflict")
        if conflict:
            conflict["original_seen"] = now
        return
    conflict = state.get("gateway_conflict")
    if not conflict or conflict.get("mac") != mac:
        state["gateway_conflict"] = {"mac": mac, "first": now, "last": now, "was": known}
        event(state, now, "bad", f"{mac} answered as the router (the router is {known})")
    else:
        conflict["last"] = now
        quiet_since = conflict.get("original_seen", conflict["first"])
        if now - conflict["first"] > 86400 and quiet_since <= conflict["first"]:
            event(state, now, "info", f"the router's hardware address is now {mac} (was {known} for a day)")
            state["gateway_mac"] = mac
            state.pop("gateway_conflict", None)


def note_dhcp_server(state, ob, now):
    """Only the router should hand out addresses. Another DHCP server can send the family's traffic
    anywhere it likes (a rogue DHCP server)."""
    server, gateway = ob.get("server"), state.get("gateway")
    if not gateway or server == gateway or ob["mac"] == state.get("gateway_mac"):
        return
    rogue = state.setdefault("dhcp_rogue", {})
    if server not in rogue:
        event(state, now, "bad", f"a second DHCP server answered: {server} ({ob['mac']})")
    entry = rogue.setdefault(server, {"mac": ob["mac"], "first": now})
    entry["last"] = now


# --- what each device is: every source, ranked -------------------------------------------------------

APPLE_NAMES = {
    "iPhone10,1": "iPhone 8", "iPhone10,4": "iPhone 8", "iPhone10,2": "iPhone 8 Plus", "iPhone10,5": "iPhone 8 Plus",
    "iPhone10,3": "iPhone X", "iPhone10,6": "iPhone X", "iPhone11,2": "iPhone XS", "iPhone11,4": "iPhone XS Max",
    "iPhone11,6": "iPhone XS Max", "iPhone11,8": "iPhone XR", "iPhone12,1": "iPhone 11", "iPhone12,3": "iPhone 11 Pro",
    "iPhone12,5": "iPhone 11 Pro Max", "iPhone12,8": "iPhone SE (2nd generation)", "iPhone13,1": "iPhone 12 mini",
    "iPhone13,2": "iPhone 12", "iPhone13,3": "iPhone 12 Pro", "iPhone13,4": "iPhone 12 Pro Max", "iPhone14,4": "iPhone 13 mini",
    "iPhone14,5": "iPhone 13", "iPhone14,2": "iPhone 13 Pro", "iPhone14,3": "iPhone 13 Pro Max",
    "iPhone14,6": "iPhone SE (3rd generation)", "iPhone14,7": "iPhone 14", "iPhone14,8": "iPhone 14 Plus",
    "iPhone15,2": "iPhone 14 Pro", "iPhone15,3": "iPhone 14 Pro Max", "iPhone15,4": "iPhone 15", "iPhone15,5": "iPhone 15 Plus",
    "iPhone16,1": "iPhone 15 Pro", "iPhone16,2": "iPhone 15 Pro Max", "iPhone17,1": "iPhone 16 Pro",
    "iPhone17,2": "iPhone 16 Pro Max", "iPhone17,3": "iPhone 16", "iPhone17,4": "iPhone 16 Plus", "iPhone17,5": "iPhone 16e",
    "MacBookAir8,1": "MacBook Air (Retina, 13-inch, 2018)", "MacBookAir8,2": "MacBook Air (Retina, 13-inch, 2019)",
    "MacBookAir9,1": "MacBook Air (Retina, 13-inch, 2020)", "MacBookAir10,1": "MacBook Air (M1, 2020)",
    "Mac14,2": "MacBook Air (M2, 2022)", "Mac14,15": "MacBook Air (15-inch, M2, 2023)",
    "Mac15,12": "MacBook Air (13-inch, M3, 2024)", "Mac15,13": "MacBook Air (15-inch, M3, 2024)",
    "Mac16,12": "MacBook Air (13-inch, M4, 2025)", "Mac16,13": "MacBook Air (15-inch, M4, 2025)",
    "MacBookPro17,1": "MacBook Pro (13-inch, M1, 2020)", "MacBookPro18,1": "MacBook Pro (16-inch, 2021)",
    "MacBookPro18,2": "MacBook Pro (16-inch, 2021)", "MacBookPro18,3": "MacBook Pro (14-inch, 2021)",
    "MacBookPro18,4": "MacBook Pro (14-inch, 2021)", "Mac14,7": "MacBook Pro (13-inch, M2, 2022)",
    "Mac14,5": "MacBook Pro (14-inch, 2023)", "Mac14,9": "MacBook Pro (14-inch, 2023)",
    "Mac14,6": "MacBook Pro (16-inch, 2023)", "Mac14,10": "MacBook Pro (16-inch, 2023)",
    "Mac15,3": "MacBook Pro (14-inch, M3, Nov 2023)", "Mac15,6": "MacBook Pro (14-inch, M3 Pro or M3 Max, Nov 2023)",
    "Mac15,8": "MacBook Pro (14-inch, M3 Pro or M3 Max, Nov 2023)", "Mac15,10": "MacBook Pro (14-inch, M3 Pro or M3 Max, Nov 2023)",
    "Mac15,7": "MacBook Pro (16-inch, Nov 2023)", "Mac15,9": "MacBook Pro (16-inch, Nov 2023)",
    "Mac15,11": "MacBook Pro (16-inch, Nov 2023)", "Mac16,1": "MacBook Pro (14-inch, M4, 2024)",
    "Mac16,6": "MacBook Pro (14-inch, M4 Pro or M4 Max, 2024)", "Mac16,8": "MacBook Pro (14-inch, M4 Pro or M4 Max, 2024)",
    "Mac16,5": "MacBook Pro (16-inch, 2024)", "Mac16,7": "MacBook Pro (16-inch, 2024)",
    "Macmini9,1": "Mac mini (M1, 2020)", "Mac14,3": "Mac mini (M2, 2023)", "Mac14,12": "Mac mini (M2 Pro, 2023)",
    "Mac16,10": "Mac mini (M4, 2024)", "Mac16,11": "Mac mini (M4 Pro, 2024)", "iMac21,1": "iMac (24-inch, M1, 2021)",
    "iMac21,2": "iMac (24-inch, M1, 2021)", "Mac15,4": "iMac (24-inch, M3, 2023)", "Mac15,5": "iMac (24-inch, M3, 2023)",
    "Mac16,2": "iMac (24-inch, M4, 2024)", "Mac16,3": "iMac (24-inch, M4, 2024)", "Mac13,1": "Mac Studio (2022)",
    "Mac13,2": "Mac Studio (2022)", "Mac14,13": "Mac Studio (2023)", "Mac14,14": "Mac Studio (2023)", "Mac14,8": "Mac Pro (2023)",
    "AppleTV5,3": "Apple TV HD", "AppleTV6,2": "Apple TV 4K", "AppleTV11,1": "Apple TV 4K (2nd generation)",
    "AppleTV14,1": "Apple TV 4K (3rd generation)", "AudioAccessory1,1": "HomePod", "AudioAccessory5,1": "HomePod mini",
    "AudioAccessory6,1": "HomePod (2nd generation)"}
APPLE_MODEL = re.compile(r"(iPhone|iPad|iPod|Watch|AppleTV|AudioAccessory|MacBookAir|MacBookPro|MacBook|iMac\w*|Macmini|MacPro|Mac)\d+,\d+$")
APPLE_FAMILY = {"iPhone": ("phone", "iOS"), "iPad": ("tablet", "iPadOS"), "iPod": ("music player", "iOS"),
                "Watch": ("watch", "watchOS"), "AppleTV": ("TV streamer", "tvOS"), "AudioAccessory": ("smart speaker", "HomePod software")}
BAMBU_MODELS = {"BL-P001": "X1 Carbon", "BL-P002": "X1", "C11": "P1P", "C12": "P1S", "C13": "X1E", "N1": "A1 mini",
                "N2S": "A1", "O1D": "H2D"}
DARWIN_MACOS = {19: "macOS 10.15 Catalina", 20: "macOS 11 Big Sur", 21: "macOS 12 Monterey", 22: "macOS 13 Ventura",
                23: "macOS 14 Sonoma", 24: "macOS 15 Sequoia", 25: "macOS 26 Tahoe"}
GENERIC_HOSTS = {"iphone", "ipad", "mac", "macbook", "macbook-air", "macbook-pro", "android", "localhost", "linux", "none",
                 "unknown", "espressif", "(null)", "host", "device", "client", "galaxy", "samsung", "esp", "wlan0",
                 "localnetworkprivacy", "spotifyconnect", "airplay", "chromecast", "printer", "nas", "speaker", "tv"}
NAME_SERVICES = ("_companion-link._tcp", "_device-info._tcp", "_airplay._tcp", "_googlecast._tcp", "_raop._tcp", "_hap._tcp",
                 "_sonos._tcp", "_ipp._tcp", "_ipps._tcp", "_printer._tcp", "_smb._tcp", "_workstation._tcp", "_rfb._tcp",
                 "_ssh._tcp", "_adisk._tcp", "_amzn-wplay._tcp", "_androidtvremote2._tcp", "_viziocast._tcp")
# a maker that names a product line; a weak one only says "a chip, a brand"
MAKER_KINDS = (("roku", "TV streamer", "Roku OS", 60), ("ring llc", "Ring camera or doorbell", "", 65),
               ("amazon", "Amazon device (Echo, Fire TV or Kindle)", "Fire OS", 50), ("sonos", "Sonos speaker", "Sonos", 70),
               ("raspberry", "Raspberry Pi", "Linux", 65), ("espressif", "smart-home device (ESP32/ESP8266 chip)", "", 45),
               ("tuya", "smart-home device (Tuya)", "", 55), ("nintendo", "Nintendo console", "", 65),
               ("sony interactive", "PlayStation", "PlayStation", 70), ("hewlett packard", "printer", "", 45),
               ("canon", "printer or camera", "", 45), ("seiko epson", "printer", "", 60), ("brother", "printer", "", 55),
               ("ecobee", "thermostat", "", 70), ("nest", "Google Nest device", "", 60), ("aura home", "Aura photo frame", "", 70),
               ("wyze", "camera", "", 60), ("arlo", "camera", "", 60), ("philips lighting", "Hue bridge", "", 65),
               ("signify", "Hue bridge", "", 60), ("vizio", "Vizio TV", "SmartCast", 65), ("tp-link", "network device", "", 35),
               ("netgear", "network device", "", 35), ("ubiquiti", "network device", "", 40), ("eero", "Wi-Fi point", "", 60),
               ("ugreen", "NAS or network device", "", 45), ("synology", "NAS", "", 60), ("qnap", "NAS", "", 60),
               ("apple", "Apple device", "", 40), ("google", "Google device", "", 40), ("samsung", "Samsung device", "", 35),
               ("lg electronics", "LG device", "", 35), ("microsoft", "Microsoft device", "", 40),
               ("intel", "computer", "", 30), ("liteon", "computer", "", 30), ("azurewave", "computer or console", "", 25),
               ("realtek", "computer", "", 25), ("dell", "computer", "", 35), ("lenovo", "computer", "", 35),
               ("nokia", "network device", "", 35))
NAME_HINTS = ((r"^iphone|iphone$|s-iphone", "phone", "iOS", "iPhone"), (r"^ipad|ipad$|s-ipad", "tablet", "iPadOS", "iPad"),
              (r"macbook", "laptop", "macOS", "MacBook"), (r"^imac", "computer", "macOS", "iMac"),
              (r"apple-?tv", "TV streamer", "tvOS", "Apple TV"), (r"homepod", "smart speaker", "HomePod software", "HomePod"),
              (r"^android|galaxy|^pixel|oneplus|^sm-", "phone or tablet", "Android", ""),
              (r"^(desktop|laptop)-[a-z0-9]{7}$", "Windows PC", "Windows", ""), (r"raspberrypi", "Raspberry Pi", "Linux", "Raspberry Pi"),
              (r"chromecast", "Chromecast", "Google TV", "Chromecast"), (r"^roku", "TV streamer", "Roku OS", "Roku"),
              (r"^amazon-|echo|^kindle", "Amazon device", "Fire OS", ""), (r"fire-?tv", "Fire TV", "Fire OS", "Fire TV"),
              (r"^ps5-", "game console", "PlayStation", "PlayStation 5"), (r"^ps4-", "game console", "PlayStation", "PlayStation 4"),
              (r"xbox", "game console", "Xbox", "Xbox"), (r"switch|nintendo", "game console", "", "Nintendo Switch"),
              (r"^(hp|brw|epson|canon)[0-9a-f]", "printer", "", ""), (r"lgwebostv|webos", "TV", "webOS", "LG TV"),
              (r"^ring|ring-|stickup|doorbell", "Ring camera or doorbell", "", ""), (r"^dh\d{4}", "NAS", "Linux", "UGREEN NAS"))
SERVICE_KINDS = (("_googlecast._tcp", "Google Cast device", "Google Cast", 80), ("_androidtvremote2._tcp", "Android TV", "Android TV", 85),
                 ("_amzn-wplay._tcp", "Fire TV", "Fire OS", 85), ("_sonos._tcp", "Sonos speaker", "Sonos", 90),
                 ("_ipp._tcp", "printer", "", 85), ("_ipps._tcp", "printer", "", 85), ("_printer._tcp", "printer", "", 85),
                 ("_pdl-datastream._tcp", "printer", "", 80), ("_hue._tcp", "Hue bridge", "", 85),
                 ("_hap._tcp", "HomeKit accessory", "", 60), ("_matter._tcp", "smart-home device (Matter)", "", 45),
                 ("_viziocast._tcp", "Vizio TV", "SmartCast", 85), ("_companion-link._tcp", "Apple device", "", 70),
                 ("_airplay._tcp", "AirPlay receiver", "", 45), ("_raop._tcp", "AirPlay speaker", "", 40),
                 ("_spotify-connect._tcp", "speaker or media player", "", 30), ("_adisk._tcp", "NAS or Time Machine disk", "", 55),
                 ("_smb._tcp", "computer or NAS", "", 35), ("_workstation._tcp", "computer", "", 35))
PORT_KINDS = {62078: ("iPhone or iPad", "iOS or iPadOS", 85, "TCP 62078 open: Apple's iPhone/iPad sync service"),
              8060: ("TV streamer", "Roku OS", 85, "TCP 8060 open: Roku's remote-control service"),
              1400: ("Sonos speaker", "Sonos", 80, "TCP 1400 open: Sonos control"),
              8009: ("Google Cast device", "Google Cast", 75, "TCP 8009 open: Google Cast"),
              3389: ("Windows PC", "Windows", 70, "TCP 3389 open: Remote Desktop"),
              5555: ("Android device", "Android", 70, "TCP 5555 open: Android debugging"),
              32400: ("media server", "", 50, "TCP 32400 open: Plex"),
              55443: ("Amazon device", "", 40, "TCP 55443 open (TLS): as Amazon Echo devices have")}
RISK_PORTS = {23: ("bad", "Telnet is open: an unencrypted remote login anyone on the network can try"),
              5555: ("bad", "Android debugging (adb) is open: anyone on the network could take control of it"),
              21: ("warn", "FTP is open: passwords and files cross the network unencrypted"),
              3389: ("warn", "Remote Desktop is open to the network"), 5900: ("warn", "VNC screen sharing is open to the network"),
              1883: ("warn", "MQTT without encryption is open"), 445: ("info", "Windows file sharing (SMB) is open"),
              139: ("info", "old NetBIOS file sharing is open"), 22: ("info", "SSH remote login is open"),
              554: ("info", "a camera or video stream (RTSP) is open"), 8554: ("info", "a video stream (RTSP) is open")}


def real_name(name):
    """A name a person gave or would recognise: not empty, not generic, not a serial number or an ID."""
    name = clean(name)
    if not name or name.lower() in GENERIC_HOSTS:
        return ""
    if re.fullmatch(r"[0-9a-fA-F]{8}(-[0-9a-fA-F]{4}){3}-[0-9a-fA-F]{12}|[0-9A-Fa-f:]{12,}|[A-Z0-9]{10,}|[0-9a-f]{16,}", name):
        return ""
    core = re.sub(r"[-_.:# ]", "", name)
    if len(core) >= 10 and re.fullmatch(r"[0-9A-Fa-f]+", core):     # a hex ID with dashes (Matter, serials)
        return ""
    return name


def instance_name(instance, service):
    """ "HS-DJH2LMVT4M [7c:3b:2d:8f:ec:29]" -> the name; "A1B2C3D4E5F6@Kitchen" -> "Kitchen"."""
    name = re.sub(r"\s*\[[0-9a-fA-F:]{17}\]$", "", instance)
    if service == "_raop._tcp" and "@" in name:
        name = name.split("@", 1)[1]
    return name


def apple_model(code):
    """("MacBook Air (M2, 2022)", "laptop", "macOS") for "Mac14,2"; None when it is not an Apple model code."""
    code = clean(code, 40).replace(" ", "")
    m = APPLE_MODEL.match(code)
    if not m:
        return None
    family = m.group(1)
    name = APPLE_NAMES.get(code, "")
    if family in APPLE_FAMILY:
        kind, system = APPLE_FAMILY[family]
        name = name or {"iPhone": "iPhone", "iPad": "iPad", "iPod": "iPod touch", "Watch": "Apple Watch",
                        "AppleTV": "Apple TV", "AudioAccessory": "HomePod"}[family]
    else:
        laptop = "MacBook" in family or "MacBook" in name
        kind, system = ("laptop" if laptop else "Mac"), "macOS"
        name = name or "Mac"
    return f"{name} · {code}", kind, system


def os_from_darwin(osxvers):
    try:
        n = int(str(osxvers).split(".")[0])
    except ValueError:
        return ""
    return DARWIN_MACOS.get(n, f"macOS {n + 1}" if n > 25 else "")


def system_from_dhcp_vendor(vendor):
    """(system, model, why) from DHCP option 60, the client's own name for its software."""
    v = vendor or ""
    m = re.match(r"android-dhcp-(\S+)", v)
    if m:
        return f"Android {m.group(1)}", "", f"DHCP: the client says android-dhcp-{m.group(1)}"
    if v.startswith("MSFT"):
        return "Windows", "", f"DHCP: the client says {v} (Windows)"
    m = re.match(r"dhcpcd-[\d.]+:(\w+)-([\w.+-]+):[^:]*:?(.*)", v)
    if m:
        return f"{m.group(1)} {m.group(2).split('-')[0]}", clean(m.group(3)), f"DHCP: the client says {v}"
    m = re.match(r"udhcp ([\d.]+)", v)
    if m:
        return "embedded Linux (BusyBox)", "", f"DHCP: the client says {v}"
    return "", "", f"DHCP: the client says {v}" if v else ""


def identify(d, known=None, now=None):
    """Fold every observation into one answer: name, every name it gave, kind, system, model, how sure,
    the evidence (strongest first) and what it exposes. Each claim carries a strength; the strongest
    claim for each field wins, so a device's own statement beats a guess from its maker or its TTL."""
    now = time.time() if now is None else now
    o, known = d.get("obs") or {}, known or {}
    cand = {"kind": [], "system": [], "model": []}
    why = []

    def claim(kind=None, system=None, model=None, strength=0, text=None):
        for field, value in (("kind", kind), ("system", system), ("model", model)):
            if value:
                cand[field].append((strength, value))
        if text:
            why.append((strength, clean(text, 160)))

    names = []

    def name(value, source):
        value = real_name(value)
        if value and value.lower() not in (n["name"].lower() for n in names):
            names.append({"name": value, "source": source})

    md, ss, up, dh = o.get("mdns") or {}, o.get("ssdp") or {}, o.get("upnp") or {}, o.get("dhcp") or {}
    nb, nm = o.get("netbios") or {}, o.get("nmap") or {}
    txt, inst, services = md.get("txt") or {}, md.get("instances") or {}, md.get("services") or []
    ports = sorted(set((o.get("ports") or {}).get("open", [])) | {p["port"] for p in nm.get("ports", [])})

    # names: yours first, then what the device calls itself, most deliberate first
    name(known.get("name"), "you")
    name(up.get("friendlyName"), "UPnP description")
    name(ss.get("devname.bambu.com"), "Bambu Lab announcement")
    for service in NAME_SERVICES:
        for instance in inst.get(service, []):
            name(instance_name(instance, service), f"Bonjour {service.split('.')[0].lstrip('_')}")
    name(txt.get("fn"), "Google Cast")
    for host in md.get("hosts", []):
        name(host, "Bonjour host name")
    generic = (dh.get("hostname") or "").lower() in GENERIC_HOSTS
    if not generic:
        name(dh.get("hostname"), "DHCP host name")
    name(dh.get("fqdn"), "DHCP")
    name(nb.get("name"), "NetBIOS")
    name(o.get("router_dns"), "router's DNS")
    for value in nm.get("names", []):
        name(value, "nmap")
    if generic and dh.get("hostname"):
        names.append({"name": clean(dh["hostname"]), "source": "DHCP host name (generic: a private Wi-Fi address)"})

    if d.get("router"):
        claim(kind="router", strength=100, text="it is the network's gateway (the default route)")
    # the device's own statements
    if up:
        model = " ".join(dict.fromkeys(x for x in (up.get("manufacturer"), up.get("modelName")) if x))
        if up.get("modelNumber") and up.get("modelNumber") not in model:
            model += f" ({up['modelNumber']})"
        dtype = (up.get("deviceType") or "").lower()
        kind = ("router" if "internetgateway" in dtype else "printer" if "printer" in dtype else
                "TV streamer" if "roku" in dtype else "media player" if "mediarenderer" in dtype or "player" in dtype else "")
        claim(kind=kind, model=model.strip(), strength=90,
              text=f"UPnP description: {up.get('friendlyName', '?')}, {model.strip() or '?'}"
                   + (f" ({up['modelDescription']})" if up.get("modelDescription") else ""))
    if ss.get("devmodel.bambu.com") or any("bambulab" in t for t in ss.get("types", [])):
        code = ss.get("devmodel.bambu.com", "")
        claim(kind="3D printer", model=f"Bambu Lab {BAMBU_MODELS.get(code, code or '3D printer')}".strip(),
              system=f"Bambu firmware {ss['devversion.bambu.com']}" if ss.get("devversion.bambu.com") else "",
              strength=95 if code else 85, text="UPnP notice from a Bambu Lab printer" + (f" (model code {code})" if code else ""))
    server = ss.get("server", "")
    m = re.search(r"Roku/([\d.]+)", server)
    if m:
        claim(kind="TV streamer", system=f"Roku OS {m.group(1)}", strength=90, text=f"UPnP server header: {server}")
    elif "Windows" in server:
        claim(system="Windows", strength=70, text=f"UPnP server header: {server}")
    elif server:
        lm = re.search(r"Linux/([\d.]+)", server)
        claim(system=f"Linux {lm.group(1)}" if lm else "", strength=40, text=f"UPnP server header: {server}")
    apple = apple_model(txt.get("model") or txt.get("rpmd") or txt.get("am") or "")
    if apple:
        model, kind, system = apple
        claim(kind=kind, model=model, system=os_from_darwin(txt["osxvers"]) if txt.get("osxvers") else system,
              strength=95, text=f"Bonjour: Apple model {model}" + (f", {os_from_darwin(txt['osxvers'])}" if txt.get("osxvers") else ""))
    elif txt.get("model") and txt.get("manufacturer"):
        claim(model=f"{txt['manufacturer']} {txt['model']}", strength=85, text=f"Bonjour: {txt['manufacturer']} model {txt['model']}")
    elif txt.get("model") and txt["model"].lower() != "macsamba":
        claim(model=txt["model"], strength=70, text=f"Bonjour: model {txt['model']}")
    if txt.get("md"):
        claim(model=txt["md"], strength=88, text=f"Google Cast: model {txt['md']}")
    if txt.get("ty") or txt.get("product") or txt.get("usb_mdl"):
        model = txt.get("ty") or txt.get("product", "").strip("()") or f"{txt.get('usb_mfg', '')} {txt.get('usb_mdl', '')}".strip()
        claim(kind="printer", model=model, strength=90, text=f"Bonjour printer: {model}")
    if "_smb._tcp" in services and ("_nut._tcp" in services or (txt.get("model") or "").lower() == "macsamba"):
        claim(kind="NAS", system="Linux (Samba)", strength=75, text="Bonjour: file sharing through Samba")
    for service, kind, system, strength in SERVICE_KINDS:
        if service in services:
            claim(kind=kind, system=system, strength=strength, text=f"Bonjour: offers {service}")
    # DHCP: what it said when it joined
    if dh:
        system, model, text_ = system_from_dhcp_vendor(dh.get("vendor"))
        claim(system=system, model=model, strength=92 if system else 0, text=text_)
        host = (dh.get("hostname") or "").lower()
        if host in ("iphone", "ipad"):
            claim(kind="phone" if host == "iphone" else "tablet", system="iOS" if host == "iphone" else "iPadOS",
                  model="iPhone" if host == "iphone" else "iPad", strength=88,
                  text=f"DHCP: it calls itself \"{dh['hostname']}\" (what an {dh['hostname']} sends with a private address)")
        elif dh.get("hostname"):
            why.append((50, f"DHCP: host name \"{dh['hostname']}\""))
        prl = set((dh.get("prl") or "").split(","))
        if not dh.get("vendor") and {"1", "121", "3", "6", "15", "119", "252"} <= prl:
            claim(system="iOS, iPadOS or macOS", strength=60, text="DHCP: Apple's request pattern (options 121, 119, 252)")
        if (dh.get("vendor") or "").startswith("android-dhcp") and private_mac(d.get("mac", "")):
            claim(kind="phone or tablet", strength=55)
    agent = ss.get("agent", "")
    if agent:
        system = ("Windows" if "Windows" in agent else "Android" if "Android" in agent else
                  "iOS or macOS" if re.search(r"iOS|iPhone|Darwin|Mac OS", agent) else "Linux" if "Linux" in agent else "")
        kind = ("Windows PC" if system == "Windows" else "phone or tablet" if system == "Android" else
                "computer" if system == "Linux" else "")
        claim(kind=kind, system=system, strength=60, text=f"it searched for UPnP devices as \"{agent}\"")
    if "_matter._tcp" in services and "_spotify-connect._tcp" in services:
        amazon = "amazon" in (d.get("maker") or "").lower()
        claim(kind="smart speaker" if amazon else "smart speaker (likely an Amazon Echo)",
              model="Amazon Echo" if amazon else "", system="Fire OS" if amazon else "", strength=80 if amazon else 60,
              text="Bonjour: a Matter hub with Spotify Connect, as an Amazon Echo is")
    if ss.get("devconnect.bambu.com"):
        why.append((60, f"Bambu printer: {ss['devconnect.bambu.com']} connection"
                        + (f", {ss['devbind.bambu.com']}" if ss.get("devbind.bambu.com") else "")))
    # names that say what they are
    for value in [n["name"] for n in names] + [dh.get("hostname", "")]:
        for pattern, kind, system, model in NAME_HINTS:
            if value and re.search(pattern, value, re.I):
                claim(kind=kind, system=system, model=model, strength=65, text=f"the name \"{value}\" says {kind}")
                break
    for value in [n["name"] for n in names]:
        m = re.match(r"(DXP|DH)(\d{4})(PLUS|PRO)?(?:-|$)", value, re.I)
        if m and "ugreen" in (d.get("maker") or "").lower():
            model = f"UGREEN NASync {m.group(1).upper()}{m.group(2)}{' ' + m.group(3).title() if m.group(3) else ''}"
            claim(kind="NAS", model=model, strength=80, text=f"the name \"{value}\" is a UGREEN model ({model})")
            break
    # ports and services
    for port in ports:
        if port in PORT_KINDS:
            kind, system, strength, text_ = PORT_KINDS[port]
            claim(kind=kind, system=system, strength=strength, text=text_)
    for cert in nm.get("certs", []):
        if "bbl" in (cert.get("issuer") or "").lower():
            claim(kind="3D printer", model="Bambu Lab 3D printer", strength=88,
                  text=f"TLS certificate from Bambu Lab (BBL), printer serial {cert.get('cn') or '?'}")
        elif cert.get("cn") or cert.get("issuer"):
            why.append((40, f"TLS certificate: {cert.get('cn') or '?'}, issued by {cert.get('issuer') or '?'}"))
    if {8883, 990, 6000} <= set(ports):
        claim(kind="3D printer", model="Bambu Lab 3D printer", strength=80, text="ports 8883, 990 and 6000: a Bambu Lab printer's LAN mode")
    if nm.get("os"):
        claim(system=nm["os"], strength=90, text=f"nmap: {nm['os']}")
    for p in nm.get("ports", []):
        if p.get("product"):
            why.append((45, f"nmap: port {p['port']} runs {p['product']}"))
            if re.search(r"Microsoft Windows", p["product"]):
                claim(system="Windows", kind="Windows PC", strength=75)
    for title in nm.get("titles", []):
        why.append((40, f"its web page is titled \"{title}\""))
    # Windows clues
    if nb.get("os"):
        claim(kind="computer or NAS", system="Windows or Samba", strength=45,
              text=f"NetBIOS browser announcement: OS version {nb['os']}" + (f", \"{nb['comment']}\"" if nb.get("comment") else "")
                   + " (Windows and Samba both send these)")
    elif nb.get("name"):
        claim(kind="computer", system="Windows or Samba", strength=50, text=f"NetBIOS name {nb['name']}")
    if (o.get("llmnr") or {}).get("at"):
        claim(system="Windows (or Linux)", strength=40, text="it looks names up with LLMNR (Windows does by default)")
    # the hardware maker
    maker = d.get("maker", "")
    if d.get("real_mac"):
        why.append((85, f"its Bonjour data gives its real hardware address {d['real_mac']}"
                        + (f" ({d['real_maker']})" if d.get("real_maker") else "")))
        maker = d.get("real_maker") or maker
    for key, kind, system, strength in MAKER_KINDS:
        if key in maker.lower():
            claim(kind=kind, system=system, strength=strength, text=f"made by {maker} (IEEE registry)")
            break
    else:
        if maker:
            why.append((30, f"made by {maker} (IEEE registry)"))
    # last resorts
    ttl = d.get("ttl")
    if ttl and 64 < ttl <= 128:
        claim(kind="computer", system="Windows", strength=35, text=f"ping TTL {ttl}: Windows")
    elif ttl and ttl > 128:
        claim(kind="network device", strength=25, text=f"ping TTL {ttl}: network equipment or a small embedded system")
    if d.get("via") and {22, 80} <= set(ports) | {80} and 22 in ports:
        claim(kind="Wi-Fi extender or mesh point", strength=55,
              text=f"shares hardware address {d['via']} with the devices it relays, and has a login page")
    if private_mac(d.get("mac", "")) and not d.get("via"):
        claim(kind="phone, tablet or laptop", system="iOS or Android" if ttl and ttl <= 64 else "", strength=20,
              text="it uses a private (randomised) Wi-Fi address, as phones, tablets and laptops do")
    if d.get("via"):
        why.append((20, f"it reaches the network through an extender that swaps its hardware address for {d['via']}"))

    pick = lambda f: max(cand[f], key=lambda c: c[0])[1] if cand[f] else ""
    top = max((c[0] for c in cand["kind"]), default=0)
    d["names"] = names[:10]
    d["name"] = names[0]["name"] if names and not names[0]["source"].startswith("DHCP host name (generic") else ""
    d["kind"], d["system"], d["model"] = pick("kind") or "device", pick("system"), pick("model")
    d["confidence"] = "exact" if top >= 85 else "likely" if top >= 55 else "guess" if top >= 20 else "unknown"
    seen = set()
    d["evidence"] = [t for _, t in sorted(why, key=lambda w: -w[0]) if t and not (t in seen or seen.add(t))][:14]
    d["ports"] = ports[:40]
    d["services"] = [f"{p['port']} {p['service']}" + (f" ({p['product']})" if p.get("product") else "") for p in nm.get("ports", [])][:24]
    d["trusted"] = bool(known.get("trusted"))
    d["user_name"] = clean(known.get("name"))
    d["risks"] = risks(d, services, ports, now)
    d["label"] = label(d)
    return d


def risks(d, services, ports, now):
    """What deserves a look: what it exposes ("open"), whether it is new, whether it is still unknown."""
    out = []
    for port in ports:
        if port in RISK_PORTS and not (port == 22 and d.get("router")):
            out.append({"level": RISK_PORTS[port][0], "what": "open", "port": port, "text": RISK_PORTS[port][1]})
    if "_rfb._tcp" in services and 5900 not in ports:
        out.append({"level": "info", "what": "open", "text": "it offers Screen Sharing (remote control) on the network"})
    if "_ssh._tcp" in services and 22 not in ports:
        out.append({"level": "info", "what": "open", "text": "it offers Remote Login (SSH) on the network"})
    if d.get("new_at") and not d.get("trusted") and now - d["new_at"] < NEW_S:
        out.append({"level": "warn", "what": "new",
                    "text": f"new on the network since {time.strftime('%a %H:%M', time.localtime(d['new_at']))}, not yet marked as yours"})
    if d.get("confidence") in ("unknown", "guess"):
        out.append({"level": "info", "what": "unknown", "text": "not identified for certain yet"})
    return out[:10]


LEGAL_WORDS = re.compile(r"(inc|llc|ltd|limited|corp|corporate|corporation|co|gmbh|kg|ag|sa|bv|plc|pte|technology|"
                         r"technologies|electronics|international|holdings|group|ind)\.?", re.I)


def short_maker(maker):
    """ "Nokia Solutions and Networks GmbH & Co. KG" -> "Nokia Solutions and Networks", "Intel Corporate" -> "Intel"."""
    words = []
    for w in (maker or "").split(",")[0].replace("&", " ").split():
        if words and LEGAL_WORDS.fullmatch(w):
            break
        words.append(w)
    return " ".join(words[:4])


def label(d):
    """What the lists show: its own name in full; else what it is and who made it."""
    if d.get("name"):
        return d["name"]
    what = d.get("model") or d.get("kind") or "device"
    words = short_maker(d.get("real_maker") or d.get("maker")).split()
    maker = " ".join(words[:1] if words and len(words[0]) >= 5 else words[:2])   # "Nokia", "Hon Hai": the rest is in the detail
    if maker and maker.split()[0].lower() not in what.lower():
        return f"{what} ({maker})"
    return what


# --- security alerts (the screen shows them; notify.py sends them to the phone) ---------------------------

def alerts(state, now):
    out = []
    conflict = state.get("gateway_conflict")
    if conflict and conflict.get("mac") in state.get("shared_macs", {}):
        conflict = None               # the Mac itself now reaches the router through the extender
    if conflict and now - conflict.get("last", 0) < 3600:
        out.append({"level": "bad", "title": "another device is answering as your router",
                    "why": f"{conflict['mac']} claims the router's address {state.get('gateway')} (the router is "
                           f"{conflict.get('was')}). That is how a man-in-the-middle (ARP spoofing) attack starts; it is "
                           "also what a newly replaced router looks like.",
                    "fix": ["macserver devices", f"find {conflict['mac']}: macserver devices {conflict['mac']}",
                            "if you replaced the router: sudo macserver devices accept-router"]})
    for server, r in sorted(state.get("dhcp_rogue", {}).items()):
        if now - r.get("last", 0) < 6 * 3600:
            out.append({"level": "bad", "title": f"a second DHCP server is on your network ({server})",
                        "why": f"{server} ({r.get('mac')}) answered a device that was joining. Only the router should; "
                               "another DHCP server can send your family's traffic wherever it likes.",
                        "fix": [f"macserver devices {server}", "unplug that device, or turn off its DHCP server"]})
    if state.get("flood") and now - state["flood"] < 3600:
        out.append({"level": "bad", "title": "hundreds of hardware addresses appeared (MAC flooding)",
                    "why": f"more than {MAX_DEVICES} devices: someone may be flooding the network to overwhelm it",
                    "fix": ["macserver devices", "restart the router and change the Wi-Fi password"]})
    fresh = [d for d in state.get("devices", {}).values()
             if d.get("new_at") and not d.get("trusted") and now - d["new_at"] < NEW_S and now - d.get("last_seen", 0) < 3600]
    for d in sorted(fresh, key=lambda d: d["new_at"])[:8]:
        where = f"at {d['ip']}" if d.get("ip") else "still joining"
        out.append({"level": "warn", "title": f"new device on your network: {d['key']}",
                    "why": f"{d.get('label') or 'unknown device'}, {d.get('kind')}, {where}, first seen "
                           f"{time.strftime('%H:%M', time.localtime(d['new_at']))}. If you do not know it, it should not be here.",
                    "fix": [f"macserver devices {d['ip'] or d['mac']}   (everything known about it)",
                            f"sudo macserver devices trust {d['ip'] or d['mac']}   (it is yours: stop the alert)",
                            "not yours: change the Wi-Fi password and check the router's list of clients"]})
    for d in state.get("devices", {}).values():
        for r in d.get("risks", []):
            if r["what"] == "open" and r["level"] == "bad" and now - d.get("last_seen", 0) < 3600:
                out.append({"level": "warn", "title": f"{d['key']}: {r['text'].split(':')[0]}",
                            "why": f"{d.get('label')} at {d.get('ip')}: {r['text']}",
                            "fix": [f"macserver devices {d.get('ip') or d['mac']}", "turn that service off on the device, "
                                    "or update its firmware; if it is not yours, remove it from the network"]})
    return out[:16]


# --- one look, and the service loop --------------------------------------------------------------------

def load_known(path=KNOWN):
    data = load_json(path)
    devices = data.get("devices") if isinstance(data.get("devices"), dict) else {}
    return {"devices": devices, "accept_router": data.get("accept_router")}


def known_for(known, d):
    k = known.get("devices", {})
    return k.get(d["key"]) or (k.get(d["mac"]) if d["key"] == d["mac"] else None) or {}


def refresh(state, now, known=None):
    """Identify every device again and recompute the alerts."""
    known = known if known is not None else load_known()
    accept = known.get("accept_router")
    if accept and state.get("gateway_conflict") and accept == state["gateway_conflict"].get("mac"):
        state["gateway_mac"] = accept
        state.pop("gateway_conflict", None)
        event(state, now, "info", f"router's new hardware address accepted: {accept}")
    gateway = state.get("gateway")
    for d in state.get("devices", {}).values():
        d["router"] = bool(gateway) and d.get("ip") == gateway
        identify(d, known_for(known, d), now)
    seen = {}
    for d in state.get("devices", {}).values():
        seen.setdefault(d["label"], []).append(d)
    for same in seen.values():
        if len(same) > 1:
            for d in same:
                if not d.get("name"):
                    d["label"] = f"{d['label']} · {d['mac'][-5:]}" if d["key"] == d["mac"] else f"{d['label']} · {d.get('ip')}"
    state["alerts"] = alerts(state, now)
    devices = list(state.get("devices", {}).values())
    recent = [d for d in devices if now - d.get("last_seen", 0) < 3600]
    state["counts"] = {"devices": len(recent), "known": len(devices),
                       "identified": sum(d.get("confidence") in ("exact", "likely") for d in recent),
                       "yours": sum(bool(d.get("trusted")) for d in recent),
                       "new": sum(bool(d.get("new_at")) and not d.get("trusted") and now - d["new_at"] < NEW_S for d in recent),
                       "exposed": sum(any(r["what"] == "open" and r["level"] in ("bad", "warn") for r in d.get("risks", []))
                                     for d in recent)}
    return state


def pump(state, listener, recent, now=None):
    if listener is None:
        return 0
    n = 0
    for at, frame in listener.take():
        for ob in parse_frame(frame):
            observe(state, ob, now or at, recent)
        n += 1
    return n


def look(state, listener=None, recent=None, now=None, proc=PROC, sweep_fn=sweep, probe_fn=None, ports_fn=open_ports,
         upnp_fn=upnp_describe, nmap_fn=nmap_scan, oui_files=OUI_FILES, own_fn=own_address, multicast=True):
    """One active look: sweep, ask, check, then identify everything."""
    now = time.time() if now is None else now
    recent = {} if recent is None else recent
    route = default_route(read(proc / "net/route"))
    if not route:
        return state
    iface, gateway, net = route
    me = own_fn(gateway)
    state.setdefault("baseline_until", now + BASELINE_S)
    state.update(iface=iface, gateway=gateway, network=str(net), me=me, looked=now,
                 my_mac=read(Path("/sys/class/net") / iface / "address").strip().lower() or state.get("my_mac"))
    addresses = [str(a) for a in net.hosts() if str(a) != me] if net.num_addresses <= MAX_SWEEP else \
        list(neighbours(read(proc / "net/arp"), iface))
    ttls = sweep_fn(addresses)
    pump(state, listener, recent)
    arp = {ip: mac for ip, mac in neighbours(read(proc / "net/arp"), iface).items() if ip != me}
    if gateway in arp:
        note_gateway(state, arp[gateway], now)
    by_mac = {}
    for ip, mac in arp.items():
        if ip in ttls:
            by_mac.setdefault(mac, []).append(ip)
    for mac, ips in by_mac.items():
        if len(ips) >= 2:
            mark_shared(state, mac, ips, now)
    present = {}
    for ip, mac in arp.items():
        d = ensure(state, mac, ip, now)
        if d:
            if ttls.get(ip):
                d["ttl"] = ttls[ip]
            present[d["key"]] = d
    if multicast and listener is not None:
        mdns_multicast(me)
        ssdp_search(me)
    probe_fn = probe_fn or (lambda ip: probe(ip, gateway))
    with ThreadPoolExecutor(max_workers=16) as pool:
        answers = dict(zip(present, pool.map(lambda d: probe_fn(d["ip"]), present.values())))
    for key, ans in answers.items():
        d = present[key]
        merge_unicast(state, d, ans.get("mdns") or [], now)
        if ans.get("netbios"):
            obs(d, "netbios")["name"] = ans["netbios"]
        if ans.get("router_dns"):
            d["obs"]["router_dns"] = ans["router_dns"]
    pump(state, listener, recent)
    # a sleeping phone answers nothing: an empty answer is asked again in half an hour, not in six hours
    due = [d for d in present.values() if is_due(d["obs"].get("ports"), PORTS_EVERY_S if (d["obs"].get("ports") or {}).get("open")
                                                 else 3 * LOOK_S, now)]
    if due:
        found = ports_fn([d["ip"] for d in due])
        for d in due:
            before = (d["obs"].get("ports") or {}).get("open") or []
            d["obs"]["ports"] = {"open": found.get(d["ip"], []) or before, "at": now}   # asleep now: keep what it showed
    for d in present.values():
        loc = (d["obs"].get("ssdp") or {}).get("location")
        if loc and is_due(d["obs"].get("upnp"), UPNP_EVERY_S, now):
            desc = upnp_fn(loc, d["ip"])
            if desc:
                d["obs"]["upnp"] = {**desc, "at": now}
    pump(state, listener, recent)
    if nmap_fn is not None:
        due = sorted((d for d in present.values() if is_due(d["obs"].get("nmap"), DEEP_EVERY_S, now)),
                     key=lambda d: (not d.get("new_at"), (d["obs"].get("nmap") or {}).get("at", 0)))
        budget = time.monotonic() + 300               # a look never spends more than 5 minutes in nmap
        for d in due[:DEEP_PER_LOOK]:
            if time.monotonic() > budget:
                break
            result = nmap_fn(d["ip"])
            if result is not None:
                d["obs"]["nmap"] = {**result, "at": now}
            pump(state, listener, recent)
    real = {}
    for d in state.get("devices", {}).values():
        for instance in ((d.get("obs") or {}).get("mdns") or {}).get("instances", {}).get("_workstation._tcp", []):
            m = re.search(r"\[([0-9a-fA-F:]{17})\]$", instance)
            if m:
                d["real_mac"] = m.group(1).lower()
                real[d["real_mac"]] = d
    maker = makers({d["mac"] for d in state.get("devices", {}).values()} | set(real), oui_files)
    for d in state.get("devices", {}).values():
        d["maker"] = maker.get(d["mac"], d.get("maker", ""))
        if d.get("real_mac"):
            d["real_maker"] = maker.get(d["real_mac"], "")
    devices = state.get("devices", {})
    for key in [k for k, d in devices.items() if now - d.get("last_seen", 0) > FORGET_S]:
        del devices[key]
    for mac in [m for m, e in state.get("shared_macs", {}).items() if now - e.get("last", 0) > 7 * 86400]:
        del state["shared_macs"][mac]
    return refresh(state, now)


def is_due(done, every, now):
    """A check never made is due; one made is due again `every` seconds later."""
    return not done or "at" not in done or now - done["at"] > every


def probe(ip, gateway):
    """The unicast questions for one device (each may come back empty)."""
    return {"router_dns": router_name(gateway, ip) if ip != gateway else "", "mdns": mdns(ip), "netbios": netbios(ip)}


def migrate(state, now):
    """A list from the first version (a timer and no listening): keep its devices as the household."""
    if state.get("version") == 2:
        return state
    devices = {}
    for mac, d in (state.get("devices") or {}).items():
        if isinstance(d, dict) and usable_mac(mac):
            devices[mac] = {"key": mac, "mac": mac, "ip": d.get("ip"), "ips": [d["ip"]] if d.get("ip") else [],
                            "first_seen": d.get("first_seen", now), "last_seen": d.get("last_seen", now), "baseline": True,
                            "ttl": d.get("ttl"), "maker": d.get("maker", ""), "obs": {}}
    state.clear()
    state.update(version=2, devices=devices, baseline_until=now + BASELINE_S)
    return state


def save(state, path=STATE):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(state, sort_keys=True))
    os.chmod(tmp, 0o644)
    tmp.replace(path)


def watch():
    """The service: listen all the time, look every 10 minutes (SIGUSR1: look now; SIGHUP: reread your
    names and trust list)."""
    now = time.time()
    state = migrate(load_json(STATE), now)
    look_now, reload_now = threading.Event(), threading.Event()

    def quit_(*_):
        raise SystemExit(0)
    signal.signal(signal.SIGTERM, quit_)
    signal.signal(signal.SIGINT, quit_)
    signal.signal(signal.SIGUSR1, lambda *_: look_now.set())
    signal.signal(signal.SIGHUP, lambda *_: reload_now.set())
    try:
        watch_loop(state, look_now, reload_now)
    finally:
        refresh(state, time.time())
        save(state)


def watch_loop(state, look_now, reload_now):
    listener, recent, next_look, last_save, last_route, iface = None, {}, time.time() + 15, 0, 0, None
    while True:
        now = time.time()
        if now - last_route > 30:
            last_route = now
            route = default_route(read(PROC / "net/route"))
            if route and route[0] != iface:
                iface = route[0]
                if listener:
                    listener.close()
                try:
                    listener = Listener(iface)
                    print(f"listening on {iface}", flush=True)
                except (OSError, AttributeError) as e:
                    listener = None
                    print(f"not listening ({e}): asking only", flush=True)
                state["capture"] = listener is not None
        changed = pump(state, listener, recent, now)
        if look_now.is_set() or now >= next_look:
            look_now.clear()
            try:
                look(state, listener, recent, now)
            except Exception as e:                       # one bad look must not end the watch
                print(f"look failed: {e!r}", flush=True)
            next_look, changed = time.time() + LOOK_S, True
            counts = state.get("counts") or {}
            print(f"{len(state.get('devices', {}))} device(s), {counts.get('new', 0)} new, "
                  f"{len(state.get('alerts', []))} alert(s)", flush=True)
        if reload_now.is_set():
            reload_now.clear()
            changed = True
            last_save = 0
        if changed and now - last_save > 20:
            refresh(state, now)
            state["updated"] = now
            save(state)
            last_save = now
        time.sleep(1.0)


# --- the command line ---------------------------------------------------------------------------------

def current(now=None):
    """The device list: the service's own file (root), else the status snapshot (anyone)."""
    state = load_json(STATE)
    if state.get("devices"):
        return state
    status = load_json(STATUS)
    net = (status.get("system") or {}).get("network") or {}
    watch_ = net.get("lan_watch") or {}
    devices = {}
    for d in net.get("neighbours") or []:
        if isinstance(d, dict) and d.get("mac"):
            key = d.get("key") or d["mac"]
            devices[key] = {**d, "key": key, "label": d.get("name"), "last_seen": d.get("seen") or status.get("generated_at", 0)}
    return {"devices": devices, "alerts": watch_.get("alerts", []), "iface": watch_.get("iface"),
            "network": watch_.get("network"), "looked": watch_.get("looked"), "capture": watch_.get("capture")}


def find(state, who):
    """Devices matching an address, a hardware address, a key or part of a name."""
    who = (who or "").strip().lower()
    devices = list(state.get("devices", {}).values())
    exact = [d for d in devices if who in (str(d.get("ip")).lower(), d.get("mac", ""), d.get("key", "").lower())]
    if exact:
        return exact
    return [d for d in devices if who and who in " ".join([d.get("label") or "", d.get("name") or "", d.get("model") or "",
                                                          d.get("user_name") or ""]).lower()]


def show(state, who=None, now=None, width=None):
    now = time.time() if now is None else now
    devices = sorted(state.get("devices", {}).values(), key=lambda d: (ip_order(d.get("ip")), d.get("key", "")))
    if who:
        matches = find(state, who)
        if not matches:
            print(f"No device matches {who!r}. Try an address, a hardware address or part of a name.")
            return 1
        for d in matches:
            detail(d, now)
        return 0
    if not devices:
        print("No devices yet: the first look runs a minute after the service starts (sudo macserver devices scan).")
        return 0
    for a in state.get("alerts", []):
        print(f"{'CRITICAL' if a['level'] == 'bad' else 'ALERT':8} {a['title']}\n         {a['why']}")
    if state.get("alerts"):
        print()
    width = width or shutil.get_terminal_size((160, 40)).columns
    col = lambda key, least, most: min(max([len(str(d.get(key) or "")) for d in devices] + [least]), most)
    name_w, kind_w, sys_w = col("label", 6, 52), col("kind", 4, 30), col("system", 6, 30)
    print(f"{'DEVICE':{name_w}}  {'ADDRESS':15}  {'TYPE':{kind_w}}  {'SYSTEM':{sys_w}}  {'SEEN':10}  STATUS")
    for d in devices:
        status = ("NEW" if d.get("new_at") and not d.get("trusted") and now - d["new_at"] < NEW_S else
                  "yours" if d.get("trusted") else "") + (" !" if any(r["level"] == "bad" for r in d.get("risks", [])) else "")
        print(f"{(d.get('label') or d.get('mac', '?')):{name_w}}  {(d.get('ip') or 'joining'):15}  "
              f"{(d.get('kind') or '')[:kind_w]:{kind_w}}  {(d.get('system') or '-')[:sys_w]:{sys_w}}  "
              f"{ago(now - d.get('last_seen', now)):10}  {status}")
        extra = " · ".join(x for x in (d.get("model") if d.get("model") and d.get("model") != d.get("label") else "",
                                        d.get("real_maker") or d.get("maker") or ("private address" if private_mac(d.get("mac", "")) else ""),
                                        d.get("mac")) if x)
        print(f"{'':{name_w}}  {extra}")
    print(f"\n{len(devices)} device(s) on {state.get('iface') or '?'} ({state.get('network') or '?'}), "
          f"last look {ago(now - (state.get('looked') or now))}; listening: {'yes' if state.get('capture') else 'no'}. "
          "Details: macserver devices <address or name>")
    return 0


def detail(d, now):
    print(f"{d.get('label') or d.get('mac')}")
    rows = [("kind", f"{d.get('kind', '?')}  ({d.get('confidence', '?')})"), ("system", d.get("system")), ("model", d.get("model")),
            ("address", ", ".join(d.get("ips") or [d.get("ip") or "?"])), ("hardware", d.get("mac")),
            ("maker", d.get("real_maker") or d.get("maker") or ("none: a private (randomised) address" if private_mac(d.get("mac", "")) else "")),
            ("real hardware", d.get("real_mac")), ("relayed by", d.get("via")),
            ("first seen", time.strftime("%Y-%m-%d %H:%M", time.localtime(d["first_seen"])) if d.get("first_seen") else ""),
            ("last seen", ago(now - d.get("last_seen", now))), ("yours", "yes" if d.get("trusted") else "not marked")]
    for key, value in rows:
        if value:
            print(f"  {key:14} {value}")
    if d.get("names"):
        print("  names it gave:")
        for n in d["names"]:
            print(f"    {n['name']}   ({n['source']})")
    if d.get("services"):
        print("  open services:")
        for s in d["services"]:
            print(f"    {s}")
    elif d.get("ports"):
        print(f"  open ports:    {', '.join(str(p) for p in d['ports'])}")
    if d.get("risks"):
        print("  notes:")
        for r in d["risks"]:
            print(f"    [{r['level']}] {r['text']}")
    if d.get("evidence"):
        print("  how it was identified (strongest first):")
        for e in d["evidence"]:
            print(f"    - {e}")
    print(f"  name it:       sudo macserver devices name {d.get('ip') or d.get('mac')} \"Living room TV\"")
    print()


def edit_known(who, change, state=None, path=KNOWN):
    """Apply `change(entry)` to the trust/name entries of the matching devices (root writes the file)."""
    state = state or current()
    data = load_json(path)
    data.setdefault("devices", {})
    if who == "all":
        targets = list(state.get("devices", {}).values())
    else:
        targets = find(state, who)
        if len(targets) > 1:
            print(f"{who!r} matches {len(targets)} devices; use an address or a hardware address:")
            for d in targets:
                print(f"  {d.get('ip')}  {d.get('mac')}  {d.get('label')}")
            return 1
    if not targets:
        print(f"No device matches {who!r}.")
        return 1
    for d in targets:
        entry = data["devices"].setdefault(d["key"], {})
        change(entry)
        entry["at"] = int(time.time())
        entry["mac"] = d["mac"]
        print(f"{d.get('ip') or '?':15} {d['mac']}  {entry.get('name') or d.get('label') or ''}"
              f"{'  (yours)' if entry.get('trusted') else ''}")
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, indent=1, sort_keys=True))
    os.chmod(tmp, 0o644)
    tmp.replace(path)
    return 0


def main(argv):
    command = argv[0] if argv else "show"
    if command == "watch":
        watch()
        return 0
    if command == "scan":
        now = time.time()
        state = migrate(load_json(STATE), now)
        listener = None
        try:
            listener = Listener(default_route(read(PROC / "net/route"))[0])
        except (OSError, TypeError, AttributeError):
            pass
        state["capture"] = listener is not None
        look(state, listener, {}, now)
        if listener:
            time.sleep(4)
            pump(state, listener, {})
            listener.close()
        refresh(state, time.time())
        save(state)
        print(f"{len(state.get('devices', {}))} device(s) on the local network", flush=True)
        return 0
    if command == "show":
        return show(current(), argv[1] if len(argv) > 1 else None)
    if command == "name" and len(argv) >= 3:
        new = clean(" ".join(argv[2:]))
        return edit_known(argv[1], lambda e: e.update(name=new, trusted=True))
    if command == "trust" and len(argv) == 2:
        return edit_known(argv[1], lambda e: e.update(trusted=True))
    if command == "untrust" and len(argv) == 2:
        return edit_known(argv[1], lambda e: e.update(trusted=False))
    if command == "accept-router":
        state = current()
        conflict = state.get("gateway_conflict")
        if not conflict:
            print("The router's hardware address has not changed: nothing to accept.")
            return 0
        data = load_json(KNOWN)
        data["accept_router"] = conflict["mac"]
        Path(KNOWN).parent.mkdir(parents=True, exist_ok=True)
        Path(KNOWN).write_text(json.dumps(data, indent=1, sort_keys=True))
        os.chmod(KNOWN, 0o644)
        print(f"accepted {conflict['mac']} as the router")
        return 0
    if command not in ("watch", "scan", "show", "name", "trust", "untrust", "accept-router") and len(argv) == 1:
        return show(current(), command)                  # lan_devices.py 192.168.18.4
    print(__doc__)
    return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
