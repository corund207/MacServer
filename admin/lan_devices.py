#!/usr/bin/env python3
"""Who is on the local network: a name, a maker and a kind for every device the Mac can see.

    lan_devices.py scan     look now and update the device list (macserver-devices.timer, every 10 min)
    lan_devices.py show     print the device list

The screen and the admin page used to show the local network as bare MAC addresses. This asks each
device what it is, the way nmap's discovery scripts, avahi and nbtscan do, but with the standard
library only and without listening anywhere (AGENTS.md: nothing listens on the LAN). Every question is
a unicast packet to one device, so its answer comes back as a reply to the Mac's own packet and the
firewall lets it in like any other reply.

  1. Sweep: one ping to every address of the Mac's subnet. The kernel has to find each MAC address
     (ARP) to send it, so afterwards its neighbour table lists every device that is on, not only the
     ones the Mac happened to talk to. A ping reply's TTL also hints at the system: 64 for Linux,
     Apple and Android, 128 for Windows, 255 for network gear.
  2. The router's DNS: most home routers answer a reverse lookup with the name the device gave when it
     joined the network (DHCP).
  3. mDNS (Bonjour), asked directly at the device: its .local name, the services it offers (AirPlay,
     Google Cast, printing, HomeKit...) and their friendly names and models ("Living Room", "iPhone15,2").
  4. NetBIOS: a Windows PC's computer name.
  5. The maker, from the first half of the MAC address and the IEEE registry that systemd ships
     (/usr/lib/udev/hwdb.d/20-OUI.hwdb). A phone's private (randomised) address has no maker.

What a device says about itself is untrusted: names are cut to printable text of a sane length before
anything stores or shows them. The result is /var/lib/macserver-devices/devices.json, read by the
status collector. Runs as a throwaway unprivileged user (systemd DynamicUser) with no capabilities.
"""
import ipaddress
import json
import os
import random
import re
import socket
import struct
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

PROC = Path(os.environ.get("MACSERVER_PROC", "/proc"))
STATE = Path(os.environ.get("STATE_DIRECTORY", "/var/lib/macserver-devices")) / "devices.json"
OUI_FILES = (Path("/usr/lib/udev/hwdb.d/20-OUI.hwdb"), Path("/lib/udev/hwdb.d/20-OUI.hwdb"))
MAX_SWEEP = 1024             # a bigger subnet is not a home network: look only at the devices already known
FORGET_S = 30 * 86400        # a device not seen for a month leaves the list
NAME_MAX = 40

A, PTR, TXT, SRV = 1, 12, 16, 33
IP_RECVTTL, IP_TTL = 12, 2   # Linux socket option numbers (socket.IP_RECVTTL is missing on some builds)


# --- small helpers ---------------------------------------------------------------------------------

def clean(text, limit=NAME_MAX):
    """Printable text only, one line, at most `limit` characters: a device chooses its own name."""
    text = "".join(ch if ch.isprintable() else " " for ch in str(text or ""))
    text = " ".join(text.replace(" ", " ").split())
    return text[:limit].strip()


def private_mac(mac):
    """A locally administered (randomised) address: phones and laptops use one per network."""
    try:
        return bool(int(mac[:2], 16) & 2)
    except ValueError:
        return False


def read(path, default=""):
    try:
        return Path(path).read_text()
    except OSError:
        return default


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


# --- 1. the sweep: ping everyone, note who answers and with which TTL -------------------------------

def sweep(addresses, wait=2.5):
    """{ip: ttl} of the addresses that answered a ping. Uses an unprivileged ping socket
    (net.ipv4.ping_group_range); where that is not allowed, a UDP packet still makes the kernel ARP."""
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
        payload = b"macserver"
        for n, ip in enumerate(addresses):
            try:
                s.sendto(struct.pack("!BBHHH", 8, 0, 0, 0, n & 0xFFFF) + payload, (ip, 0))
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


def system_from_ttl(ttl):
    if not ttl:
        return ""
    return "windows" if 64 < ttl <= 128 else "network" if ttl > 128 else "unix"


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
    for _ in range(qd):
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


# --- 2. the router's DNS ----------------------------------------------------------------------------

def router_name(gateway, ip, timeout=0.8):
    qid = random.randrange(65536)
    for name, rtype, value in ask(gateway, 53, dns_query([(reverse_name(ip), PTR)], qid, recursion=True), qid, timeout):
        if rtype == PTR and value:
            host = value.split(".")[0]
            if host and not re.fullmatch(r"[\d-]+|(ip|host|dhcp|unknown|localhost)[\w-]*", host, re.I):
                return clean(host)
    return ""


