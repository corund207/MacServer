"""admin/lan_devices.py, the network watch: who is on the local network, exactly, and alerts about
strangers. No network needed: frames are built by hand, the sweep, the probes and nmap are stand-ins,
and the answers are the ones real devices gave on the owner's network (2026-11-26)."""
import contextlib
import importlib.util
import io
import json
import random
import socket
import struct
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("lan_devices", ROOT / "admin/lan_devices.py")
lan = importlib.util.module_from_spec(spec)
spec.loader.exec_module(lan)

ROUTE = ("Iface\tDestination\tGateway\tFlags\tRefCnt\tUse\tMetric\tMask\tMTU\tWindow\tIRTT\n"
         "wlp2s0\t00000000\t0112A8C0\t0003\t0\t0\t600\t00000000\t0\t0\t0\n"
         "br-0e5b\t000012AC\t00000000\t0001\t0\t0\t0\t0000FFFF\t0\t0\t0\n"
         "wlp2s0\t0012A8C0\t00000000\t0001\t0\t0\t600\t00FFFFFF\t0\t0\t0\n")
ARP = ("IP address       HW type     Flags       HW address            Mask     Device\n"
       "192.168.18.1     0x1         0x2         e0:1f:2b:38:b7:30     *        wlp2s0\n"
       "192.168.18.4     0x1         0x2         D8:31:34:67:B6:38     *        wlp2s0\n"
       "192.168.18.40    0x1         0x2         60:5b:b4:05:d2:c3     *        wlp2s0\n"
       "192.168.18.59    0x1         0x2         b6:33:93:f6:8c:a9     *        wlp2s0\n"
       "192.168.18.77    0x1         0x0         00:00:00:00:00:00     *        wlp2s0\n"
       "172.18.0.8       0x1         0x2         46:f9:62:08:92:a8     *        br-0e5b\n")
OUI = ("OUI:E01F2B*\n ID_OUI_FROM_DATABASE=Nokia Solutions and Networks GmbH & Co. KG\n\n"
       "OUI:D83134*\n ID_OUI_FROM_DATABASE=Roku, Inc\n\n"
       "OUI:605BB4*\n ID_OUI_FROM_DATABASE=AzureWave Technology Inc.\n\n"
       "OUI:A0B339*\n ID_OUI_FROM_DATABASE=Intel Corporate\n\n"
       "OUI:A0B339067*\n ID_OUI_FROM_DATABASE=Longer Prefix Wins Ltd\n\n")
GATEWAY = "192.168.18.1"
ROUTER, ROKU, PS4, IPHONE = "e0:1f:2b:38:b7:30", "d8:31:34:67:b6:38", "60:5b:b4:05:d2:c3", "b6:33:93:f6:8c:a9"
EXTENDER = "76:da:88:94:ec:c8"
MAC_ADDRESS = lambda gateway: "192.168.18.33"          # this Mac, whatever network the test runs on


# --- building packets ---------------------------------------------------------------------------------

def answer(qid, records, flags=0x8400):
    """A DNS answer message: records = [(name, type, rdata bytes)]."""
    body = b"".join(lan.encode_name(n) + struct.pack("!HHIH", t, 1, 120, len(r)) + r for n, t, r in records)
    return struct.pack("!HHHHHH", qid, flags, 0, len(records), 0, 0) + body


def txt(**items):
    return b"".join(bytes([len(f"{k}={v}")]) + f"{k}={v}".encode() for k, v in items.items())


def srv(target):
    return struct.pack("!HHH", 0, 0, 7000) + lan.encode_name(target)


def eth(src, payload, etype=0x0800, dst="ff:ff:ff:ff:ff:ff"):
    return bytes.fromhex(dst.replace(":", "")) + bytes.fromhex(src.replace(":", "")) + struct.pack("!H", etype) + payload


def udp(sip, dip, sport, dport, payload, frag=0):
    u = struct.pack("!HHHH", sport, dport, 8 + len(payload), 0) + payload
    return struct.pack("!BBHHHBBH4s4s", 0x45, 0, 20 + len(u), 1, frag, 64, 17, 0, socket.inet_aton(sip), socket.inet_aton(dip)) + u


def dhcp(mac, options, op=1):
    p = bytearray(240)
    p[0], p[1], p[2] = op, 1, 6
    p[28:34] = bytes.fromhex(mac.replace(":", ""))
    p[236:240] = lan.DHCP_MAGIC
    for code, value in options:
        p += bytes([code, len(value)]) + value
    return bytes(p + b"\xff")


def dhcp_join(mac, hostname=b"", vendor=b"", ip="192.168.18.77", prl=bytes([1, 3, 6, 15]), src=None):
    opts = [(53, b"\x03"), (50, socket.inet_aton(ip)), (55, prl)]
    opts += [(12, hostname)] if hostname else []
    opts += [(60, vendor)] if vendor else []
    return eth(src or mac, udp("0.0.0.0", "255.255.255.255", 68, 67, dhcp(mac, opts)))


def arp(mac, ip, op=2):
    body = struct.pack("!HHBBH", 1, 0x0800, 6, 4, op) + bytes.fromhex(mac.replace(":", "")) + socket.inet_aton(ip) + b"\x00" * 6 + socket.inet_aton("192.168.18.33")
    return eth(mac, body, 0x0806)


def mdns_frame(mac, ip, records, flags=0x8400):
    return eth(mac, udp(ip, "224.0.0.251", 5353, 5353, answer(0, records, flags)), dst="01:00:5e:00:00:fb")


def ssdp_frame(mac, ip, text, port=1900):
    return eth(mac, udp(ip, "239.255.255.250", port, port, text.replace("\n", "\r\n").encode()), dst="01:00:5e:7f:ff:fa")


