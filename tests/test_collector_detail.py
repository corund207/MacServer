"""The detail sections of status.json: one test per reader, fed text and fake /proc and /sys trees
copied from the real MacBook Air (so a format change on the Mac shows up here, not on the screen)."""
import importlib.util
import json
import sys
from pathlib import Path
import tempfile
import types
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "admin"))
spec = importlib.util.spec_from_file_location("collect_status", ROOT / "admin/collect_status.py")
collector = importlib.util.module_from_spec(spec)
spec.loader.exec_module(collector)


def put(root, rel, text):
    path = Path(root) / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text)
    return path


class SmallReaders(unittest.TestCase):
    def test_os_release_and_logins(self):
        self.assertEqual(collector.os_pretty('PRETTY_NAME="Debian GNU/Linux 13 (trixie)"\nID=debian\n'), "Debian GNU/Linux 13 (trixie)")
        who = ("admin    tailscaled   Nov  3 11:23 (someone@example.com@100.124.144.126)\n"
               "macserver-console seat0        Nov  3 11:22\n"
               "macserver-console tty1         Nov  3 11:22\n"
               "admin    pts/0        Nov  3 10:52 (100.124.144.126)\n")
        sessions = collector.logged_in(who)
        self.assertEqual([(s["user"], s["tty"]) for s in sessions],
                         [("admin", "tailscaled"), ("macserver-console", "tty1"), ("admin", "pts/0")])
        self.assertNotIn("100.124", json.dumps(sessions))      # where a login came from stays out of the file

    def test_cpu_static(self):
        cpuinfo = "".join(f"processor\t: {i}\nmodel name\t: Intel(R) Core(TM) i5-8210Y CPU @ 1.60GHz\nmicrocode\t: 0xfc\n"
                          f"physical id\t: 0\ncore id\t\t: {i // 2}\n\n" for i in range(4))
        with tempfile.TemporaryDirectory() as tmp:
            put(tmp, "devices/system/cpu/cpu0/cpufreq/scaling_governor", "powersave\n")
            put(tmp, "devices/system/cpu/cpu0/cpufreq/scaling_driver", "intel_pstate\n")
            put(tmp, "devices/system/cpu/cpu0/cpufreq/cpuinfo_min_freq", "400000\n")
            put(tmp, "devices/system/cpu/cpu0/cpufreq/cpuinfo_max_freq", "3600000\n")
            put(tmp, "devices/system/cpu/intel_pstate/no_turbo", "0\n")
            info = collector.cpu_static(cpuinfo, tmp)
        self.assertEqual((info["model"], info["cores"], info["threads"]), ("Intel Core i5-8210Y", 2, 4))
        self.assertEqual((info["min_mhz"], info["max_mhz"], info["governor"], info["driver"], info["turbo"]),
                         (400.0, 3600.0, "powersave", "intel_pstate", True))

    def test_pressure_stall_information(self):
        text = "some avg10=23.84 avg60=25.51 avg300=24.69 total=261854193164\nfull avg10=0.00 avg60=0.00 avg300=0.00 total=0\n"
        self.assertEqual(collector.pressure(text), {"some": {"avg10": 23.84, "avg60": 25.51, "avg300": 24.69},
                                                    "full": {"avg10": 0.0, "avg60": 0.0, "avg300": 0.0}})
        self.assertEqual(collector.pressure(""), {})
        with tempfile.TemporaryDirectory() as tmp:
            put(tmp, "cpu", text)
            self.assertEqual(list(collector.read_pressure(tmp)), ["cpu"])     # a kernel without io/memory PSI

    def test_memory_detail_and_swap(self):
        meminfo = ("MemTotal: 7998668 kB\nMemFree: 642300 kB\nMemAvailable: 3856912 kB\nBuffers: 43384 kB\nCached: 2897436 kB\n"
                   "SwapCached: 1000 kB\nAnonPages: 2686996 kB\nSlab: 1072632 kB\nSReclaimable: 837304 kB\nShmem: 200820 kB\n"
                   "Dirty: 5972 kB\nSwapTotal: 3999332 kB\nSwapFree: 3243092 kB\nCommitted_AS: 8670120 kB\nHugePages_Total: 0\n")
        m = collector.memory_detail(meminfo)
        self.assertEqual((m["total"], m["used"], m["free"]), (7998668 * 1024, (7998668 - 3856912) * 1024, 642300 * 1024))
        self.assertEqual((m["swap_total"], m["swap_used"], m["slab"], m["anon"]), (3999332 * 1024, 756240 * 1024, 1072632 * 1024, 2686996 * 1024))
        swaps = "Filename\t\t\t\tType\t\tSize\t\tUsed\t\tPriority\n/dev/zram0                              partition\t3999332\t\t732976\t\t100\n"
        self.assertEqual(collector.swap_devices(swaps), [{"name": "/dev/zram0", "kind": "partition", "size": 3999332 * 1024, "used": 732976 * 1024}])

    def test_kernel_facts(self):
        facts = collector.kernel_facts("5.83 6.00 5.86 13/1003 3797821\n", "3505\t0\t9223372036854775807\n", "79", "262144")
        self.assertEqual(facts, {"load": [5.83, 6.0, 5.86], "sched_running": 13, "sched_total": 1003, "files_open": 3505,
                                 "conntrack": {"count": 79, "max": 262144}})
        empty = collector.kernel_facts("", "", None, None)               # a kernel without conntrack or files
        self.assertEqual((empty["load"], empty["files_open"], empty["conntrack"]), ([], None, {"count": None, "max": None}))

    def test_tcp_stats(self):
        snmp = ("Tcp: RtoAlgorithm RtoMin CurrEstab ActiveOpens PassiveOpens AttemptFails EstabResets InSegs OutSegs RetransSegs InErrs\n"
                "Tcp: 1 200 4 352531 6842 83 359 6986810 7436231 2489 4\n")
        t = collector.tcp_stats(snmp, "sockets: used 300\nTCP: inuse 14 orphan 0 tw 9 alloc 17 mem 2\n")
        self.assertEqual((t["established"], t["retransmitted"], t["time_wait"], t["in_use"], t["resets"]), (4, 2489, 9, 14, 359))
        self.assertEqual(collector.tcp_stats("", "")["established"], None)

    def test_wifi_info(self):
        nmcli = "*:Café\\: guest:73:540 Mbit/s:5805 MHz:161:WPA2\n :Neighbour:40:130 Mbit/s:2412 MHz:1:WPA2\n"
        wireless = ("Inter-| sta-|   Quality        |   Discarded packets               | Missed | WE\n"
                    " face | tus | link level noise |  nwid  crypt   frag  retry   misc | beacon | 22\n"
                    "wlp2s0: 0000   63.  -47.  -256        0      0      0   3511      0        0\n")
        info = collector.wifi_info(nmcli, wireless, "wlp2s0")
        self.assertEqual((info["ssid"], info["signal_pct"], info["rate_mbps"], info["freq_mhz"], info["channel"], info["band"]),
                         ("Café: guest", 73, 540, 5805, 161, "5 GHz"))
        self.assertEqual((info["signal_dbm"], info["noise_dbm"]), (-47, None))               # -256 means the driver reports no noise floor
        self.assertIsNone(collector.wifi_info(" :Neighbour:40:130 Mbit/s:2412 MHz:1:WPA2\n"))     # not connected
        self.assertEqual(collector.split_nmcli("a\\:b:c\\\\d:e"), ["a:b", "c\\d", "e"])

    def test_interfaces_hide_the_container_noise(self):
        dev = ("Inter-|   Receive  |  Transmit\n face |bytes packets errs drop fifo frame compressed multicast|bytes packets errs drop fifo colls carrier compressed\n"
               "    lo: 500 5 0 0 0 0 0 0 500 5 0 0 0 0 0 0\n"
               " wlp2s0: 3412000000 9000 1 2 0 0 0 0 812000000 7000 3 4 0 0 0 0\n"
               "tailscale0: 95000000 100 0 0 0 0 0 0 61000000 90 0 0 0 0 0 0\n"
               "veth1: 10 1 0 0 0 0 0 0 20 2 0 0 0 0 0 0\nveth2: 30 3 0 0 0 0 0 0 40 4 0 0 0 0 0 0\n"
               "br-0e5b61fc7fe6: 1 1 0 0 0 0 0 0 1 1 0 0 0 0 0 0\n")
        with tempfile.TemporaryDirectory() as tmp:
            put(tmp, "class/net/wlp2s0/operstate", "up\n")
            put(tmp, "class/net/wlp2s0/mtu", "1500\n")
            put(tmp, "class/net/wlp2s0/speed", "-1\n")           # Wi-Fi has no fixed speed
            put(tmp, "class/net/wlp2s0/wireless/x", "")
            rows = collector.interface_stats(dev, tmp, {"wlp2s0": ["192.168.18.76/24"]})
        by = {r["name"]: r for r in rows}
        self.assertEqual(list(by), ["wlp2s0", "tailscale0", "br-0e5b61fc7fe6", "containers"])
        self.assertEqual((by["wlp2s0"]["kind"], by["wlp2s0"]["state"], by["wlp2s0"]["speed_mbps"], by["wlp2s0"]["addresses"]),
                         ("wifi", "up", None, ["192.168.18.76/24"]))
        self.assertEqual((by["wlp2s0"]["rx_errors"], by["wlp2s0"]["tx_drops"]), (1, 4))
        self.assertEqual((by["containers"]["count"], by["containers"]["rx_bytes"], by["containers"]["tx_bytes"]), (2, 40, 60))
        self.assertEqual((by["tailscale0"]["kind"], by["br-0e5b61fc7fe6"]["kind"]), ("tailscale", "bridge"))

    def test_ip_addresses_skip_link_local(self):
        text = json.dumps([{"ifname": "wlp2s0", "addr_info": [{"family": "inet", "local": "192.168.18.76", "prefixlen": 24, "scope": "global"},
                                                              {"family": "inet6", "local": "fe80::1", "prefixlen": 64, "scope": "link"}]},
                           {"ifname": "enx1", "addr_info": []}])
        self.assertEqual(collector.ip_addresses(text), {"wlp2s0": ["192.168.18.76/24"]})
        self.assertEqual(collector.ip_addresses("not json"), {})


