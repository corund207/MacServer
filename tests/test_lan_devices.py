"""admin/lan_devices.py: naming the devices on the local network. No network needed: packets are built
by hand, the sweep and the probes are stand-ins, and the answers are the ones real devices gave."""
import importlib.util
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
       "192.168.18.40    0x1         0x2         a0:b3:39:06:7c:cc     *        wlp2s0\n"
       "192.168.18.59    0x1         0x2         b6:33:93:f6:8c:a9     *        wlp2s0\n"
       "192.168.18.77    0x1         0x0         00:00:00:00:00:00     *        wlp2s0\n"
       "172.18.0.8       0x1         0x2         46:f9:62:08:92:a8     *        br-0e5b\n")
OUI = ("OUI:E01F2B*\n ID_OUI_FROM_DATABASE=Nokia Solutions and Networks GmbH & Co. KG\n\n"
       "OUI:D83134*\n ID_OUI_FROM_DATABASE=Roku, Inc\n\n"
       "OUI:A0B339*\n ID_OUI_FROM_DATABASE=Intel Corporate\n\n"
       "OUI:A0B339067*\n ID_OUI_FROM_DATABASE=Longer Prefix Wins Ltd\n\n")


MAC_ADDRESS = lambda gateway: "192.168.18.33"          # this Mac, whatever network the test runs on


def answer(qid, records):
    """A DNS answer message: records = [(name, type, rdata bytes)]."""
    body = b"".join(lan.encode_name(n) + struct.pack("!HHIH", t, 1, 120, len(r)) + r for n, t, r in records)
    return struct.pack("!HHHHHH", qid, 0x8400, 0, len(records), 0, 0) + body


def txt(**items):
    return b"".join(bytes([len(f"{k}={v}")]) + f"{k}={v}".encode() for k, v in items.items())


class NetworkTests(unittest.TestCase):
    def test_the_default_route_gives_the_interface_router_and_subnet(self):
        iface, gateway, net = lan.default_route(ROUTE)
        self.assertEqual((iface, gateway, str(net)), ("wlp2s0", "192.168.18.1", "192.168.18.0/24"))
        self.assertIsNone(lan.default_route("Iface\tDestination\n"))

    def test_only_complete_entries_on_the_interface(self):
        self.assertEqual(lan.neighbours(ARP, "wlp2s0"), {"192.168.18.1": "e0:1f:2b:38:b7:30", "192.168.18.4": "d8:31:34:67:b6:38",
                                                         "192.168.18.40": "a0:b3:39:06:7c:cc", "192.168.18.59": "b6:33:93:f6:8c:a9"})

    def test_makers_from_the_registry_and_none_for_private_addresses(self):
        with tempfile.TemporaryDirectory() as tmp:
            oui = Path(tmp) / "20-OUI.hwdb"
            oui.write_text(OUI)
            found = lan.makers(["e0:1f:2b:38:b7:30", "d8:31:34:67:b6:38", "a0:b3:39:06:7c:cc", "b6:33:93:f6:8c:a9"], [oui])
        self.assertEqual(found, {"e0:1f:2b:38:b7:30": "Nokia Solutions and Networks GmbH & Co. KG", "d8:31:34:67:b6:38": "Roku, Inc",
                                 "a0:b3:39:06:7c:cc": "Longer Prefix Wins Ltd"})
        self.assertTrue(lan.private_mac("b6:33:93:f6:8c:a9"))
        self.assertFalse(lan.private_mac("d8:31:34:67:b6:38"))
        self.assertEqual(lan.system_from_ttl(64), "unix")
        self.assertEqual(lan.system_from_ttl(128), "windows")
        self.assertEqual(lan.system_from_ttl(255), "network")


class MessageTests(unittest.TestCase):
    def test_answers_with_compression_ptr_txt_and_a(self):
        name = lan.encode_name("_airplay._tcp.local")
        msg = answer(7, [("_airplay._tcp.local", lan.PTR, b"\x14Roku Streaming Stick" + b"\xc0\x0c"),
                         ("Roku Streaming Stick._airplay._tcp.local", lan.TXT, txt(model="3810RW", fn="Den")),
                         ("roku.local", lan.A, bytes([192, 168, 18, 4]))])
        self.assertTrue(name)
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
        self.assertEqual(lan.real_name("none"), "")
        self.assertEqual(lan.real_name("6f8a5bec-49da-562d-9289-9fdecce0ac05"), "")
        self.assertEqual(lan.real_name("YH003S781286"), "")                       # a serial number
        self.assertEqual(lan.real_name("PS4-067CCC"), "PS4-067CCC")