# --- 3. mDNS, asked of the device itself --------------------------------------------------------------

# the services worth a second question: they carry a friendly name or a model, and say what the device is
NAMED_SERVICES = ("_companion-link._tcp", "_airplay._tcp", "_googlecast._tcp", "_raop._tcp", "_ipp._tcp",
                  "_printer._tcp", "_hap._tcp", "_spotify-connect._tcp", "_sonos._tcp", "_amzn-wplay._tcp",
                  "_androidtvremote2._tcp", "_smb._tcp", "_workstation._tcp", "_rdlink._tcp", "_sleep-proxy._udp")


def mdns(ip, timeout=0.7):
    """{"host", "services", "names" {service: instance}, "txt" {key: value}} from the device's own mDNS."""
    found = {"host": "", "services": [], "names": {}, "txt": {}}
    qid = random.randrange(65536)
    first = ask(ip, 5353, dns_query([(reverse_name(ip), PTR), ("_services._dns-sd._udp.local", PTR)], qid), qid, timeout)
    if not first:
        return found
    services = set()
    for name, rtype, value in first:
        if rtype == PTR and name.endswith(".in-addr.arpa") and value:
            found["host"] = clean(value.removesuffix(".").removesuffix(".local"))
        elif rtype == PTR and name.startswith("_services._dns-sd") and value:
            services.add(value.removesuffix(".local").removesuffix("."))
    found["services"] = sorted(services)[:24]
    wanted = [s for s in NAMED_SERVICES if s in services][:6] + ["_device-info._tcp"]
    qid = random.randrange(65536)
    for name, rtype, value in ask(ip, 5353, dns_query([(f"{s}.local", PTR) for s in wanted], qid), qid, timeout):
        if rtype == PTR and value:
            service = name.removesuffix(".local").removesuffix(".")
            instance = value[:-(len(name) + 1)] if value.endswith("." + name) else value.split("._")[0]
            found["names"].setdefault(service, clean(instance.replace("\\032", " ")))
        elif rtype == TXT and isinstance(value, dict):
            for key in ("model", "md", "fn", "ty", "am", "usb_mdl", "product"):
                if value.get(key) and key not in found["txt"]:
                    found["txt"][key] = clean(value[key])
    found["host"] = real_name(found["host"])
    return found


# --- 4. NetBIOS: a Windows PC's computer name ----------------------------------------------------------

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
        count = data[pos]
        for n in range(min(count, 64)):
            entry = data[pos + 1 + 18 * n:pos + 19 + 18 * n]
            if len(entry) < 18:
                break
            name, suffix, flags = entry[:15].decode("ascii", "replace").strip(), entry[15], struct.unpack("!H", entry[16:18])[0]
            if suffix == 0 and not flags & 0x8000 and name:
                return clean(name)
    except (IndexError, struct.error):
        pass
    return ""


# --- 5. the maker -------------------------------------------------------------------------------------

def makers(macs, files=OUI_FILES):
    """{mac: maker} from the IEEE registry systemd ships. Longer (MA-M, MA-S) assignments win."""
    wanted = {}
    for mac in macs:
        if not private_mac(mac):
            digits = mac.replace(":", "").upper()
            for size in (9, 7, 6):
                wanted.setdefault(digits[:size], []).append(mac)
    out, best = {}, {}
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


# --- what it is ---------------------------------------------------------------------------------------

APPLE_MODEL = re.compile(r"(iPhone|iPad|iPod|Watch|AppleTV|AudioAccessory|MacBook\w*|iMac\w*|Macmini|MacPro|Mac)\d+,\d+$")
APPLE_KINDS = {"iPhone": ("iPhone", "iOS"), "iPad": ("iPad", "iPadOS"), "iPod": ("iPod", "iOS"), "Watch": ("Apple Watch", "watchOS"),
               "AppleTV": ("Apple TV", "tvOS"), "AudioAccessory": ("HomePod", "HomePod")}