class StorageAndPower(unittest.TestCase):
    def test_filesystems_leave_out_overlays_and_duplicates(self):
        mounts = ("/dev/mapper/cryptroot / ext4 rw,relatime 0 0\n/dev/nvme0n1p2 /boot ext4 rw 0 0\n"
                  "/dev/nvme0n1p1 /boot/efi vfat rw 0 0\noverlay /var/lib/docker/overlay2/x/merged overlay rw 0 0\n"
                  "tmpfs /run tmpfs rw 0 0\n/dev/mapper/cryptroot /var/lib/docker/volumes ext4 rw 0 0\n"
                  "/dev/sdb1 /mnt/with\\040space ext4 ro,noatime 0 0\n")

        def fake(mount):
            if mount == "/mnt/with space":
                raise OSError("gone")                      # a disk that vanished mid-read is skipped, not fatal
            return types.SimpleNamespace(f_blocks=1000, f_frsize=4096, f_bfree=400, f_bavail=300, f_files=100, f_ffree=40)
        rows = collector.filesystems(mounts, fake)
        self.assertEqual([r["mount"] for r in rows], ["/", "/boot", "/boot/efi"])
        root = rows[0]
        self.assertEqual((root["total"], root["used"], root["avail"], root["inodes_used"], root["readonly"]),
                         (4096000, 600 * 4096, 300 * 4096, 60, False))

    def test_block_devices_and_nvme_state(self):
        with tempfile.TemporaryDirectory() as tmp:
            put(tmp, "block/nvme0n1/size", "490234752\n")
            put(tmp, "block/nvme0n1/queue/rotational", "0\n")
            put(tmp, "block/nvme0n1/nvme0n1p1/partition", "1\n")
            put(tmp, "block/nvme0n1/nvme0n1p3/partition", "3\n")
            put(tmp, "block/zram0/size", "7998664\n")          # swap in RAM: not a disk
            put(tmp, "class/nvme/nvme0/model", "APPLE SSD AP0256N\n")
            put(tmp, "class/nvme/nvme0/state", "live\n")
            put(tmp, "class/nvme/nvme0/firmware_rev", "1310\n")
            disks = collector.block_devices(tmp)
        self.assertEqual(len(disks), 1)
        d = disks[0]
        self.assertEqual((d["name"], d["model"], d["size"], d["ssd"], d["partitions"], d["state"], d["firmware"]),
                         ("nvme0n1", "APPLE SSD AP0256N", 490234752 * 512, True, 2, "live", "1310"))
        self.assertEqual(collector.block_devices("/nonexistent/path"), [])

    def test_sensors_and_the_t2_fans(self):
        with tempfile.TemporaryDirectory() as tmp:
            put(tmp, "hwmon/hwmon4/name", "coretemp\n")
            put(tmp, "hwmon/hwmon4/temp1_input", "89000\n")
            put(tmp, "hwmon/hwmon4/temp1_label", "Package id 0\n")
            put(tmp, "hwmon/hwmon4/temp1_max", "100000\n")
            put(tmp, "hwmon/hwmon4/temp1_crit", "100000\n")
            put(tmp, "hwmon/hwmon0/name", "nvme\n")
            put(tmp, "hwmon/hwmon0/temp1_input", "38850\n")
            put(tmp, "hwmon/hwmon9/name", "dead\n")
            put(tmp, "hwmon/hwmon9/temp1_input", "0\n")        # a sensor that reads zero is not a reading
            temps = collector.sensor_list(Path(tmp) / "hwmon")
            self.assertEqual([(t["chip"], t["label"], t["temp_c"], t["crit_c"]) for t in temps],
                             [("nvme", "temp1", 38.85, None), ("coretemp", "Package id 0", 89.0, 100.0)])
            fan = "acpi/APP0001_00/"      # (the real directory is APP0001:00; a colon is not allowed in Windows paths)
            for name, value in (("input", 7987), ("min", 2700), ("max", 8000), ("output", 8000)):
                put(tmp, f"{fan}fan1_{name}", f"{value}\n")
            self.assertEqual(collector.fan_list(Path(tmp) / "acpi/APP0001_00", Path(tmp) / "hwmon"),
                             [{"rpm": 7987, "min": 2700, "max": 8000, "target": 8000}])
            # The status page's own fan list now sees them too (it only looked in hwmon before).
            self.assertEqual(collector.hwmon(Path(tmp) / "hwmon", Path(tmp) / "acpi/APP0001_00")["fans_rpm"], [7987])

    def battery(self, tmp, status="Full", current=0, now=3853000):
        supply = "power_supply/BAT0/"
        for name, value in (("type", "Battery"), ("status", status), ("capacity", 88), ("cycle_count", 287),
                            ("voltage_now", 12667000), ("voltage_min_design", 11490000), ("current_now", current),
                            ("charge_full_design", 4381000), ("charge_full", 3970000), ("charge_now", now), ("temp", 375),
                            ("technology", "Li-ion")):
            put(tmp, supply + name, f"{value}\n")
        put(tmp, "power_supply/ADP1/type", "Mains\n")
        put(tmp, "power_supply/ADP1/online", "1\n")
        return collector.battery_detail(Path(tmp) / "power_supply")

    def test_battery_health_and_time_left(self):
        with tempfile.TemporaryDirectory() as tmp:
            b = self.battery(tmp)
            self.assertEqual((b["percent"], b["status"], b["ac_online"], b["cycles"], b["health_pct"], b["temp_c"]),
                             (88, "Full", True, 287, 90.6, 37.5))
            self.assertEqual((b["capacity_wh"], b["design_wh"]), (45.6, 50.3))
            self.assertIsNone(b["seconds_left"])                  # a full battery on the charger has no time left
        with tempfile.TemporaryDirectory() as tmp:
            self.assertEqual(self.battery(tmp, "Discharging", current=1_000_000)["seconds_left"], 13870)   # 3.853 Ah at 1 A
        with tempfile.TemporaryDirectory() as tmp:
            self.assertEqual(self.battery(tmp, "Charging", current=1_000_000)["seconds_left"], 421)        # 0.117 Ah to go
        self.assertIsNone(collector.battery_detail("/nonexistent"))