def nb_encode(name, suffix=0x00):
    raw = name.upper().ljust(15)[:15].encode() + bytes([suffix])
    return bytes([32]) + b"".join(bytes([0x41 + (c >> 4), 0x41 + (c & 15)]) for c in raw) + b"\x00"


BAMBU = """NOTIFY * HTTP/1.1
HOST: 239.255.255.250:1900
Server: UPnP/1.0
Location: 192.168.18.11
NT: urn:bambulab-com:device:3dprinter:1
USN: 03919D540808722
DevModel.bambu.com: N2S
DevName.bambu.com: JC printer
DevConnect.bambu.com: cloud
DevBind.bambu.com: occupied
DevVersion.bambu.com: 01.08.01.00

"""


def bpf_run(program, frame):
    """A tiny interpreter for the classic BPF the listener attaches: what the kernel would keep."""
    ins = [struct.unpack_from("HBBI", program, i) for i in range(0, len(program), 8)]
    a = x = pc = 0
    while True:
        code, jt, jf, k = ins[pc]
        pc += 1
        if code == 0x28:
            if k + 2 > len(frame):
                return 0
            a = struct.unpack_from("!H", frame, k)[0]
        elif code == 0x30:
            if k >= len(frame):
                return 0
            a = frame[k]
        elif code == 0xB1:
            x = (frame[k] & 0xF) * 4
        elif code == 0x48:
            if x + k + 2 > len(frame):
                return 0
            a = struct.unpack_from("!H", frame, x + k)[0]
        elif code == 0x15:
            pc += jt if a == k else jf
        elif code == 0x45:
            pc += jt if a & k else jf
        elif code == 0x06:
            return k
        else:
            raise AssertionError(f"unknown instruction {code:#x}")


# --- the parts ------------------------------------------------------------------------------------------

class NetworkTests(unittest.TestCase):
    def test_the_default_route_gives_the_interface_router_and_subnet(self):
        iface, gateway, net = lan.default_route(ROUTE)
        self.assertEqual((iface, gateway, str(net)), ("wlp2s0", GATEWAY, "192.168.18.0/24"))
        self.assertIsNone(lan.default_route("Iface\tDestination\n"))

    def test_only_complete_entries_on_the_interface(self):
        self.assertEqual(lan.neighbours(ARP, "wlp2s0"), {GATEWAY: ROUTER, "192.168.18.4": ROKU, "192.168.18.40": PS4,
                                                         "192.168.18.59": IPHONE})

    def test_makers_from_the_registry_and_none_for_private_addresses(self):
        with tempfile.TemporaryDirectory() as tmp:
            oui = Path(tmp) / "20-OUI.hwdb"
            oui.write_text(OUI)
            found = lan.makers([ROUTER, ROKU, "a0:b3:39:06:7c:cc", IPHONE], [oui])
        self.assertEqual(found, {ROUTER: "Nokia Solutions and Networks GmbH & Co. KG", ROKU: "Roku, Inc",
                                 "a0:b3:39:06:7c:cc": "Longer Prefix Wins Ltd"})

    def test_short_makers(self):
        self.assertEqual(lan.short_maker("Nokia Solutions and Networks GmbH & Co. KG"), "Nokia Solutions and Networks")
        self.assertEqual(lan.short_maker("Intel Corporate"), "Intel")
        self.assertEqual(lan.short_maker("Nintendo Co.,Ltd"), "Nintendo")
        self.assertEqual(lan.short_maker("Amazon Technologies Inc."), "Amazon")


class MessageTests(unittest.TestCase):
    def test_answers_with_compression_ptr_txt_and_a(self):
        msg = answer(7, [("_airplay._tcp.local", lan.PTR, b"\x14Roku Streaming Stick" + b"\xc0\x0c"),
                         ("Roku Streaming Stick._airplay._tcp.local", lan.TXT, txt(model="3810RW", fn="Den")),
                         ("roku.local", lan.A, bytes([192, 168, 18, 4]))])
        qid, records = lan.parse_dns(msg)
        self.assertEqual(qid, 7)
        self.assertEqual(records[0], ("_airplay._tcp.local", lan.PTR, "Roku Streaming Stick._airplay._tcp.local"))
        self.assertEqual(records[1][2], {"model": "3810RW", "fn": "Den"})
        self.assertEqual(records[2][2], "192.168.18.4")

    def test_a_hostile_answer_is_refused_not_followed(self):
        loop = struct.pack("!HHHHHH", 1, 0x8400, 0, 1, 0, 0) + b"\xc0\x0c"        # a pointer to itself
        for bad in (b"", b"\x00" * 5, loop, answer(1, [("a.local", lan.PTR, b"\x05ab")]),
                    struct.pack("!HHHHHH", 1, 0, 0, 9, 0, 0) + b"\x01a\x00"):
            with self.subTest(bad=bad):
                with self.assertRaises((ValueError, struct.error, IndexError)):
                    lan.parse_dns(bad)

    def test_names_are_printable_and_short(self):
        self.assertEqual(lan.clean("Evil\x1b[2J\x07Name\nnext"), "Evil [2J Name next")
        self.assertEqual(len(lan.clean("x" * 500)), lan.NAME_MAX)
        for generic in ("none", "linux", "LocalNetworkPrivacy", "6f8a5bec-49da-562d-9289-9fdecce0ac05", "YH003S781286",
                        "9C7427FEAF59", "798CC5793B59090F-04065634CD8F47B1"):
            self.assertEqual(lan.real_name(generic), "", generic)
        for real in ("PS4-067CCC", "DH4300PLUS-6D77", "HS-DJH2LMVT4M", "Andrews-iPhone", "JC printer"):
            self.assertEqual(lan.real_name(real), real)


