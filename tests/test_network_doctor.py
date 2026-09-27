import importlib.util
import io
from pathlib import Path
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("network_doctor", ROOT / "scripts/network_doctor.py")
doctor = importlib.util.module_from_spec(spec)
spec.loader.exec_module(doctor)

ROUTES = ("Iface\tDestination\tGateway\tFlags\tRefCnt\tUse\tMetric\tMask\n"
          "enx0\t0010A8C0\t00000000\t0001\t0\t0\t0\tF0FFFFFF\n"
          "enx0\t00000000\t010AA8C0\t0003\t0\t0\t0\t00000000\n")


def facts(**overrides):
    base = {"interfaces": [{"name": "enx0", "driver": "ipheth", "kind": "iPhone USB tethering",
                            "carrier": True}],
            "route": "enx0", "nameservers": [], "wifi_hardware": False,
            "wifi_firmware": False, "ip_ok": True, "dns_ok": True}
    base.update(overrides)
    return base


class ParsingTests(unittest.TestCase):
    def test_default_route_and_nameservers(self):
        self.assertEqual(doctor.default_route_interface(ROUTES), "enx0")
        self.assertIsNone(doctor.default_route_interface(ROUTES.splitlines()[0]))
        self.assertEqual(doctor.nameservers("# x\nnameserver 192.0.2.1\nsearch lan\n"),
                         ["192.0.2.1"])

    def test_classify(self):
        self.assertEqual(doctor.classify("ipheth", False), "iPhone USB tethering")
        self.assertEqual(doctor.classify("brcmfmac", True), "internal Broadcom Wi-Fi")
        self.assertEqual(doctor.classify("", False), "virtual")


class DiagnosisTests(unittest.TestCase):
    def test_verdicts(self):
        self.assertEqual(doctor.diagnose(facts())[0], "ok")
        verdict, steps = doctor.diagnose(facts(dns_ok=False))
        self.assertEqual(verdict, "dns-broken")
        self.assertIn("fix-dns", steps[-1])
        self.assertEqual(doctor.diagnose(facts(ip_ok=False, dns_ok=False))[0], "no-internet")
        self.assertEqual(doctor.diagnose(facts(route=None, interfaces=[]))[0], "no-network")

    def test_missing_wifi_firmware_is_reported(self):
        _verdict, steps = doctor.diagnose(facts(wifi_hardware=True))
        self.assertIn("firmware", steps[0])


class FixTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        (self.root / "etc/dhcp").mkdir(parents=True)
        (self.root / "sbin").mkdir()
        (self.root / "sbin/dhclient").write_text("")
        (self.root / "etc/resolv.conf").write_text("nameserver 192.0.2.1\n")
        (self.root / "etc/dhcp/dhclient.conf").write_text("request subnet-mask;")

    def tearDown(self):
        self.tmp.cleanup()

    def test_declined_confirmation_changes_nothing(self):
        code = doctor.apply_fix(self.root, ["9.9.9.9"], False, io.StringIO("no\n"))
        self.assertEqual(code, 1)
        self.assertEqual((self.root / "etc/resolv.conf").read_text(), "nameserver 192.0.2.1\n")
        self.assertEqual(len(list((self.root / "etc").glob("resolv.conf.macserver-*"))), 0)

    def test_fix_writes_override_with_backups_and_is_idempotent(self):
        self.assertEqual(doctor.apply_fix(self.root, ["9.9.9.9", "149.112.112.112"], False,
                                          io.StringIO("yes\n")), 0)
        resolv = (self.root / "etc/resolv.conf").read_text()
        self.assertIn("nameserver 9.9.9.9\nnameserver 149.112.112.112\n", resolv)
        dhclient = (self.root / "etc/dhcp/dhclient.conf").read_text()
        self.assertTrue(dhclient.startswith("request subnet-mask;\n"))
        self.assertIn("supersede domain-name-servers 9.9.9.9, 149.112.112.112;", dhclient)
        backup = next((self.root / "etc").glob("resolv.conf.macserver-*"))
        self.assertEqual(backup.read_text(), "nameserver 192.0.2.1\n")
        doctor.apply_fix(self.root, ["9.9.9.9"], True)
        self.assertEqual((self.root / "etc/dhcp/dhclient.conf").read_text().count(doctor.MARKER), 1)

    def test_dhcpcd_override(self):
        (self.root / "sbin/dhclient").unlink()
        (self.root / "usr/sbin").mkdir(parents=True)
        (self.root / "usr/sbin/dhcpcd").write_text("")
        doctor.apply_fix(self.root, ["9.9.9.9"], True)
        self.assertIn("static domain_name_servers=9.9.9.9",
                      (self.root / "etc/dhcpcd.conf").read_text())
        self.assertEqual(list((self.root / "etc").glob("dhcpcd.conf.macserver-*")), [])

    def test_rejects_invalid_nameserver_and_managed_resolv(self):
        with self.assertRaises(ValueError):
            doctor.apply_fix(self.root, ["dns.example"], True)
        (self.root / "etc/resolv.conf").unlink()
        (self.root / "etc/resolv.conf").symlink_to(self.root / "elsewhere")
        self.assertEqual(doctor.apply_fix(self.root, ["9.9.9.9"], True), 2)


if __name__ == "__main__":
    unittest.main()