class DockerDatabaseTailscale(unittest.TestCase):
    def test_project_rollup(self):
        items = [{"project": "supabase", "state": "running", "status": "Up (healthy)", "cpu": 1.5, "mem": 100},
                 {"project": "supabase", "state": "running", "status": "Up (unhealthy)", "cpu": 0.5, "mem": 50},
                 {"project": "apps", "state": "exited", "status": "Exited (1)", "cpu": None, "mem": None}]
        rolled = {p["name"]: p for p in collector.project_rollup(items)}
        self.assertEqual((rolled["supabase"]["total"], rolled["supabase"]["running"], rolled["supabase"]["unhealthy"],
                          rolled["supabase"]["cpu"], rolled["supabase"]["mem"]), (2, 2, 1, 2.0, 150))
        self.assertEqual((rolled["apps"]["running"], rolled["apps"]["mem"]), (0, 0))

    def test_inspect_lines_and_docker_df(self):
        line = ("/macserver-gateway-cloudflared-1|2|2026-10-26T21:56:24.941492373Z||4095851|unless-stopped|"
                "cloudflare/cloudflared:2026.10.3@sha256:" + "f" * 64)
        info = collector.docker_inspect_lines(line + "\ngarbage\n")["macserver-gateway-cloudflared-1"]
        self.assertIsInstance(info["restarts"], int)
        self.assertEqual((info["restarts"], info["pid"], info["health"], info["policy"], info["image"]),
                         (2, 4095851, "", "unless-stopped", "cloudflare/cloudflared:2026.10.3"))
        self.assertEqual(info["started"], 1793051784)
        df = "\n".join(json.dumps(r) for r in (
            {"Active": "20", "Reclaimable": "2.959GB (19%)", "Size": "14.79GB", "TotalCount": "41", "Type": "Images"},
            {"Active": "5", "Reclaimable": "0B (0%)", "Size": "15.95GB", "TotalCount": "7", "Type": "Local Volumes"},
            {"Active": "0", "Reclaimable": "12.76GB", "Size": "18.22GB", "TotalCount": "267", "Type": "Build Cache"}))
        parsed = collector.docker_df(df)
        self.assertEqual((parsed["images"]["count"], parsed["images"]["size"], parsed["images"]["reclaimable"]),
                         (41, 14_790_000_000, 2_959_000_000))
        self.assertEqual(parsed["build_cache"]["reclaimable"], 12_760_000_000)

    def test_cgroup_extras(self):
        with tempfile.TemporaryDirectory() as tmp:
            scope = Path(tmp) / "system.slice/docker-abc.scope"
            def write(rbytes, wbytes):
                put(scope, "pids.current", "11\n")
                put(scope, "memory.max", "max\n")
                put(scope, "io.stat", f"253:0 rbytes={rbytes} wbytes={wbytes} rios=1 wios=1\n259:0 rbytes=100 wbytes=200 rios=1 wios=1\n")
            write(1000, 5000)
            first, reading = collector.cgroup_extras({"supabase-db": "abc", "gone": "zzz"}, {}, 100.0, Path(tmp))
            self.assertEqual(first["supabase-db"], {"pids": 11, "mem_limit": None, "blk_read": 0, "blk_write": 0})
            self.assertNotIn("gone", first)                       # a stopped container has no cgroup
            write(3000, 9000)
            second, _ = collector.cgroup_extras({"supabase-db": "abc"}, reading, 102.0, Path(tmp))
            self.assertEqual((second["supabase-db"]["blk_read"], second["supabase-db"]["blk_write"]), (1000, 2000))

    def test_container_network_totals(self):
        with tempfile.TemporaryDirectory() as tmp:
            put(tmp, "77/net/dev", "h1\nh2\n    lo: 9 1 0 0 0 0 0 0 9 1 0 0 0 0 0 0\n  eth0: 100 1 0 0 0 0 0 0 250 1 0 0 0 0 0 0\n  eth1: 5 1 0 0 0 0 0 0 7 1 0 0 0 0 0 0\n")
            self.assertEqual(collector.container_net(77, tmp), (105, 257))
            self.assertEqual(collector.container_net(99, tmp), (0, 0))

    def test_db_stats(self):
        raw = json.dumps({"version": "17.6", "size": 699_000_000, "connections": 9, "max_connections": 100, "commits": 900,
                          "rollbacks": 100, "blocks_hit": 990, "blocks_read": 10, "users": 42, "storage_objects": None})
        first, reading = collector.db_stats(raw, {}, 1000.0)
        self.assertEqual((first["tps"], first["cache_hit_pct"], first["users"]), (0.0, 99.0, 42))
        later = json.loads(raw)
        later["commits"] += 100
        second, _ = collector.db_stats(json.dumps(later), reading, 1010.0)
        self.assertEqual(second["tps"], 10.0)                      # 100 more transactions in 10 s
        error, kept = collector.db_stats("psql: could not connect", reading, 1020.0)
        self.assertEqual(error, {"error": "no answer from the database"})
        self.assertEqual(kept, reading)                            # a failed query does not reset the rate baseline
        self.assertIn("read_only", "default_transaction_read_only")  # (the query runs read-only: see system_info)
        self.assertNotIn("update ", collector.DB_QUERY.lower())
        self.assertEqual(collector.db_container({"supabase-db": "1", "supabase-auth": "2", "macserver-apps-db-1": "3"}), "supabase-db")

    def test_tailscale_detail(self):
        status = json.dumps({
            "Version": "1.104.5-t788384763-gd9218e278", "Health": ["Tailscale can't reach the log server"],
            "CurrentTailnet": {"Name": "someone@example.com"},
            "Self": {"KeyExpiry": "2027-02-01T10:00:00Z", "HostName": "macserver"},
            "Peer": {"k1": {"HostName": "jonahs-iphone", "OS": "iOS", "Online": True, "TailscaleIPs": ["100.1.1.1"], "CurAddr": "203.0.113.9:41641",
                            "RxBytes": 10, "TxBytes": 20, "LastSeen": "2026-11-03T11:00:00Z"},
                     "k2": {"HostName": "desktop-old", "OS": "windows", "Online": False, "TailscaleIPs": ["100.1.1.2"],
                            "LastSeen": "0001-01-01T00:00:00Z"},
                     "k3": {"HostName": "laptop", "OS": "windows", "Online": True, "TailscaleIPs": ["100.1.1.3"], "Relay": "nyc"},
                     "f1": {"HostName": "funnel-ingress-node", "Online": True, "TailscaleIPs": ["100.1.1.9"], "Relay": "par"},
                     "f2": {"HostName": "funnel-ingress-node", "Online": True, "TailscaleIPs": ["100.1.1.10"], "Relay": "lhr"}}})
        d = collector.tailscale_detail(status, 0)
        self.assertEqual((d["version"], d["tailnet"], d["key_expiry"], len(d["health"])), ("1.104.5", "someone@example.com", 1801476000, 1))
        self.assertEqual([(p["name"], p["route"]) for p in d["peers"]],
                         [("jonahs-iphone", "direct"), ("laptop", "relay nyc"), ("desktop-old", "offline")])   # online first
        self.assertIsNone(d["peers"][2]["last_seen"])              # "never" is not 1 January of year 1
        self.assertNotIn("203.0.113", json.dumps(d))               # a peer's real address is not stored
        self.assertEqual(d["funnel_relays"], 2)                    # Tailscale's own relays are counted, not listed
        self.assertEqual(collector.tailscale(status)["peers_online"], 2)   # ...and are not "devices online"
        self.assertEqual(collector.tailscale_detail("not json", 0), {})