NAS_MAKERS = ("synology", "qnap", "ugreen", "western digital", "asustor", "buffalo", "terramaster")
NAME_HINTS = (("iphone", "iPhone", "iOS"), ("ipad", "iPad", "iPadOS"), ("macbook", "Mac", "macOS"), ("imac", "Mac", "macOS"),
              ("apple-tv", "Apple TV", "tvOS"), ("appletv", "Apple TV", "tvOS"), ("homepod", "HomePod", "HomePod"),
              ("android", "Android phone", "Android"), ("galaxy", "Android phone", "Android"), ("pixel", "Android phone", "Android"),
              ("desktop-", "Windows PC", "Windows"), ("laptop-", "Windows PC", "Windows"), ("raspberrypi", "Raspberry Pi", "Linux"),
              ("chromecast", "Chromecast", "Google TV"), ("roku", "Roku", "Roku OS"), ("echo", "Amazon Echo", "Fire OS"),
              ("fire-tv", "Fire TV", "Fire OS"), ("firetv", "Fire TV", "Fire OS"), ("kindle", "Kindle", "Fire OS"),
              ("lgwebostv", "LG TV", "webOS"), ("samsung", "Samsung device", ""), ("xbox", "Xbox", "Xbox"),
              ("playstation", "PlayStation", "PlayStation"), ("ps5-", "PlayStation 5", "PlayStation"),
              ("ps4-", "PlayStation 4", "PlayStation"), ("nintendo", "Nintendo console", ""),
              ("printer", "printer", ""), ("nest", "Google Nest", "Google"), ("sonos", "Sonos speaker", "Sonos"))
MAKER_HINTS = (("roku", "Roku", "Roku OS"), ("amazon", "Amazon device", "Fire OS"), ("sonos", "Sonos speaker", "Sonos"),
               ("raspberry", "Raspberry Pi", "Linux"), ("espressif", "smart-home device", ""), ("tuya", "smart-home device", ""),
               ("nintendo", "Nintendo console", ""), ("sony interactive", "PlayStation", "PlayStation"),
               ("hewlett packard", "printer", ""), ("canon", "printer", ""), ("seiko epson", "printer", ""), ("brother", "printer", ""),
               ("ring llc", "Ring camera", ""), ("ecobee", "thermostat", ""), ("nest", "Google Nest", "Google"),
               ("aura home", "photo frame", ""), ("wyze", "camera", ""), ("arlo", "camera", ""), ("philips lighting", "Hue bridge", ""),
               ("google", "Google device", ""), ("apple", "Apple device", ""), ("samsung", "Samsung device", ""),
               ("lg electronics", "LG device", ""), ("vizio", "Vizio TV", "SmartCast"), ("tp-link", "network device", ""),
               ("netgear", "network device", ""), ("ubiquiti", "network device", ""), ("eero", "Wi-Fi point", ""),
               ("intel", "computer", ""), ("liteon", "computer", ""), ("azurewave", "computer", ""), ("realtek", "computer", ""),
               ("microsoft", "Microsoft device", "Windows"), ("dell", "computer", ""), ("lenovo", "computer", ""))
# a maker that only says "a computer, a chip, a brand" is weaker evidence than a service ("it plays Spotify")
WEAK_MAKER_KINDS = ("computer", "network device", "Google device", "Apple device", "Samsung device", "LG device", "Microsoft device")
SERVICE_HINTS = (("_googlecast._tcp", "Chromecast / Google TV", "Google TV"), ("_androidtvremote2._tcp", "Android TV", "Android TV"),
                 ("_amzn-wplay._tcp", "Fire TV", "Fire OS"), ("_sonos._tcp", "Sonos speaker", "Sonos"),
                 ("_ipp._tcp", "printer", ""), ("_printer._tcp", "printer", ""), ("_hap._tcp", "HomeKit device", ""))
# offered by many kinds of device (TVs, consoles, speakers, PCs): only after a specific maker
GENERIC_SERVICE_HINTS = (("_airplay._tcp", "AirPlay device", ""), ("_raop._tcp", "AirPlay speaker", ""),
                         ("_spotify-connect._tcp", "speaker", ""), ("_smb._tcp", "computer", ""),
                         ("_workstation._tcp", "Linux computer", "Linux"))


