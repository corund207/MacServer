import importlib.util
from pathlib import Path
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("collect_status", ROOT / "admin/collect_status.py")
collector = importlib.util.module_from_spec(spec)
spec.loader.exec_module(collector)

NET = ("Inter-|   Receive                                                |  Transmit\n"
       " face |bytes    packets errs drop fifo frame compressed multicast|bytes    packets errs drop fifo colls carrier compressed\n"
       "    lo: 500 5 0 0 0 0 0 0 500 5 0 0 0 0 0 0\n"
       " wlp2s0: {rx} 10 0 0 0 0 0 0 {tx} 10 0 0 0 0 0 0\n"
       "veth12: 9 1 0 0 0 0 0 0 9 1 0 0 0 0 0 0\n")


def proc_stat(name, ticks, start, rss_pages, uid_file=None):
    # pid (name) S ppid pgrp session tty tpgid flags minflt cminflt majflt cmajflt utime stime ...
    fields = ["S", "1", "1", "1", "0", "-1", "0", "0", "0", "0", "0", str(ticks), "0", "0", "0", "20", "0", "3",
              "0", str(start), "0", str(rss_pages)]
    return f"{name} ({name}) " + " ".join(fields) + "\n"


def build(root, cpu, cores, rx, tx, sectors, procs):
    """A fake /proc and /sys under root."""
    proc, sysfs = root / "proc", root / "sys"
    (proc / "net").mkdir(parents=True, exist_ok=True)
    (sysfs / "block/nvme0n1").mkdir(parents=True, exist_ok=True)
    lines = [f"cpu  {cpu[0]} 0 {cpu[1]} {cpu[2]} 0 0 0 0 0 0"]
    lines += [f"cpu{i} {c[0]} 0 {c[1]} {c[2]} 0 0 0 0 0 0" for i, c in enumerate(cores)]
    (proc / "stat").write_text("\n".join(lines) + "\nctxt 1\n")
    (proc / "net/dev").write_text(NET.format(rx=rx, tx=tx))
    (proc / "diskstats").write_text(
        f" 259 0 nvme0n1 1 0 {sectors[0]} 0 1 0 {sectors[1]} 0 0 0 0\n"
        f" 259 1 nvme0n1p1 1 0 999999 0 1 0 999999 0 0 0 0\n"
        f"   7 0 loop0 1 0 999999 0 1 0 999999 0 0 0 0\n")
    (proc / "meminfo").write_text("MemTotal: 8000 kB\nMemAvailable: 3000 kB\nCached: 1000 kB\nBuffers: 100 kB\n"
                                  "SwapTotal: 2000 kB\nSwapFree: 1500 kB\n")
    for pid, (name, ticks, start, rss) in procs.items():
        (proc / str(pid)).mkdir(exist_ok=True)
        (proc / str(pid) / "stat").write_text(proc_stat(name, ticks, start, rss))
    return proc, sysfs


class LiveStatsTests(unittest.TestCase):
    def test_rates_from_two_readings(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            proc, sysfs = build(root, (100, 50, 850), [(50, 25, 425), (50, 25, 425)], 1000, 2000, (100, 200),
                                {10: ("busy", 100, 5, 100), 11: ("idle", 0, 6, 10)})
            first, reading = collector.live_stats({}, 1000.0, proc, sysfs, tick_hz=100, page_size=4096)
            self.assertEqual(first["cpu"]["total"], 0.0)          # no earlier reading: zeros, not garbage
            self.assertEqual(first["net"]["rx"], 0)
            build(root, (200, 100, 1000), [(100, 50, 500), (100, 50, 500)], 1000 + 4000, 2000 + 1000, (100 + 2048, 200 + 4096),
                  {10: ("busy", 300, 5, 100), 11: ("idle", 0, 6, 10)})
            second, _ = collector.live_stats(reading, 1002.0, proc, sysfs, tick_hz=100, page_size=4096)
            self.assertEqual(second["span_s"], 2.0)
            self.assertEqual(second["cpu"]["total"], 50.0)         # 150 busy of 300 ticks
            self.assertEqual(second["cpu"]["cores"], [50.0, 50.0])
            self.assertEqual(second["net"]["rx"], 2000)            # 4000 bytes in 2 s; lo and veth ignored
            self.assertEqual(second["net"]["tx"], 500)
            self.assertEqual(list(second["net"]["ifaces"]), ["wlp2s0"])
            self.assertEqual(second["disk_io"], {"read": 2048 * 512 // 2, "write": 4096 * 512 // 2})  # partitions and loops skipped
            self.assertEqual(second["mem"]["used"], 5000 * 1024)
            self.assertEqual(second["mem"]["cached"], 1000 * 1024)
            self.assertEqual(second["swap"], {"total": 2000 * 1024, "used": 500 * 1024})
            top = second["procs"][0]
            self.assertEqual((top["name"], top["cpu"]), ("busy", 100.0))   # 200 ticks / 100 Hz / 2 s
            self.assertEqual(top["rss"], 100 * 4096)
            self.assertNotIn("cmdline", top)                       # command lines can hold secrets
            self.assertEqual(second["tasks"]["total"], 2)

    def test_recycled_pid_is_not_a_cpu_spike(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            proc, sysfs = build(root, (0, 0, 100), [(0, 0, 100)], 0, 0, (0, 0), {10: ("old", 9000, 5, 1)})
            _, reading = collector.live_stats({}, 0.0, proc, sysfs, tick_hz=100, page_size=4096)
            build(root, (0, 0, 200), [(0, 0, 200)], 0, 0, (0, 0), {10: ("new", 100, 77, 1)})  # same pid, new start time
            stats, _ = collector.live_stats(reading, 2.0, proc, sysfs, tick_hz=100, page_size=4096)
            self.assertEqual(stats["procs"][0]["cpu"], 0.0)

    def test_process_name_with_parentheses(self):
        with tempfile.TemporaryDirectory() as tmp:
            proc, sysfs = build(Path(tmp), (0, 0, 1), [(0, 0, 1)], 0, 0, (0, 0), {})
            (proc / "5").mkdir()
            (proc / "5/stat").write_text("5 (my (odd) name) S 1 1 1 0 -1 0 0 0 0 0 1 0 0 0 20 0 3 0 1 0 1\n")
            stats, _ = collector.live_stats({}, 1.0, proc, sysfs, tick_hz=100, page_size=4096)
            self.assertEqual(stats["procs"][0]["name"], "my (odd) name")

    def test_process_list_has_cpu_hogs_and_memory_hogs(self):
        with tempfile.TemporaryDirectory() as tmp:
            procs = {100 + i: (f"p{i}", 0, 1, i) for i in range(30)}   # memory grows with i, cpu all zero
            proc, sysfs = build(Path(tmp), (0, 0, 1), [(0, 0, 1)], 0, 0, (0, 0), procs)
            stats, _ = collector.live_stats({}, 1.0, proc, sysfs, tick_hz=100, page_size=4096)
            names = [p["name"] for p in stats["procs"]]
            self.assertEqual(len(names), collector.LIVE_PROCS + collector.LIVE_PROCS_BY_MEM)
            self.assertIn("p29", names)                            # the biggest is always listed


if __name__ == "__main__":
    unittest.main()
