import importlib.util
import json
import os
from pathlib import Path
import tempfile
import time
import unittest

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("collect_status", ROOT / "admin/collect_status.py")
collector = importlib.util.module_from_spec(spec)
spec.loader.exec_module(collector)


class ParserTests(unittest.TestCase):
    def test_env_parsing_ignores_comments(self):
        env = collector.parse_env("# ANON_KEY=nope\nANON_KEY=abc=def\nEMPTY=\n")
        self.assertEqual(env["ANON_KEY"], "abc=def")
        self.assertEqual(env["EMPTY"], "")

    def test_memory(self):
        mem = collector.memory("MemTotal:  8000 kB\nMemAvailable: 3000 kB\n")
        self.assertEqual(mem, {"total": 8000 * 1024, "used": 5000 * 1024})

    def test_containers(self):
        line = json.dumps({"Names": "supabase-db", "State": "running", "Status": "Up 2 hours (healthy)",
                           "Labels": "com.docker.compose.project=supabase,x=y"})
        result = collector.containers(line + "\nnot json\n")
        self.assertEqual(result, [{"name": "supabase-db", "state": "running",
                                   "status": "Up 2 hours (healthy)", "project": "supabase"}])

    def test_claude_session(self):
        log = ("\x1b[2J\x1b[1mRemote Control\x1b[0m connecting...\r\n"
               "Session: \x1b[4mhttps://claude.ai/code/session_01AbC-x_9?env=1\x1b[0m\r\n")
        info = collector.claude_session(log, "active", True)
        self.assertEqual(info["url"], "https://claude.ai/code/session_01AbC-x_9?env=1")
        self.assertEqual(info["problem"], "")
        # The log outlives the session: no link once it stopped.
        self.assertEqual(collector.claude_session(log, "inactive", True)["url"], "")
        denied = "Error: You must be logged in to use Remote Control.\n"
        self.assertEqual(collector.claude_session(denied, "failed", True)["problem"], "login")
        self.assertEqual(collector.claude_session("Enable Remote Control? (y/n)", "active", True)["problem"],
                         "consent")
        self.assertEqual(collector.claude_session("", "", False),
                         {"installed": False, "state": "inactive", "url": "", "problem": ""})

    def test_container_stats(self):
        lines = "\n".join([json.dumps({"Name": "supabase-db", "CPUPerc": "3.50%", "MemUsage": "212.4MiB / 7.6GiB"}),
                           "junk", json.dumps({"Name": "supabase-auth", "CPUPerc": "--", "MemUsage": ""})])
        stats = collector.container_stats(lines)
        self.assertEqual(stats["supabase-db"], {"cpu": 3.5, "mem": int(212.4 * 1024 ** 2)})
        self.assertNotIn("supabase-auth", stats)
        merged = collector.with_stats([{"name": "supabase-db"}, {"name": "x"}], stats)
        self.assertEqual(merged[1], {"name": "x", "cpu": None, "mem": None})

    def test_container_ids(self):
        lines = "\n".join([json.dumps({"Names": "supabase-db", "ID": "f" * 64}), "junk",
                           json.dumps({"Names": "", "ID": "a"}), json.dumps({"Names": "x"})])
        self.assertEqual(collector.container_ids(lines), {"supabase-db": "f" * 64})

    def test_cgroup_stats(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            scope = root / "system.slice" / "docker-abc.scope"
            scope.mkdir(parents=True)

            def write(usage_usec, memory, inactive):
                (scope / "cpu.stat").write_text(f"usage_usec {usage_usec}\nuser_usec 1\nsystem_usec 1\n")
                (scope / "memory.current").write_text(f"{memory}\n")
                (scope / "memory.stat").write_text(f"file 9\ninactive_file {inactive}\n")

            ids = {"supabase-db": "abc", "stopped": "gone"}
            write(1_000_000, 300 * 1024 ** 2, 100 * 1024 ** 2)
            stats, sample = collector.cgroup_stats(ids, {}, 100.0, root)
            # First reading has nothing to compare with; stopped containers are left out.
            self.assertEqual(stats, {"supabase-db": {"cpu": 0.0, "mem": 200 * 1024 ** 2}})
            # 0.5 CPU-seconds over 2 s is 25% of one core.
            write(1_500_000, 300 * 1024 ** 2, 100 * 1024 ** 2)
            stats, sample = collector.cgroup_stats(ids, sample, 102.0, root)
            self.assertEqual(stats["supabase-db"]["cpu"], 25.0)
            # A counter that went backwards (container restarted) never gives a negative %.
            write(10, 1, 0)
            stats, _ = collector.cgroup_stats(ids, sample, 104.0, root)
            self.assertEqual(stats["supabase-db"]["cpu"], 0.0)

    def test_cached_reuses_slow_values(self):
        state, calls = {}, []

        def produce():
            calls.append(1)
            return len(calls)

        self.assertEqual(collector.cached(state, "k", 300, 1000.0, produce), 1)
        self.assertEqual(collector.cached(state, "k", 300, 1299.0, produce), 1)   # still fresh
        self.assertEqual(collector.cached(state, "k", 300, 1301.0, produce), 2)   # expired
        self.assertEqual(collector.cached(state, "k", 300, 1200.0, produce), 3)   # clock went backwards
        self.assertIs(collector.cached(state, "none", 300, 1.0, lambda: None), None)
        self.assertIs(collector.cached(state, "none", 300, 2.0, lambda: 1 / 0), None)   # cached None is a hit

    def test_state_round_trip_survives_garbage(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "state.json"
            self.assertEqual(collector.load_state(path), {})
            collector.save_state({"cgroup": {"at": 1.0}}, path)
            self.assertEqual(collector.load_state(path), {"cgroup": {"at": 1.0}})
            self.assertEqual(path.stat().st_mode & 0o777, 0o600)
            path.write_text("[1, 2")
            self.assertEqual(collector.load_state(path), {})
            path.write_text("[1]")
            self.assertEqual(collector.load_state(path), {})

    def test_logs_are_redacted(self):
        line = ('auth error: invalid token eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiIxIn0.abc password=hunter2 '
                'Authorization: Bearer sk_live_123 key=' + "A" * 40 + ' postgres://u:p@db:5432/x')
        clean = collector.redact(line)
        for secret in ("eyJhbGci", "hunter2", "sk_live_123", "A" * 40, "u:p@db"):
            self.assertNotIn(secret, clean)
        self.assertIn("auth error: invalid token", clean)
        self.assertIn("password=[hidden]", clean)

    def test_tailscale(self):
        data = {"BackendState": "Running", "Self": {"DNSName": "macserver.tail1.ts.net.",
                                                    "TailscaleIPs": ["100.64.0.1"], "Online": True}}
        self.assertEqual(collector.tailscale(json.dumps(data))["name"], "macserver.tail1.ts.net")
        data["Peer"] = {"a": {"Online": True}, "b": {"Online": False}, "c": {"Online": True}}
        self.assertEqual(collector.tailscale(json.dumps(data))["peers_online"], 2)
        self.assertIsNone(collector.public_ok(""))
        self.assertEqual(collector.tailscale(None), {"state": "unknown"})

    def test_hwmon_and_battery(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            mon = root / "hwmon" / "hwmon0"
            mon.mkdir(parents=True)
            (mon / "name").write_text("coretemp\n")
            (mon / "temp1_input").write_text("61000\n")
            (mon / "temp2_input").write_text("72500\n")
            smc = root / "hwmon" / "hwmon1"
            smc.mkdir()
            (smc / "name").write_text("applesmc\n")
            (smc / "fan1_input").write_text("2100\n")
            self.assertEqual(collector.hwmon(root / "hwmon"), {"cpu_temp_c": 72.5, "fans_rpm": [2100]})

            bat = root / "ps" / "BAT0"
            bat.mkdir(parents=True)
            (bat / "type").write_text("Battery\n")
            (bat / "capacity").write_text("79\n")
            (bat / "status").write_text("Not charging\n")
            (bat / "charge_control_end_threshold").write_text("80\n")
            self.assertEqual(collector.battery(root / "ps"),
                             {"percent": 79, "status": "Not charging", "limit": "80"})
            self.assertIsNone(collector.battery(root / "none"))

    def test_only_public_keys_are_exported(self):
        self.assertEqual(set(collector.PUBLIC_KEYS), {"SUPABASE_PUBLISHABLE_KEY", "ANON_KEY"})


ACCESS_LOG = "\n".join([
    '172.18.0.12 - - [29/Sep/2026:01:31:46 +0000] "POST /rest/v1/event_teams?on_conflict=event_id%2Cteam_id HTTP/1.1" 201 0 "-" "Deno/2.1.4 (variant; SupabaseEdgeRuntime/1.76.2)"',
    '172.18.0.12 - - [29/Sep/2026:01:31:47 +0000] "POST /rest/v1/teams?on_conflict=number&select=id HTTP/1.1" 201 45 "-" "Deno/2.1.4 (variant; SupabaseEdgeRuntime/1.76.2)"',
    '172.18.0.1 - - [29/Sep/2026:01:31:44 +0000] "POST /functions/v1/sync-events HTTP/1.1" 200 77 "-" "curl/8.14.1"',
    '192.168.18.20 - - [29/Sep/2026:01:31:50 +0000] "GET /rest/v1/events?apikey=eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiIxIn0.abc HTTP/1.1" 503 12 "-" "MyApp/3 CFNetwork/1494 Darwin/23"',
    '100.127.159.27 - - [29/Sep/2026:01:31:51 +0000] "GET /auth/v1/user/0f8fad5b-d9cb-469f-a165-70867728950e HTTP/1.1" 404 30 "-" "Mozilla/5.0"',
    '[2026-09-29 01:42:44.118][1][info][main] [source/server/drain_manager_impl.cc:226] shutting down parent after drain',
])
NAMES = {"172.18.0.12": "supabase-edge-functions", "172.18.0.5": "supabase-envoy"}
PEERS = {"100.127.159.27": "jonahs-iphone"}
T0 = 1790645510   # 2026-09-29 01:31:50 UTC


class DebugParserTests(unittest.TestCase):
    def test_ip_kind_and_agent_kind(self):
        for ip, kind in (("127.0.0.1", "loopback"), ("::1", "loopback"), ("100.89.16.53", "tailnet"),
                         ("fd7a:115c:a1e0::292b:1036", "tailnet"), ("192.168.18.33", "lan"),
                         ("172.18.0.5", "lan"), ("1.1.1.1", "public"), ("junk", ""), ("[::]", "lan")):
            self.assertEqual(collector.ip_kind(ip), kind, ip)
        self.assertEqual(collector.agent_kind("Deno/2.1.4 (variant; SupabaseEdgeRuntime/1.76.2)"), "edge function")
        self.assertEqual(collector.agent_kind("MyApp/3 CFNetwork/1494 Darwin/23"), "iOS app")
        self.assertEqual(collector.agent_kind("Mozilla/5.0"), "browser")
        self.assertEqual(collector.agent_kind("weirdtool/9"), "weirdtool")
        self.assertEqual(collector.agent_kind(""), "-")

    def test_parse_access_strips_queries_and_secrets(self):
        lines = ACCESS_LOG.splitlines()
        first = collector.parse_access(lines[0])
        self.assertEqual((first["method"], first["path"], first["status"], first["bytes"]),
                         ("POST", "/rest/v1/event_teams", 201, 0))
        self.assertEqual(first["t"], T0 - 4)
        leaked = collector.parse_access(lines[3])
        self.assertEqual(leaked["path"], "/rest/v1/events")            # the ?apikey=eyJ... never reaches status.json
        self.assertNotIn("eyJ", json.dumps(leaked))
        self.assertIsNone(collector.parse_access(lines[5]))            # envoy's own log lines are skipped
        self.assertEqual(collector.clean_path("/storage/v1/object/sign/x/" + "A" * 50 + "?token=abc"),
                         "/storage/v1/object/sign/x/[hidden]")
        self.assertEqual(collector.clean_path("/auth/v1/user/0f8fad5b-d9cb-469f-a165-70867728950e"),
                         "/auth/v1/user/0f8fad5b-d9cb-469f-a165-70867728950e")     # a uuid is not a secret
        self.assertIsNone(collector.parse_access("garbage"))
        east = '1.2.3.4 - - [29/Sep/2026:01:31:46 -0400] "GET / HTTP/1.1" 200 1 "-" "x"'
        self.assertEqual(collector.parse_access(east)["t"], T0 - 4 + 4 * 3600)    # time zones are honoured

    def test_request_summary(self):
        summary, errors = collector.request_summary(ACCESS_LOG.splitlines(), T0 + 2, NAMES, PEERS)
        self.assertEqual(summary["total"], 5)
        self.assertEqual(summary["classes"], {"2xx": 3, "3xx": 0, "4xx": 1, "5xx": 1})
        self.assertEqual(sum(summary["per_2s"]), 5)
        self.assertEqual(len(summary["per_2s"]), 30)
        newest = summary["recent"][0]
        self.assertEqual((newest["status"], newest["client"], newest["agent"]), (404, "tailnet jonahs-iphone", "browser"))
        self.assertEqual([r["client"] for r in summary["recent"]][-1], "this Mac")
        self.assertEqual({(r["status"], r["client"]) for r in summary["errors"]},
                         {(503, "LAN 192.168.18.20"), (404, "tailnet jonahs-iphone")})
        self.assertIn(["POST /rest/v1/teams", 1], [list(p) for p in summary["top_paths"]])
        self.assertIn("GET /auth/v1/user/:id", [p for p, _ in summary["top_paths"]])      # ids are folded together
        self.assertIn("edge-functions (edge function)", [c for c, _ in summary["top_clients"]])
        # Errors outlive the one-minute window (for 15 minutes), without repeating.
        again, errors = collector.request_summary([], T0 + 300, NAMES, PEERS, errors)
        self.assertEqual(again["total"], 0)
        self.assertEqual(len(again["errors"]), 2)
        again, errors = collector.request_summary([], T0 + 2000, NAMES, PEERS, errors)
        self.assertEqual(again["errors"], [])

    def test_conntrack_flows(self):
        table = "\n".join([
            "ipv4     2 tcp      6 431999 ESTABLISHED src=172.18.0.12 dst=104.18.2.161 sport=39754 dport=443 src=104.18.2.161 dst=192.168.18.33 sport=443 dport=39754 [ASSURED] mark=0 zone=0 use=2",
            "ipv4     2 tcp      6 431999 ESTABLISHED src=172.18.0.12 dst=104.18.2.161 sport=39755 dport=443 src=104.18.2.161 dst=192.168.18.33 sport=443 dport=39755 [ASSURED] mark=0 zone=0 use=2",
            "ipv4     2 tcp      6 118 TIME_WAIT src=172.18.0.12 dst=8.8.8.8 sport=1 dport=443 src=8.8.8.8 dst=1.1.1.1 sport=443 dport=1 mark=0 zone=0 use=2",
            "ipv4     2 udp      17 29 src=100.89.16.53 dst=100.100.100.100 sport=45302 dport=53 src=100.100.100.100 dst=100.89.16.53 sport=53 dport=45302 mark=0 zone=0 use=2",
            "ipv4     2 tcp      6 431999 ESTABLISHED src=192.168.18.20 dst=192.168.18.33 sport=51000 dport=5432 src=172.18.0.10 dst=192.168.18.20 sport=5432 dport=51000 [ASSURED] mark=0 zone=0 use=2",
            "ipv4     2 tcp      6 431999 ESTABLISHED src=100.127.159.27 dst=100.89.16.53 sport=50000 dport=443 src=100.89.16.53 dst=100.127.159.27 sport=443 dport=50000 [ASSURED] mark=0 zone=0 use=2",
            "ipv4     2 tcp      6 431999 ESTABLISHED src=172.18.0.1 dst=172.18.0.5 sport=50000 dport=8000 src=172.18.0.5 dst=172.18.0.1 sport=8000 dport=50000 [ASSURED] mark=0 zone=0 use=2",
            "ipv4     2 tcp      6 431999 ESTABLISHED src=192.168.18.33 dst=199.165.136.100 sport=46206 dport=443 src=199.165.136.100 dst=192.168.18.33 sport=443 dport=46206 [ASSURED] mark=0 zone=0 use=2",
            "not a flow", "ipv6     10 icmpv6 58 29 src=fe80::1 dst=ff02::2 type=133 code=0",
        ])
        flows = collector.parse_conntrack(table)
        self.assertEqual(len(flows), 8)
        self.assertEqual(flows[0], {"proto": "tcp", "state": "ESTABLISHED", "src": "172.18.0.12", "dst": "104.18.2.161",
                                    "sport": "39754", "dport": "443"})
        self.assertEqual(flows[3]["state"], "")                          # udp has no state
        host = {"192.168.18.33", "100.89.16.53", "172.18.0.1"}
        summary = collector.flow_summary(flows, host, NAMES, PEERS, {("199.165.136.100", 443): "tailscaled"})
        self.assertEqual(summary["tracked"], 8)
        out = {(o["who"], o["dst"], o["port"]): o for o in summary["out"]}
        self.assertEqual(out[("edge-functions", "104.18.2.161", 443)]["n"], 2)       # two flows, one line
        self.assertEqual(out[("edge-functions", "104.18.2.161", 443)]["svc"], "https")
        self.assertIn(("tailscaled", "199.165.136.100", 443), out)                   # host flows get their process
        self.assertFalse([o for o in summary["out"] if o["dst"] == "8.8.8.8"])       # TIME_WAIT is not live
        self.assertFalse([o for o in summary["out"] if o["port"] == 53])             # MagicDNS is noise
        inbound = {(i["src"], i["port"]): i for i in summary["in"]}
        self.assertIn(("LAN 192.168.18.20", 5432), inbound)                          # a LAN host reaching Postgres
        self.assertEqual(inbound[("LAN 192.168.18.20", 5432)]["kind"], "lan")
        self.assertIn(("tailnet jonahs-iphone", 443), inbound)
        self.assertFalse([i for i in summary["in"] if i["src"] == "this Mac"])       # container-to-container is internal
        self.assertEqual(summary["states"]["ESTABLISHED"], 6)

    def test_listeners_show_how_far_they_reach(self):
        ss = "\n".join([
            'LISTEN 0 4096 0.0.0.0:5432 0.0.0.0:* users:(("docker-proxy",pid=101570,fd=8))',
            'LISTEN 0 4096 [::]:5432 [::]:* users:(("docker-proxy",pid=101577,fd=8))',
            'LISTEN 0 4096 127.0.0.1:8090 0.0.0.0:* users:(("python3",pid=65937,fd=3))',
            'LISTEN 0 4096 100.89.16.53:8443 0.0.0.0:* users:(("tailscaled",pid=763,fd=31))',
            'LISTEN 0 4096 192.168.18.33:9999 0.0.0.0:* users:(("thing",pid=1,fd=3))',
            'LISTEN 0 4096 127.0.0.53%lo:53 0.0.0.0:*',
            "junk"])
        rows = collector.parse_ss_listeners(ss, {5432: "supabase-pooler"})
        self.assertEqual([(r["port"], r["scope"], r["proc"]) for r in rows],
                         [(5432, "all", "supabase-pooler"),           # exposed first; the IPv4/IPv6 pair is one row
                          (9999, "lan", "thing"), (8443, "tailnet", "tailscaled"),
                          (53, "loopback", "?"), (8090, "loopback", "python3")])
        ports = collector.published_ports(json.dumps({"Names": "supabase-pooler",
                                                     "Ports": "0.0.0.0:5432->5432/tcp, [::]:6543->6543/tcp, 10000/tcp"}))
        self.assertEqual(ports, {5432: "supabase-pooler", 6543: "supabase-pooler"})
        procs = collector.parse_ss_established('0 0 192.168.18.33:46206 199.165.136.100:443 users:(("tailscaled",pid=763,fd=26))\n')
        self.assertEqual(procs, {("199.165.136.100", 443): "tailscaled"})

    def test_labels_and_small_helpers(self):
        self.assertEqual(collector.client_label("172.18.0.1", NAMES, PEERS), "this Mac")
        self.assertEqual(collector.client_label("172.18.0.12", NAMES, PEERS), "edge-functions")
        self.assertEqual(collector.client_label("100.101.9.9", NAMES, PEERS), "tailnet 100.101.9.9")
        self.assertEqual(collector.client_label("8.8.4.4", NAMES, PEERS), "8.8.4.4")
        self.assertEqual(collector.default_gateway("Iface\tDestination\tGateway\nwlp2s0\t00000000\t0112A8C0\n"), "192.168.18.1")
        self.assertIsNone(collector.default_gateway("Iface\tDestination\tGateway\n"))
        status = json.dumps({"Peer": {"k": {"HostName": "jonahs-iphone", "TailscaleIPs": ["100.127.159.27", "fd7a::1"]}}})
        self.assertEqual(collector.tailscale_peers(status), {"100.127.159.27": "jonahs-iphone", "fd7a::1": "jonahs-iphone"})
        self.assertEqual(collector.tailscale_peers("nope"), {})

    def test_files_and_backups(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "a.sql.gz").write_bytes(b"x" * 10)
            (root / "b.sql.gz").write_bytes(b"y" * 2048)
            os.utime(root / "a.sql.gz", (T0 - 9000, T0 - 9000))
            os.utime(root / "b.sql.gz", (T0 - 7200, T0 - 7200))
            info = collector.backup_info(T0, str(root))
            self.assertEqual((info["count"], info["newest_size"]), (2, 2048))
            self.assertEqual(collector.backup_info(T0, str(root / "none")), {"count": 0})
        files = collector.watched_files(time.time())
        self.assertTrue(all(f["path"] and f["note"] for f in files))
        self.assertFalse([f for f in files if f["age_s"] < 0])

    def test_debug_never_breaks_the_snapshot(self):
        state = {}
        boom = collector.debug_info
        try:
            collector.debug_info = lambda *a: 1 / 0
            data = collector.collect(state)
        finally:
            collector.debug_info = boom
        self.assertIn("error", data["debug"])
        self.assertIn("containers", data)


if __name__ == "__main__":
    unittest.main()