class FrameTests(unittest.TestCase):
    """What the listener makes of each kind of broadcast."""

    def test_a_joining_iphone_names_itself_and_its_request_pattern(self):
        frame = dhcp_join("da:11:22:33:44:55", b"iPhone", prl=bytes([1, 121, 3, 6, 15, 108, 114, 119, 252]))
        self.assertEqual(lan.parse_frame(frame), [{"kind": "dhcp", "mac": "da:11:22:33:44:55", "frame_mac": "da:11:22:33:44:55",
                                                   "ip": "192.168.18.77", "type": 3, "hostname": "iPhone", "vendor": "",
                                                   "prl": "1,121,3,6,15,108,114,119,252", "fqdn": ""}])

    def test_dhcp_vendor_and_a_dhcp_server(self):
        self.assertEqual(lan.parse_frame(dhcp_join("aa:bb:cc:00:00:01", b"Galaxy-S24", b"android-dhcp-14"))[0]["vendor"], "android-dhcp-14")
        offer = eth("de:ad:be:ef:00:01", udp("192.168.18.66", "255.255.255.255", 67, 68,
                                              dhcp("aa:bb:cc:00:00:01", [(53, b"\x02"), (54, socket.inet_aton("192.168.18.66"))], op=2)))
        self.assertEqual(lan.parse_frame(offer), [{"kind": "dhcp-server", "mac": "de:ad:be:ef:00:01", "ip": "192.168.18.66",
                                                   "server": "192.168.18.66", "type": 2}])

    def test_upnp_notices_and_searches(self):
        (notice,) = lan.parse_frame(ssdp_frame("30:ed:a0:c5:ed:d0", "192.168.18.11", BAMBU, 2021))
        self.assertEqual((notice["kind"], notice["headers"]["devname.bambu.com"], notice["headers"]["devmodel.bambu.com"]),
                         ("ssdp", "JC printer", "N2S"))
        (search,) = lan.parse_frame(ssdp_frame(EXTENDER, "192.168.18.41", "M-SEARCH * HTTP/1.1\nST: ssdp:all\n"
                                                                            "USER-AGENT: Chromium/126.0.6478.183 Windows\n\n"))
        self.assertEqual((search["kind"], search["agent"]), ("ssdp-search", "Chromium/126.0.6478.183 Windows"))

    def test_mdns_answers_but_not_questions(self):
        records = [("_workstation._tcp.local", lan.PTR, lan.encode_name("HS-DJH2LMVT4M [7c:3b:2d:8f:ec:29]._workstation._tcp.local"))]
        (ob,) = lan.parse_frame(mdns_frame("32:a8:db:61:45:82", "192.168.18.55", records))
        self.assertEqual(ob["records"][0][2], "HS-DJH2LMVT4M [7c:3b:2d:8f:ec:29]._workstation._tcp.local")
        self.assertEqual(lan.parse_frame(mdns_frame("32:a8:db:61:45:82", "192.168.18.55", records, flags=0)), [])

    def test_netbios_registration_and_arp(self):
        reg = struct.pack("!HHHHHH", 1, 0x2910, 1, 0, 0, 1) + nb_encode("DESKTOP-JQVC05U") + struct.pack("!HH", 0x20, 1) + b"\x00" * 20
        (ob,) = lan.parse_frame(eth("a0:b3:39:ae:e5:d4", udp("192.168.18.26", "192.168.18.255", 137, 137, reg)))
        self.assertEqual((ob["kind"], ob["name"]), ("netbios", "DESKTOP-JQVC05U"))
        self.assertEqual(lan.parse_frame(arp(ROUTER, GATEWAY)), [{"kind": "arp", "mac": ROUTER, "ip": GATEWAY, "op": 2}])

    def test_hostile_frames_never_raise(self):
        rng = random.Random(7)
        good = [dhcp_join("da:11:22:33:44:55", b"iPhone"), ssdp_frame(ROKU, "192.168.18.4", BAMBU),
                mdns_frame(ROKU, "192.168.18.4", [("x.local", lan.A, bytes(4))]), arp(ROUTER, GATEWAY)]
        for frame in good:
            for cut in range(0, len(frame), 7):
                lan.parse_frame(frame[:cut])
            for _ in range(200):
                b = bytearray(frame)
                for _ in range(rng.randint(1, 8)):
                    b[rng.randrange(len(b))] = rng.randrange(256)
                self.assertIsInstance(lan.parse_frame(bytes(b)), list)
        for _ in range(500):
            self.assertIsInstance(lan.parse_frame(bytes(rng.randrange(256) for _ in range(rng.randint(0, 600)))), list)
        fragment = eth(ROKU, udp("192.168.18.4", "224.0.0.251", 5353, 5353, b"\x00" * 20, frag=0x2000))
        self.assertEqual(lan.parse_frame(fragment), [])
        self.assertEqual(lan.parse_frame(eth("01:00:5e:00:00:fb", udp("192.168.18.4", "224.0.0.251", 68, 67, b""))), [])

    def test_the_kernel_filter_keeps_discovery_traffic_only(self):
        program, count = lan.bpf_program()
        self.assertEqual(len(program), 8 * count)
        keep = [dhcp_join("da:11:22:33:44:55", b"iPhone"), arp(ROUTER, GATEWAY), ssdp_frame(ROKU, "192.168.18.4", BAMBU),
                ssdp_frame(ROKU, "192.168.18.11", BAMBU, 2021), mdns_frame(ROKU, "192.168.18.4", [("x.local", lan.A, bytes(4))]),
                eth(ROKU, udp("192.168.18.4", "192.168.18.33", 5353, 40000, b"\x00" * 12))]
        drop = [eth(ROKU, udp("192.168.18.4", "192.168.18.33", 443, 40000, b"\x00" * 12)),              # ordinary traffic
                eth(ROKU, udp("192.168.18.4", "224.0.0.251", 5353, 5353, b"\x00" * 12, frag=0x0001)),  # a later fragment
                eth(ROKU, b"\x45" + b"\x00" * 8 + b"\x06" + b"\x00" * 40),                              # TCP
                eth(ROKU, b"\x00" * 60, etype=0x86DD)]                                                  # IPv6
        for frame in keep:
            self.assertGreater(bpf_run(program, frame), 0)
        for frame in drop:
            self.assertEqual(bpf_run(program, frame), 0)