class WhatItIsTests(unittest.TestCase):
    """The answers real devices gave on the owner's network (2026-11-26), and what they are."""
    CASES = [
        ({"router": True, "maker": "Nokia Solutions and Networks"}, ("router", "")),
        ({"txt": {"model": "iPhone15,2"}, "services": ["_companion-link._tcp"]}, ("iPhone", "iOS")),
        ({"txt": {"model": "MacBookAir8,2"}}, ("Mac", "macOS")),
        ({"txt": {"model": "AppleTV11,1"}}, ("Apple TV", "tvOS")),
        ({"txt": {"model": "MacSamba"}, "services": ["_device-info._tcp", "_nut._tcp", "_smb._tcp"],
          "maker": "Ugreen Group Limited", "host": "DH4300PLUS-6D77"}, ("NAS", "Linux")),
        ({"host": "PS4-067CCC", "services": ["_spotify-connect._tcp"], "maker": "AzureWave Technology Inc.", "ttl": 64},
         ("PlayStation 4", "PlayStation")),
        ({"services": ["_airplay._tcp", "_spotify-connect._tcp"], "txt": {"model": "3810RW"}, "maker": "Roku, Inc"}, ("Roku", "Roku OS")),
        ({"services": ["_matter._tcp"], "maker": "Amazon Technologies Inc."}, ("Amazon Echo", "Fire OS")),
        ({"services": ["_googlecast._tcp"], "maker": "Google, Inc."}, ("Chromecast / Google TV", "Google TV")),
        ({"netbios": "DESKTOP-JQVC05U", "ttl": 128, "maker": "Intel Corporate"}, ("Windows PC", "Windows")),
        ({"maker": "Intel Corporate", "ttl": 128}, ("Windows PC", "Windows")),
        ({"maker": "Ring LLC", "ttl": 255}, ("Ring camera", "")),
        ({"maker": "Aura Home, Inc.", "ttl": 64}, ("photo frame", "")),
        ({"mac": "b6:33:93:f6:8c:a9", "ttl": 64}, ("phone or tablet", "iOS or Android")),
        ({"mac": "02:c1:53:5b:b2:34"}, ("phone or laptop", "")),
        ({"mac": "d8:31:34:67:b6:38"}, ("device", "")),
    ]

    def test_kind_and_system(self):
        for device, expected in self.CASES:
            with self.subTest(device=device):
                self.assertEqual(lan.classify(device), expected)

    def test_the_friendliest_name_wins(self):
        self.assertEqual(lan.display_name({"names": {"_airplay._tcp": "Roku Streaming Stick",
                                                     "_spotify-connect._tcp": "6f8a5bec-49da-562d-9289-9fdecce0ac05"},
                                           "host": "YH003S781286"}), "Roku Streaming Stick")
        self.assertEqual(lan.display_name({"names": {"_spotify-connect._tcp": "SpZc-067CCC"}, "host": "PS4-067CCC"}), "PS4-067CCC")
        self.assertEqual(lan.display_name({"names": {"_raop._tcp": "A1B2C3D4E5F6@Kitchen"}}), "Kitchen")
        self.assertEqual(lan.display_name({"host": "none", "names": {"_spotify-connect._tcp": "SpotifyConnect #2"}}), "SpotifyConnect #2")
        self.assertEqual(lan.label({"mac": "60:92:c8:9a:06:1e", "maker": "Roku, Inc"}), "Roku …06:1e")
        self.assertEqual(lan.label({"mac": "b6:33:93:f6:8c:a9"}), "private …8c:a9")
        self.assertEqual(lan.label({"mac": "e0:1f:2b:38:b7:30", "router": True, "maker": "Nokia Solutions and Networks"}), "router (Nokia)")


class ScanTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        proc = Path(self.tmp.name) / "proc"
        (proc / "net").mkdir(parents=True)
        (proc / "net/route").write_text(ROUTE)
        (proc / "net/arp").write_text(ARP)
        self.proc = proc
        self.oui = Path(self.tmp.name) / "oui.hwdb"
        self.oui.write_text(OUI)
        self.swept = []

    def tearDown(self):
        self.tmp.cleanup()

    def sweep(self, addresses):
        self.swept = addresses
        return {"192.168.18.4": 64, "192.168.18.59": 64}

    def test_a_scan_sweeps_the_subnet_and_names_what_it_finds(self):
        answers = {"192.168.18.4": {"names": {"_airplay._tcp": "Roku Streaming Stick"}, "services": ["_airplay._tcp"]},
                   "192.168.18.40": {"host": "PS4-067CCC"}}
        state = lan.scan({}, now=1000, proc=self.proc, sweep_fn=self.sweep,
                         probe_fn=lambda ip, gw: answers.get(ip, {}), oui_files=[self.oui], own_fn=MAC_ADDRESS)
        self.assertEqual(len(self.swept), 253)                           # the /24 without the Mac itself
        devices = state["devices"]
        self.assertEqual(devices["d8:31:34:67:b6:38"]["label"], "Roku Streaming Stick")
        self.assertEqual(devices["a0:b3:39:06:7c:cc"]["kind"], "PlayStation 4")
        self.assertEqual(devices["e0:1f:2b:38:b7:30"]["kind"], "router")
        self.assertEqual(devices["b6:33:93:f6:8c:a9"]["kind"], "phone or tablet")
        self.assertEqual(state["network"], "192.168.18.0/24")

    def test_a_sleeping_device_keeps_its_name_and_a_long_gone_one_is_forgotten(self):
        state = lan.scan({}, now=1000, proc=self.proc, sweep_fn=self.sweep,
                         probe_fn=lambda ip, gw: {"host": "PS4-067CCC"} if ip == "192.168.18.40" else {}, oui_files=[self.oui], own_fn=MAC_ADDRESS)
        state["devices"]["00:11:22:33:44:55"] = {"mac": "00:11:22:33:44:55", "ip": "192.168.18.99", "last_seen": 1000 - lan.FORGET_S - 1}
        state = lan.scan(state, now=2000, proc=self.proc, sweep_fn=self.sweep, probe_fn=lambda ip, gw: {}, oui_files=[self.oui], own_fn=MAC_ADDRESS)
        self.assertEqual(state["devices"]["a0:b3:39:06:7c:cc"]["label"], "PS4-067CCC")
        self.assertEqual(state["devices"]["a0:b3:39:06:7c:cc"]["first_seen"], 1000)
        self.assertNotIn("00:11:22:33:44:55", state["devices"])

    def test_a_device_with_several_addresses_is_one_device_at_the_address_that_answered(self):
        (self.proc / "net/arp").write_text(ARP + "192.168.18.41    0x1  0x2  b6:33:93:f6:8c:a9  *  wlp2s0\n"
                                                 "192.168.18.250   0x1  0x2  b6:33:93:f6:8c:a9  *  wlp2s0\n")
        answers = {"192.168.18.41": {"names": {"_spotify-connect._tcp": "Kitchen Speaker"}, "services": ["_spotify-connect._tcp"]}}
        state = lan.scan({}, now=1000, proc=self.proc, sweep_fn=self.sweep,
                         probe_fn=lambda ip, gw: answers.get(ip, {}), oui_files=[self.oui], own_fn=MAC_ADDRESS)
        d = state["devices"]["b6:33:93:f6:8c:a9"]
        self.assertEqual((d["ip"], d["label"], d["ttl"]), ("192.168.18.41", "Kitchen Speaker", 64))
        self.assertEqual(sorted(d["ips"]), ["192.168.18.250", "192.168.18.41", "192.168.18.59"])

    def test_state_round_trip(self):
        path = Path(self.tmp.name) / "state" / "devices.json"
        lan.save({"devices": {"a": {"ip": "1"}}}, path)
        self.assertEqual(lan.load(path), {"devices": {"a": {"ip": "1"}}})
        path.write_text("garbage")
        self.assertEqual(lan.load(path), {})


class FilesTests(unittest.TestCase):
    def test_the_scan_runs_sandboxed_every_ten_minutes_and_is_installed(self):
        service = (ROOT / "admin/macserver-devices.service").read_text(encoding="utf-8")
        for needle in ("DynamicUser=yes", "CapabilityBoundingSet=\n", "NoNewPrivileges=yes", "ProtectSystem=strict",
                       "RestrictAddressFamilies=AF_INET AF_UNIX", "StateDirectory=macserver-devices", "lan_devices.py scan"):
            self.assertIn(needle, service)
        self.assertIn("OnUnitActiveSec=10min", (ROOT / "admin/macserver-devices.timer").read_text(encoding="utf-8"))
        installer = (ROOT / "installer/steps/40-access.sh").read_text(encoding="utf-8")
        for needle in ("admin/lan_devices.py", "macserver-devices.timer", "macserver-devices.service"):
            self.assertIn(needle, installer)

    def test_it_never_listens(self):
        """AGENTS.md: nothing listens on the LAN. The scan only sends and reads the replies."""
        code = (ROOT / "admin/lan_devices.py").read_text(encoding="utf-8")
        self.assertNotIn(".bind(", code)
        self.assertNotIn(".listen(", code)
        self.assertNotIn("224.0.0.251", code)                          # no multicast: every question is unicast
        firewall = (ROOT / "host/nftables.conf").read_text(encoding="utf-8")
        self.assertNotIn("5353", firewall)
        self.assertNotIn("137", firewall)


if __name__ == "__main__":
    unittest.main()