def classify(d):
    """(kind, system) from everything known about one device; the most specific evidence wins."""
    if d.get("router"):
        return "router", ""
    model = (d.get("txt") or {}).get("model", "") or (d.get("txt") or {}).get("am", "")
    apple = APPLE_MODEL.match(model.replace(" ", ""))
    if apple:
        return APPLE_KINDS.get(apple.group(1), ("Mac", "macOS"))
    services = d.get("services") or []
    maker = (d.get("maker") or "").lower()
    if "_smb._tcp" in services and ("_nut._tcp" in services or model.lower() == "macsamba"
                                    or any(m in maker for m in NAS_MAKERS)):
        return "NAS", "Linux"
    if "amazon" in maker and any(s.startswith("_matter") for s in services):
        return "Amazon Echo", "Fire OS"
    if "_companion-link._tcp" in services or "_apple-mobdev2._tcp" in services:
        names = " ".join([d.get("name", ""), d.get("host", "")]).lower()
        for key, kind, system in NAME_HINTS[:6]:
            if key in names:
                return kind, system
        return "Apple device", "Apple"
    names = " ".join([d.get("name", ""), d.get("host", ""), d.get("dhcp", ""), d.get("netbios", "")]).lower()
    for key, kind, system in NAME_HINTS:
        if key in names:
            return kind, system or {"windows": "Windows", "unix": ""}.get(system_from_ttl(d.get("ttl")), "")
    for key, kind, system in SERVICE_HINTS:
        if key in services:
            return kind, system
    if d.get("netbios") and system_from_ttl(d.get("ttl")) != "unix":
        return "Windows PC", "Windows"
    for key, kind, system in MAKER_HINTS:
        if key in maker and kind not in WEAK_MAKER_KINDS:
            return kind, system
    for key, kind, system in GENERIC_SERVICE_HINTS:
        if key in services:
            return kind, system
    for key, kind, system in MAKER_HINTS:
        if key in maker:
            if kind == "computer" and system_from_ttl(d.get("ttl")) == "windows":
                return "Windows PC", "Windows"
            return kind, system
    family = system_from_ttl(d.get("ttl"))
    if family == "windows":
        return "Windows PC", "Windows"
    if private_mac(d.get("mac", "")):
        return ("phone or tablet", "iOS or Android") if family == "unix" else ("phone or laptop", "")
    if family == "network":
        return "network device", ""
    return "device", ""


def real_name(name):
    """A name a person would recognise: not empty, not "none", not a serial number or an ID."""
    name = clean(name)
    if not name or name.lower() in ("none", "localhost", "unknown", "android", "espressif", "(null)"):
        return ""
    if re.fullmatch(r"[0-9a-f]{8}(-[0-9a-f]{4}){3}-[0-9a-f]{12}|[0-9A-Fa-f]{12,}|[A-Z0-9]{10,}", name):
        return ""
    return name


def display_name(d):
    """The friendliest name the device gave: a service's friendly name, its .local name, its DHCP name."""
    names = d.get("names") or {}
    for service in ("_companion-link._tcp", "_device-info._tcp", "_airplay._tcp", "_googlecast._tcp", "_hap._tcp",
                    "_sonos._tcp", "_ipp._tcp", "_printer._tcp", "_smb._tcp", "_workstation._tcp"):
        if real_name(names.get(service, "").split(" [")[0]):
            return real_name(names[service].split(" [")[0])
    if real_name((d.get("txt") or {}).get("fn")):
        return real_name(d["txt"]["fn"])
    if names.get("_raop._tcp") and real_name(names["_raop._tcp"].split("@", 1)[-1]):
        return real_name(names["_raop._tcp"].split("@", 1)[-1])
    for key in ("host", "dhcp", "netbios"):
        if real_name(d.get(key)):
            return real_name(d[key])
    return real_name(names.get("_spotify-connect._tcp", ""))


def short_maker(maker):
    """"Roku, Inc" -> "Roku", "Nokia Solutions and Networks" -> "Nokia"."""
    word = re.split(r"[\s,.(]", clean(maker), maxsplit=1)[0]
    return word if len(word) > 1 else clean(maker)[:12]


def label(d):
    """What the lists show: the device's own name, else its maker and the end of its address."""
    if d.get("name"):
        return d["name"]
    if d.get("router"):
        return f"router ({short_maker(d['maker'])})" if d.get("maker") else "router"
    if d.get("maker"):
        return f"{short_maker(d['maker'])} …{d['mac'][-5:]}"
    return f"private …{d['mac'][-5:]}" if private_mac(d.get("mac", "")) else d.get("mac", "?")


# --- one look ------------------------------------------------------------------------------------------

def probe(ip, gateway):
    """Everything one device will say about itself (each part may come back empty)."""
    found = {"dhcp": router_name(gateway, ip) if ip != gateway else ""}
    found.update(mdns(ip))
    found["netbios"] = netbios(ip)
    return found