class LiveDetail(unittest.TestCase):
    def build(self, root, user, system, iowait, idle, io_ms, ctxt, swapout):
        put(root, "proc/stat", f"cpu  {user} 0 {system} {idle} {iowait} 0 0 0 0 0\ncpu0 {user} 0 {system} {idle} {iowait} 0 0 0 0 0\n"
                               f"intr {ctxt * 2} 1 2\nctxt {ctxt}\nprocesses {ctxt // 10}\nprocs_running 3\nprocs_blocked 1\n")
        put(root, "proc/diskstats", f" 259 0 nvme0n1 100 0 {io_ms * 2} 0 50 0 {io_ms * 4} 0 0 {io_ms} 0\n")
        put(root, "proc/vmstat", f"pswpin 0\npswpout {swapout}\npgmajfault {swapout * 2}\noom_kill 0\n")
        put(root, "proc/net/dev", "h1\nh2\n")
        put(root, "proc/meminfo", "MemTotal: 8000 kB\nMemAvailable: 3000 kB\n")
        put(root, "sys/block/nvme0n1/size", "1\n")

    def test_cpu_breakdown_disk_busy_and_kernel_rates(self):
        with tempfile.TemporaryDirectory() as tmp:
            self.build(tmp, user=100, system=50, iowait=10, idle=840, io_ms=100, ctxt=1000, swapout=10)
            first, reading = collector.live_stats({}, 1000.0, f"{tmp}/proc", f"{tmp}/sys", tick_hz=100, page_size=4096)
            self.assertEqual(first["cpu"]["times"], {})            # no earlier reading: nothing invented
            self.assertEqual(first["disks"]["nvme0n1"]["busy"], 0.0)
            self.build(tmp, user=300, system=150, iowait=50, idle=1500, io_ms=1100, ctxt=3000, swapout=110)
            second, _ = collector.live_stats(reading, 1002.0, f"{tmp}/proc", f"{tmp}/sys", tick_hz=100, page_size=4096)
        # Over 2 s the CPU spent 200 user, 100 system, 40 iowait and 660 idle ticks of 1000.
        self.assertEqual(second["cpu"]["times"], {"user": 20.0, "system": 10.0, "iowait": 4.0, "irq": 0.0, "steal": 0.0})
        d = second["disks"]["nvme0n1"]
        self.assertEqual(d["busy"], 50.0)                          # 1000 ms busy in 2 s
        self.assertEqual((d["read"], d["write"]), (2000 * 512 // 2, 4000 * 512 // 2))
        self.assertEqual(second["rates"]["ctxt"], 1000)            # 2000 switches in 2 s
        self.assertEqual(second["rates"]["intr"], 2000)
        self.assertEqual(second["rates"]["swap_out"], 100 * 4096 // 2)
        self.assertEqual(second["rates"]["major_faults"], 100)
        self.assertEqual(second["run_queue"], {"running": 3, "blocked": 1})

    def test_system_info_is_whole_even_when_most_sources_are_missing(self):
        """On a machine with nothing to read (the dev PC) every section degrades to None, never an exception."""
        with tempfile.TemporaryDirectory() as tmp:
            info = collector.system_info({}, 1000.0, [{"name": "x", "state": "running", "status": "Up", "project": "p"}],
                                         {}, "", proc_root=f"{tmp}/proc", sys_root=f"{tmp}/sys")
        for key in ("host", "cpu", "memory", "storage", "network", "sensors", "power", "docker", "tailscale", "db"):
            self.assertIn(key, info)
        self.assertIsNone(info["power"])
        self.assertEqual(info["docker"]["projects"][0]["name"], "p")
        json.dumps(info)                                           # and it all serialises


if __name__ == "__main__":
    unittest.main()