class UpnpAndNmapTests(unittest.TestCase):
    def test_a_description_is_fetched_only_from_the_device_itself(self):
        self.assertTrue(lan.upnp_allowed("http://192.168.18.4:8060/", "192.168.18.4"))
        for location in ("https://192.168.18.4/", "http://192.168.18.5:8060/", "http://example.com/", "file:///etc/passwd",
                         "http://127.0.0.1/", "ftp://192.168.18.4/"):
            self.assertFalse(lan.upnp_allowed(location, "192.168.18.4"), location)
        self.assertFalse(lan.upnp_allowed("http://8.8.8.8/", "8.8.8.8"))
        self.assertEqual(lan.upnp_describe("http://example.com/", "192.168.18.4"), {})

    def test_the_description_fields(self):
        doc = ("<root><device><deviceType>urn:roku-com:device:player:1-0</deviceType><friendlyName>Roku Streaming Stick+</friendlyName>"
               "<manufacturer>Roku</manufacturer><modelName>3810RW</modelName><modelNumber>3810RW</modelNumber>"
               "<modelDescription>Roku Streaming Player &amp; Network Media</modelDescription></device></root>")
        self.assertEqual(lan.upnp_fields(doc), {"friendlyName": "Roku Streaming Stick+", "manufacturer": "Roku", "modelName": "3810RW",
                                                "modelNumber": "3810RW", "modelDescription": "Roku Streaming Player & Network Media",
                                                "deviceType": "urn:roku-com:device:player:1-0"})
        self.assertEqual(lan.upnp_fields("<friendlyName>" + "x" * 5000 + "</friendlyName>"), {})

    def test_nmap_xml(self):
        xml = """<?xml version="1.0"?><nmaprun><host><ports>
<port protocol="tcp" portid="443"><state state="open"/><service name="https" product="lighttpd" tunnel="ssl"/>
<script id="ssl-cert" output="..."><table key="subject"><elem key="commonName">03919D540808722</elem></table>
<table key="issuer"><elem key="organizationName">BBL Technologies Co., Ltd</elem></table></script>
<script id="http-title" output="Printer login"/></port>
<port protocol="tcp" portid="3389"><state state="open"/><service name="ms-wbt-server" product="Microsoft Terminal Services"/>
<script id="rdp-ntlm-info" output="..."><elem key="NetBIOS_Computer_Name">DESKTOP-JQVC05U</elem><elem key="Product_Version">10.0.22631</elem></script></port>
<port protocol="tcp" portid="22"><state state="closed"/></port></ports>
<hostscript><script id="nbstat" output="NetBIOS name: DESKTOP-JQVC05U, NetBIOS user: &lt;unknown&gt;"/></hostscript></host></nmaprun>"""
        out = lan.parse_nmap(xml)
        self.assertEqual([p["port"] for p in out["ports"]], [443, 3389])
        self.assertEqual(out["certs"], [{"cn": "03919D540808722", "issuer": "BBL Technologies Co., Ltd"}])
        self.assertEqual((out["names"], out["titles"], out["os"]), (["DESKTOP-JQVC05U"], ["Printer login"], "Windows 11 (build 22631)"))
        self.assertEqual(lan.parse_nmap("<not xml")["ports"], [])
        self.assertEqual(lan.windows_from_build("10.0.19045"), "Windows 10 (build 19045)")


# --- what each device is ---------------------------------------------------------------------------------

def device(mac="02:00:00:00:00:01", ip="192.168.18.50", **obs):
    extra = {k: obs.pop(k) for k in ("maker", "ttl", "router", "via", "real_mac", "real_maker", "new_at") if k in obs}
    return {"key": mac, "mac": mac, "ip": ip, "ips": [ip], "first_seen": 0, "last_seen": 0, "obs": obs, **extra}


def mdns_obs(hosts=(), services=(), instances=None, txt_=None):
    return {"hosts": list(hosts), "services": list(services), "instances": instances or {}, "txt": txt_ or {}}


