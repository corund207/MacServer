#!/usr/bin/env python3
"""Offline repository security assertions; never contacts a host, daemon, or network."""
import json
from pathlib import Path
import re
import subprocess

ROOT = Path(__file__).resolve().parents[1]
DIGEST = re.compile(r"^[^\s]+@sha256:[0-9a-f]{64}$")
ACTION = re.compile(r"^\s*- uses: [^@\s]+@[0-9a-f]{40}(?:\s+#.*)?$", re.MULTILINE)


def compose_findings(config):
    findings = []
    if config.get("networks") != {"default": {"internal": True}}:
        findings.append("Compose default network is not internal-only")
    for name, service in config.get("services", {}).items():
        for key in ("ports", "privileged", "network_mode"):
            if key in service:
                findings.append(f"{name} has forbidden Compose key {key}")
        if not DIGEST.fullmatch(str(service.get("image", ""))):
            findings.append(f"{name} image is not digest pinned")
        if service.get("platform") != "linux/amd64":
            findings.append(f"{name} platform is not linux/amd64")
        if not service.get("profiles") or not service.get("mem_limit") or not service.get("pids_limit"):
            findings.append(f"{name} lacks profile/resource bounds")
        if "docker.sock" in json.dumps(service):
            findings.append(f"{name} can access the Docker socket")
        for key, value in service.get("environment", {}).items():
            sensitive = ("PASSWORD" in key or "SECRET" in key or key in
                         {"JWT_SECRET", "ANON_KEY", "SERVICE_KEY", "SERVICE_ROLE_KEY"})
            if sensitive and value and (not isinstance(value, str) or "${" not in value):
                findings.append(f"{name} embeds a sensitive environment value: {key}")
    return findings


def text_findings(root):
    findings = []
    read = lambda name: (root / name).read_text()
    ssh = read("infra/host/sshd.conf")
    for directive in ("PermitRootLogin no", "PasswordAuthentication no",
                      "KbdInteractiveAuthentication no", "AuthenticationMethods publickey",
                      "AllowTcpForwarding no", "PermitTunnel no"):
        if directive not in ssh:
            findings.append(f"SSH candidate lacks {directive}")
    firewall = read("infra/host/nftables.nft")
    if 'iifname "tailscale0" tcp dport { 22, 8443 } accept' not in firewall:
        findings.append("host firewall is not limited to Tailscale SSH/admin ports")
    policy = json.loads(read("infra/tailscale-policy.example.json"))
    grants = policy.get("grants", [])
    if len(grants) != 1 or set(grants[0].get("ip", [])) != {"tcp:22", "tcp:8443"}:
        findings.append("tailnet example does not limit administrators to SSH/admin ports")
    denied = set(policy.get("tests", [{}])[0].get("deny", []))
    if not {"tag:macserver:443", "tag:macserver:5432", "tag:macserver:7460", "tag:macserver:8000"}.issubset(denied):
        findings.append("tailnet example lacks management/data/dashboard deny tests")
    admin = read("infra/admin/macserver-admin.service")
    for directive in ("User=macserver-admin", "NoNewPrivileges=true", "CapabilityBoundingSet=",
                      "IPAddressDeny=any", "InaccessiblePaths=-/run/docker.sock"):
        if directive not in admin:
            findings.append(f"admin unit lacks {directive}")
    dashboard = read("infra/dashboard/macserver-dashboard.service")
    if "MACSERVER_DASHBOARD_BIND=127.0.0.1" not in dashboard or "IPAddressAllow=localhost" not in dashboard:
        findings.append("dashboard unit is not loopback constrained")
    app = read("apps/admin/src/app.ts")
    if "trustProxy: false" not in app or "Proxy context denied" not in app or "return reply.code(501)" not in app:
        findings.append("admin app proxy or disabled-operation boundary drifted")
    backup = read("scripts/backup.py")
    for value in ("--no-cache", "CONFIRMATION_MISMATCH", "DEVICE_NOT_REMOVABLE",
                  '"--network", "none"', "RESTORE_TARGET_NOT_NEW"):
        if value not in backup:
            findings.append(f"backup refusal boundary lacks {value}")
    deploy = read("scripts/appliance.py")
    unit = read("infra/deploy/macserver-data.service")
    for value in ("source checkout is not clean", "target qualification is incomplete",
                  "commissioning requires an empty Compose target", "backup_ready()",
                  "writersDrained", "dataCompatibilityReviewed"):
        if value not in deploy:
            findings.append(f"deployment refusal boundary lacks {value}")
    for value in ("ConditionPathExists=/etc/macserver/DEPLOYMENT-APPROVED",
                  "--wait --wait-timeout 180", "Requires=docker.service tailscaled.service"):
        if value not in unit:
            findings.append(f"deployment unit lacks {value}")
    migration = read("supabase/migrations/20260907091838_establish_api_boundary.sql")
    verification = read("database/verification/rls_negative.sql")
    for value in ("REVOKE ALL ON SCHEMA", "REVOKE EXECUTE ON FUNCTIONS FROM PUBLIC"):
        if value not in migration:
            findings.append(f"database boundary lacks {value}")
    for value in ("ENABLE ROW LEVEL SECURITY", "FORCE ROW LEVEL SECURITY", "USING", "WITH CHECK"):
        if value not in verification:
            findings.append(f"RLS negative fixture lacks {value}")
    workflow = read(".github/workflows/validate.yml")
    action_lines = [line for line in workflow.splitlines() if "uses:" in line]
    if not action_lines or len(ACTION.findall(workflow)) != len(action_lines):
        findings.append("CI action is not pinned to a full commit")
    if "contents: read" not in workflow or "persist-credentials: false" not in workflow:
        findings.append("CI permissions or credential persistence are unsafe")
    return findings


def tracked_secret_findings(root):
    result = subprocess.run(["git", "ls-files", "-z"], cwd=root,
                            stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                            timeout=10, check=False)
    if result.returncode:
        return ["unable to enumerate tracked files"]
    findings = []
    for raw in result.stdout.split(b"\0"):
        if not raw:
            continue
        name = raw.decode("utf-8", "strict")
        path = Path(name)
        if (path.name == ".env" or path.suffix.lower() in {".pem", ".key", ".p12", ".dump", ".backup"}
                or "secrets" in path.parts):
            findings.append(f"secret-like tracked path: {name}")
    return findings


def audit(root=ROOT):
    root = Path(root)
    return compose_findings(json.loads((root / "infra/supabase/compose.json").read_text())) + text_findings(root) + tracked_secret_findings(root)


def main():
    findings = audit()
    for finding in findings:
        print("FAIL: " + finding)
    if findings:
        raise SystemExit(1)
    print("PASS: offline exposure, identity, secret, CI, RLS, backup, and sandbox assertions")


if __name__ == "__main__":
    main()