def scan(state, now=None, proc=PROC, sweep_fn=sweep, probe_fn=probe, oui_files=OUI_FILES, own_fn=own_address):
    """Look at the network once and merge what was found into `state` (kept between runs)."""
    now = time.time() if now is None else now
    route = default_route(read(proc / "net/route"))
    if not route:
        return state
    iface, gateway, net = route
    me = own_fn(gateway)
    addresses = [str(a) for a in net.hosts() if str(a) != me] if net.num_addresses <= MAX_SWEEP else \
        [ip for ip in neighbours(read(proc / "net/arp"), iface)]
    ttls = sweep_fn(addresses)
    seen = {ip: mac for ip, mac in neighbours(read(proc / "net/arp"), iface).items() if ip != me}
    with ThreadPoolExecutor(max_workers=16) as pool:
        answers = dict(zip(seen, pool.map(lambda ip: probe_fn(ip, gateway), seen)))
    maker = makers(seen.values(), oui_files)
    devices = state.setdefault("devices", {})
    by_mac = {}                                          # one device can hold several addresses (old DHCP leases)
    for ip, mac in seen.items():
        by_mac.setdefault(mac, []).append(ip)
    for mac, ips in by_mac.items():
        # its main address: the one that answered questions, then the one that answered a ping, then the lowest
        ips.sort(key=lambda a: (not any(answers.get(a, {}).values()), a not in ttls, [int(n) for n in a.split(".")]))
        ip, old, new = ips[0], devices.get(mac, {}), {}
        for a in ips:                                    # what any of its addresses said
            for key, value in answers.get(a, {}).items():
                if value and not new.get(key):
                    new[key] = value
        d = {"mac": mac, "ip": ip, "ips": ips[:8], "router": gateway in ips, "maker": maker.get(mac, ""),
             "ttl": next((ttls[a] for a in ips if ttls.get(a)), None) or old.get("ttl"),
             "first_seen": old.get("first_seen", now), "last_seen": now}
        for key in ("host", "dhcp", "netbios"):          # a sleeping phone answers nothing: keep what it said before
            d[key] = new.get(key) or old.get(key, "")
        for key in ("services", "names", "txt"):
            d[key] = new.get(key) or old.get(key) or ([] if key == "services" else {})
        d["name"] = display_name(d)
        d["kind"], d["system"] = classify(d)
        d["label"] = label(d)
        devices[mac] = d
    for mac in [m for m, d in devices.items() if now - d.get("last_seen", 0) > FORGET_S]:
        del devices[mac]
    state.update(updated=now, iface=iface, gateway=gateway, network=str(net))
    return state


def load(path=STATE):
    try:
        data = json.loads(Path(path).read_text())
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def save(state, path=STATE):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(state, sort_keys=True))
    os.chmod(tmp, 0o644)
    tmp.replace(path)


def show(state, now=None):
    now = time.time() if now is None else now
    devices = sorted(state.get("devices", {}).values(), key=lambda d: [int(n) for n in d.get("ip", "0.0.0.0").split(".")])
    if not devices:
        print("No devices yet: the first look runs a few minutes after boot (sudo macserver devices scan).")
        return
    print(f"{'NAME':24} {'ADDRESS':16} {'KIND':22} {'SYSTEM':14} {'MAKER':26} SEEN")
    for d in devices:
        ago = int(now - d.get("last_seen", now))
        print(f"{(d.get('label') or d['mac'])[:24]:24} {d['ip']:16} {d.get('kind', '')[:22]:22} {d.get('system', '')[:14]:14} "
              f"{(d.get('maker') or ('private address' if private_mac(d['mac']) else '-'))[:26]:26} "
              f"{'now' if ago < 900 else f'{ago // 3600}h ago' if ago < 86400 else f'{ago // 86400}d ago'}")
    print(f"\n{len(devices)} device(s) on {state.get('iface', '?')} ({state.get('network', '?')}), "
          f"looked {int((now - state.get('updated', now)) // 60)} min ago")


def main(argv):
    command = argv[0] if argv else "show"
    if command == "scan":
        state = scan(load())
        save(state)
        print(f"{len(state.get('devices', {}))} device(s) on the local network", flush=True)
        return 0
    if command == "show":
        state = load()
        if not state.get("devices"):                       # the scan's own file is root-only: use the status snapshot
            status = load(Path("/run/macserver/status.json"))
            known = ((status.get("system") or {}).get("network") or {}).get("neighbours") or []
            state = {"devices": {d["mac"]: {**d, "label": d.get("name"), "last_seen": d.get("seen") or status.get("generated_at", 0)}
                                 for d in known if isinstance(d, dict) and d.get("mac") and d.get("ip")},
                     "updated": status.get("generated_at", 0)} if known else {}
        show(state)
        return 0
    print(__doc__)
    return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