class IdentifyTests(unittest.TestCase):
    """Real devices, from what they said on the owner's network."""

    def check(self, d, label, kind, system="", model="", confidence=None, known=None):
        lan.identify(d, known or {}, now=100)
        self.assertEqual((d["label"], d["kind"], d["system"], d["model"]), (label, kind, system, model))
        if confidence:
            self.assertEqual(d["confidence"], confidence)
        self.assertTrue(d["evidence"])
        return d

    def test_roku(self):
        self.check(device(ROKU, "192.168.18.4", maker="Roku, Inc",
                          upnp={"friendlyName": "Roku Streaming Stick+", "manufacturer": "Roku", "modelName": "3810RW",
                                "modelNumber": "3810RW", "deviceType": "urn:roku-com:device:player:1-0"},
                          ssdp={"server": "Roku/16.0.4 UPnP/1.0 Roku/16.0.4"},
                          mdns=mdns_obs(["YH003S781286"], ["_airplay._tcp"], {"_airplay._tcp": ["Roku Streaming Stick "]},
                                        {"model": "3810RW", "manufacturer": "Roku"})),
                   "Roku Streaming Stick+", "TV streamer", "Roku OS 16.0.4", "Roku 3810RW", "exact")

    def test_bambu_printer(self):
        self.check(device("30:ed:a0:c5:ed:d0", "192.168.18.11", maker="Espressif Inc.",
                          ssdp={"devname.bambu.com": "JC printer", "devmodel.bambu.com": "N2S", "devversion.bambu.com": "01.08.01.00",
                                "devconnect.bambu.com": "cloud", "types": ["urn:bambulab-com:device:3dprinter:1"]}),
                   "JC printer", "3D printer", "Bambu firmware 01.08.01.00", "Bambu Lab A1", "exact")

    def test_a_mac_names_its_model_system_and_real_hardware_address(self):
        d = self.check(device("32:a8:db:61:45:82", "192.168.18.55", real_mac="7c:3b:2d:8f:ec:29", real_maker="Apple, Inc.",
                              mdns=mdns_obs(["HS-DJH2LMVT4M"], ["_workstation._tcp", "_rfb._tcp", "_ssh._tcp", "_device-info._tcp"],
                                            {"_workstation._tcp": ["HS-DJH2LMVT4M [7c:3b:2d:8f:ec:29]"], "_rfb._tcp": ["HS-DJH2LMVT4M"]},
                                            {"model": "Mac14,2", "osxvers": "25"})),
                       "HS-DJH2LMVT4M", "laptop", "macOS 26 Tahoe", "MacBook Air (M2, 2022) · Mac14,2", "exact")
        self.assertIn("Screen Sharing", " ".join(r["text"] for r in d["risks"]))

    def test_iphones(self):
        d = self.check(device(IPHONE, ttl=64, dhcp={"hostname": "iPhone", "prl": "1,121,3,6,15,119,252"}),
                       "iPhone", "phone", "iOS", "iPhone", "exact")
        self.assertEqual(d["name"], "")                                   # "iPhone" is what every iPhone sends
        self.check(device(IPHONE, mdns=mdns_obs(["Andrews-iPhone"], ["_airplay._tcp"], {"_airplay._tcp": ["LocalNetworkPrivacy"]})),
                   "Andrews-iPhone", "phone", "iOS", "iPhone")
        self.check(device(IPHONE, ports={"open": [49152, 62078]}), "iPhone or iPad", "iPhone or iPad", "iOS or iPadOS", "", "exact")

    def test_android_and_windows(self):
        self.check(device(dhcp={"hostname": "Galaxy-S24", "vendor": "android-dhcp-14"}), "Galaxy-S24", "phone or tablet", "Android 14")
        self.check(device("a0:b3:39:ae:e5:d4", maker="Intel Corporate", dhcp={"hostname": "DESKTOP-JQVC05U", "vendor": "MSFT 5.0"}),
                   "DESKTOP-JQVC05U", "Windows PC", "Windows")
        self.check(device(EXTENDER, "192.168.18.41", via=EXTENDER, ssdp={"agent": "Chromium/126.0.6478.183 Windows"}),
                   "Windows PC", "Windows PC", "Windows")

    def test_a_samba_nas_is_not_a_windows_pc(self):
        self.check(device("6c:1f:f7:a7:3c:16", "192.168.18.9", maker="Ugreen Group Limited", ports={"open": [80, 443, 445]},
                          netbios={"name": "DH4300PLUS-6D77", "os": "6.1", "comment": "DH4300PLUS-6D77"},
                          mdns=mdns_obs(["DH4300PLUS-6D77"], ["_device-info._tcp", "_smb._tcp"],
                                        {"_device-info._tcp": ["DH4300PLUS-6D77"], "_smb._tcp": ["DH4300PLUS-6D77"]}, {"model": "MacSamba"})),
                   "DH4300PLUS-6D77", "NAS", "Linux (Samba)", "UGREEN NASync DH4300 Plus")

    def test_devices_that_announce_little(self):
        self.check(device("4c:53:fd:4d:40:2e", maker="Amazon Technologies Inc.",
                          mdns=mdns_obs(["9C7427FEAF59", "linux"], ["_matter._tcp", "_spotify-connect._tcp"],
                                        {"_matter._tcp": ["798CC5793B59090F-04065634CD8F47B1"], "_spotify-connect._tcp": ["SpotifyConnect"]})),
                   "Amazon Echo", "smart speaker", "Fire OS", "Amazon Echo")
        self.check(device("9c:76:13:de:df:2b", maker="Ring LLC", ttl=255), "Ring camera or doorbell", "Ring camera or doorbell")
        self.check(device(ROUTER, GATEWAY, router=True, maker="Nokia Solutions and Networks GmbH & Co. KG", ports={"open": [22, 80, 443]}),
                   "router (Nokia)", "router", "", "", "exact")
        d = self.check(device("02:c1:53:5b:b2:34", ttl=64), "phone, tablet or laptop", "phone, tablet or laptop", "iOS or Android")
        self.assertEqual(d["confidence"], "guess")

    def test_your_name_wins_and_marks_it_yours(self):
        d = self.check(device(ROKU, maker="Roku, Inc", upnp={"friendlyName": "Roku Streaming Stick+"}), "Den TV", "TV streamer",
                       "Roku OS", "Roku", known={"name": "Den TV", "trusted": True})
        self.assertEqual(d["names"][0], {"name": "Den TV", "source": "you"})
        self.assertTrue(d["trusted"])

    def test_what_it_exposes(self):
        d = lan.identify(device(ports={"open": [23, 80, 5555]}), {}, now=100)
        levels = {r.get("port"): r["level"] for r in d["risks"] if r["what"] == "open"}
        self.assertEqual(levels, {23: "bad", 5555: "bad"})


# --- the watch: the device list over time, and its alerts ------------------------------------------------

class WatchTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        proc = Path(self.tmp.name) / "proc"
        (proc / "net").mkdir(parents=True)
        (proc / "net/route").write_text(ROUTE)
        (proc / "net/arp").write_text(ARP)
        self.proc = proc
        self.oui = Path(self.tmp.name) / "oui.hwdb"
        self.oui.write_text(OUI)
        self.swept, self.deep = [], []
        self.known = {"devices": {}}

    def tearDown(self):
        self.tmp.cleanup()

    def sweep(self, addresses):
        self.swept = addresses
        return {"192.168.18.4": 64, "192.168.18.59": 64}

    def look(self, state, now, answers=None, ports=None):
        answers = answers or {}

        def nmap(ip):
            self.deep.append(ip)
            return {"ports": [], "names": [], "certs": [], "titles": [], "os": "", "servers": []}
        original = lan.load_known
        lan.load_known = lambda path=None: self.known
        try:
            return lan.look(state, None, {}, now, self.proc, self.sweep, lambda ip: answers.get(ip, {}),
                            lambda ips: {ip: (ports or {}).get(ip, []) for ip in ips}, lambda loc, ip: {}, nmap,
                            [self.oui], MAC_ADDRESS)
        finally:
            lan.load_known = original

    def refresh(self, state, now):
        return lan.refresh(state, now, self.known)

    def test_a_look_sweeps_the_subnet_and_names_what_it_finds(self):
        roku = [("Roku Streaming Stick._airplay._tcp.local", lan.TXT, {"model": "3810RW", "manufacturer": "Roku"}),
                ("_airplay._tcp.local", lan.PTR, "Roku Streaming Stick._airplay._tcp.local")]
        ps4 = [("40.18.168.192.in-addr.arpa", lan.PTR, "PS4-067CCC.local")]
        state = self.look({}, 1000, {"192.168.18.4": {"mdns": roku}, "192.168.18.40": {"mdns": ps4}}, {"192.168.18.59": [62078]})
        self.assertEqual(len(self.swept), 253)                           # the /24 without the Mac itself
        devices = state["devices"]
        self.assertEqual((devices[ROKU]["label"], devices[ROKU]["model"]), ("Roku Streaming Stick", "Roku 3810RW"))
        self.assertEqual((devices[PS4]["label"], devices[PS4]["kind"]), ("PS4-067CCC", "game console"))
        self.assertEqual(devices[ROUTER]["kind"], "router")
        self.assertEqual(devices[IPHONE]["kind"], "iPhone or iPad")
        self.assertEqual(len(self.deep), lan.DEEP_PER_LOOK)              # nmap: a few devices per look
        self.assertEqual((state["network"], state["gateway_mac"]), ("192.168.18.0/24", ROUTER))
        self.assertEqual(state["alerts"], [])                            # the first half hour is the household

    def test_a_device_that_joins_later_is_flagged_until_you_trust_it(self):
        state = self.look({}, 1000)
        lan.observe(state, lan.parse_frame(dhcp_join("aa:bb:cc:00:00:01", b"Galaxy-S24", b"android-dhcp-14"))[0], 1000 + lan.BASELINE_S + 5)
        self.refresh(state, 1000 + lan.BASELINE_S + 10)
        titles = [a["title"] for a in state["alerts"]]
        self.assertEqual(titles, ["new device on your network: aa:bb:cc:00:00:01"])
        self.assertIn("Galaxy-S24", state["alerts"][0]["why"])
        self.assertEqual(state["counts"]["new"], 1)
        self.known["devices"]["aa:bb:cc:00:00:01"] = {"trusted": True}
        self.refresh(state, 1000 + lan.BASELINE_S + 20)
        self.assertEqual(state["alerts"], [])
        self.known["devices"] = {}
        self.refresh(state, 1000 + lan.BASELINE_S + lan.NEW_S + 10)       # a day later it is simply part of the network
        self.assertEqual(state["alerts"], [])

    def test_a_second_device_answering_as_the_router(self):
        state = self.look({}, 1000)
        lan.observe(state, lan.parse_frame(arp("de:ad:be:ef:00:01", GATEWAY))[0], 2000)
        self.refresh(state, 2001)
        self.assertEqual([a["level"] for a in state["alerts"]], ["bad"])
        self.assertEqual(state["alerts"][0]["title"], "another device is answering as your router")
        self.known["accept_router"] = "de:ad:be:ef:00:01"                 # sudo macserver devices accept-router
        self.refresh(state, 2002)
        self.assertEqual((state["gateway_mac"], state["alerts"]), ("de:ad:be:ef:00:01", []))

    def test_the_mac_reaching_the_router_through_the_extender_is_not_spoofing(self):
        state = self.look({}, 1000)
        state["shared_macs"] = {EXTENDER: {"since": 1000, "ips": ["192.168.18.21", "192.168.18.41"], "last": 1000}}
        lan.observe(state, lan.parse_frame(arp(EXTENDER, GATEWAY))[0], 2000)
        self.refresh(state, 2001)
        self.assertEqual(state["alerts"], [])

    def test_a_rogue_dhcp_server(self):
        state = self.look({}, 1000)
        offer = eth("de:ad:be:ef:00:01", udp("192.168.18.66", "255.255.255.255", 67, 68,
                                              dhcp("aa:bb:cc:00:00:01", [(53, b"\x02"), (54, socket.inet_aton("192.168.18.66"))], op=2)))
        router_offer = eth(ROUTER, udp(GATEWAY, "255.255.255.255", 67, 68, dhcp("aa:bb:cc:00:00:01", [(53, b"\x02"), (54, socket.inet_aton(GATEWAY))], op=2)))
        for frame in (router_offer, offer):
            lan.observe(state, lan.parse_frame(frame)[0], 2000)
        self.refresh(state, 2001)
        self.assertEqual([a["title"] for a in state["alerts"]], ["a second DHCP server is on your network (192.168.18.66)"])

    def test_an_extender_that_shares_one_address_is_split_by_ip(self):
        (self.proc / "net/arp").write_text(ARP + f"192.168.18.21    0x1  0x2  {EXTENDER}  *  wlp2s0\n"
                                                 f"192.168.18.41    0x1  0x2  {EXTENDER}  *  wlp2s0\n")
        state = {"devices": {}, "baseline_until": 0}
        mixed = {"mdns": {"services": ["_matter._tcp", "_spotify-connect._tcp"], "at": 400}}  # an Echo's, heard as the extender
        state["devices"][EXTENDER] = {"key": EXTENDER, "mac": EXTENDER, "ip": "192.168.18.21", "ips": ["192.168.18.21"], "obs": mixed,
                                      "first_seen": 500, "last_seen": 500, "baseline": True}      # known before as one device
        self.sweep = lambda addresses: {"192.168.18.21": 64, "192.168.18.41": 128}
        state = self.look(state, 1000)
        keys = sorted(k for k in state["devices"] if k.startswith(EXTENDER))
        self.assertEqual(keys, [f"{EXTENDER}@192.168.18.21", f"{EXTENDER}@192.168.18.41"])
        for key in keys:
            self.assertTrue(state["devices"][key]["baseline"])           # still the household, not two new devices
            self.assertEqual(state["devices"][key]["via"], EXTENDER)
            self.assertNotIn("_matter._tcp", state["devices"][key]["obs"].get("mdns", {}).get("services", []))  # no one inherits it
            self.assertNotIn("Echo", state["devices"][key]["kind"])
        self.assertFalse([a for a in state["alerts"] if EXTENDER in a["title"]])

    def test_the_capture_spots_an_extender_by_its_alternating_addresses(self):
        state, recent = {"devices": {}, "baseline_until": 10 ** 10}, {}
        for n, ip in enumerate(["192.168.18.21", "192.168.18.41", "192.168.18.21"]):
            lan.observe(state, lan.parse_frame(arp(EXTENDER, ip))[0], 100 + n, recent)
        self.assertIn(EXTENDER, state["shared_macs"])
        state, recent = {"devices": {}, "baseline_until": 10 ** 10}, {}
        for n, ip in enumerate(["192.168.18.21", "192.168.18.44", "192.168.18.44"]):  # a phone that changed address
            lan.observe(state, lan.parse_frame(arp(IPHONE, ip))[0], 100 + n, recent)
        self.assertNotIn("shared_macs", state)

    def test_a_sleep_proxy_answering_for_a_mac_does_not_move_it(self):
        state = self.look({}, 1000)
        mac_air = lan.ensure(state, "c6:be:44:e8:c0:14", "192.168.18.60", 1000)
        records = [("_workstation._tcp.local", lan.PTR, lan.encode_name("HS-HXJKD3S0265D [10:b5:88:4f:d2:1c]._workstation._tcp.local")),
                   ("HS-HXJKD3S0265D [10:b5:88:4f:d2:1c]._workstation._tcp.local", lan.SRV, srv("HS-HXJKD3S0265D.local")),
                   ("HS-HXJKD3S0265D.local", lan.A, socket.inet_aton("192.168.18.60"))]
        for ob in lan.parse_frame(mdns_frame(ROKU, "192.168.18.4", records)):          # the Roku answers for the sleeping Mac
            lan.observe(state, ob, 1100)
        self.refresh(state, 1101)
        self.assertEqual((state["devices"][ROKU]["ip"], mac_air["label"]), ("192.168.18.4", "HS-HXJKD3S0265D"))

    def test_flooding_and_forgetting(self):
        state = self.look({}, 1000)
        original = lan.MAX_DEVICES
        lan.MAX_DEVICES = len(state["devices"]) + 2
        try:
            for n in range(10):
                lan.ensure(state, f"02:00:00:00:00:{n:02x}", f"192.168.18.{100 + n}", 1001)
        finally:
            lan.MAX_DEVICES = original
        self.refresh(state, 1002)
        self.assertIn("hundreds of hardware addresses appeared (MAC flooding)", [a["title"] for a in state["alerts"]])
        state["devices"]["00:11:22:33:44:55"] = {"key": "00:11:22:33:44:55", "mac": "00:11:22:33:44:55", "ip": "192.168.18.99",
                                                 "ips": [], "obs": {}, "last_seen": 1000 - lan.FORGET_S - 1}
        state = self.look(state, 2000)
        self.assertNotIn("00:11:22:33:44:55", state["devices"])
        self.assertEqual(state["devices"][PS4]["first_seen"], 1000)

    def test_a_list_from_the_first_version_becomes_the_household(self):
        old = {"devices": {ROKU: {"ip": "192.168.18.4", "first_seen": 5, "last_seen": 9, "maker": "Roku, Inc", "label": "Roku …b6:38"}}}
        state = lan.migrate(old, 1000)
        self.assertEqual(state["version"], 3)
        self.assertTrue(state["devices"][ROKU]["baseline"])
        self.assertEqual(state["devices"][ROKU]["first_seen"], 5)
        self.assertIs(lan.migrate(state, 2000), state)
        self.assertEqual(state["baseline_until"], 1000 + lan.BASELINE_S)

    def test_version_2_splits_forget_what_they_inherited(self):
        echo = {"mdns": {"services": ["_matter._tcp"]}, "nmap": {"at": 5}}
        old = {"version": 2, "baseline_until": 7, "devices": {
            f"{EXTENDER}@192.168.18.250": {"key": f"{EXTENDER}@192.168.18.250", "ip": "192.168.18.250", "obs": echo, "first_seen": 3},
            ROKU: {"key": ROKU, "ip": "192.168.18.4", "obs": {"ssdp": {"server": "Roku"}}, "first_seen": 4}}}
        state = lan.migrate(old, 1000)
        self.assertEqual((state["version"], state["baseline_until"]), (3, 7))
        self.assertEqual(state["devices"][f"{EXTENDER}@192.168.18.250"]["obs"], {})
        self.assertEqual(state["devices"][f"{EXTENDER}@192.168.18.250"]["first_seen"], 3)
        self.assertEqual(state["devices"][ROKU]["obs"], {"ssdp": {"server": "Roku"}})   # a device on its own address keeps everything

    def test_state_round_trip(self):
        path = Path(self.tmp.name) / "state" / "devices.json"
        lan.save({"devices": {"a": {"ip": "1"}}}, path)
        self.assertEqual(lan.load_json(path), {"devices": {"a": {"ip": "1"}}})
        path.write_text("garbage")
        self.assertEqual(lan.load_json(path), {})


class CommandTests(unittest.TestCase):
    def setUp(self):
        self.state = {"devices": {}, "baseline_until": 0, "iface": "wlp2s0", "network": "192.168.18.0/24", "looked": 1000}
        for d in (device(ROKU, "192.168.18.4", maker="Roku, Inc", upnp={"friendlyName": "Roku Streaming Stick+"}),
                  device(PS4, "192.168.18.40", mdns=mdns_obs(["PS4-067CCC"]))):
            self.state["devices"][d["key"]] = d
        lan.refresh(self.state, 1000, {"devices": {}})

    def test_find_by_address_hardware_address_or_name(self):
        self.assertEqual([d["mac"] for d in lan.find(self.state, "192.168.18.4")], [ROKU])
        self.assertEqual([d["mac"] for d in lan.find(self.state, PS4)], [PS4])
        self.assertEqual([d["mac"] for d in lan.find(self.state, "streaming")], [ROKU])
        self.assertEqual(lan.find(self.state, "nothing"), [])

    def test_the_list_shows_full_names_and_one_device_shows_everything(self):
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            lan.show(self.state, now=1000, width=200)
            lan.show(self.state, "192.168.18.4", now=1000)
        text = out.getvalue()
        for needle in ("Roku Streaming Stick+", "PS4-067CCC", "TV streamer", "names it gave:", "(UPnP description)",
                       "how it was identified", "sudo macserver devices name 192.168.18.4"):
            self.assertIn(needle, text)

    def test_trust_and_name_write_the_known_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "devices-known.json"
            with contextlib.redirect_stdout(io.StringIO()):
                self.assertEqual(lan.edit_known("192.168.18.4", lambda e: e.update(name="Den TV", trusted=True), self.state, path), 0)
                self.assertEqual(lan.edit_known("all", lambda e: e.update(trusted=True), self.state, path), 0)
                self.assertEqual(lan.edit_known("nothing", lambda e: e.update(trusted=True), self.state, path), 1)
            data = json.loads(path.read_text())
        self.assertEqual(data["devices"][ROKU]["name"], "Den TV")
        self.assertTrue(all(e["trusted"] for e in data["devices"].values()))
        self.assertEqual(len(data["devices"]), 2)


class FilesTests(unittest.TestCase):
    def test_the_watch_runs_sandboxed_with_only_the_packet_capability(self):
        service = (ROOT / "admin/macserver-devices.service").read_text(encoding="utf-8")
        for needle in ("DynamicUser=yes", "CapabilityBoundingSet=CAP_NET_RAW\n", "AmbientCapabilities=CAP_NET_RAW\n", "NoNewPrivileges=yes",
                       "ProtectSystem=strict", "ProtectHome=yes", "MemoryDenyWriteExecute=yes",
                       "RestrictAddressFamilies=AF_INET AF_INET6 AF_UNIX AF_PACKET AF_NETLINK", "StateDirectory=macserver-devices",
                       "lan_devices.py watch", "Restart=on-failure", "ExecReload=/bin/kill -HUP $MAINPID"):
            self.assertIn(needle, service)
        self.assertFalse((ROOT / "admin/macserver-devices.timer").exists())
        installer = (ROOT / "installer/steps/40-access.sh").read_text(encoding="utf-8")
        for needle in ("admin/lan_devices.py", "apt_quiet install --no-install-recommends nmap", "disable --now macserver-devices.timer",
                       "enable macserver-devices.service", "restart macserver-devices.service"):
            self.assertIn(needle, installer)
        cli = (ROOT / "macserver").read_text(encoding="utf-8")
        for needle in ("name|trust|untrust|accept-router)", "systemctl kill -s USR1 macserver-devices.service",
                       "systemctl reload macserver-devices.service"):
            self.assertIn(needle, cli)

    def test_it_never_listens(self):
        """AGENTS.md: nothing listens on the LAN. The one bind is the packet socket's, to an interface:
        it reads broadcasts, owns no port and answers nothing; the firewall is unchanged."""
        code = (ROOT / "admin/lan_devices.py").read_text(encoding="utf-8")
        self.assertEqual(code.count(".bind("), 1)
        self.assertIn("self.sock.bind((iface, 0))", code)
        self.assertIn("socket.AF_PACKET, socket.SOCK_RAW", code)
        self.assertNotIn(".listen(", code)
        self.assertNotIn("SO_REUSEPORT", code)
        firewall = (ROOT / "host/nftables.conf").read_text(encoding="utf-8")
        for port in ("5353", "137", "1900"):
            self.assertNotIn(port, firewall)

    def test_nmap_is_a_connect_scan_without_privileges(self):
        code = (ROOT / "admin/lan_devices.py").read_text(encoding="utf-8")
        for needle in ('"-sT"', '"--unprivileged"', '"--version-light"'):
            self.assertIn(needle, code)
        for flag in ('"-O"', '"-sS"', '"-sU"', '"--script", "vuln', "brute", "dos"):
            self.assertNotIn(flag, code)


if __name__ == "__main__":
    unittest.main()
