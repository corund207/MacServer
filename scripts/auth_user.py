#!/usr/bin/env python3
"""Create one password user privately; never expose Auth administration publicly."""
import argparse
import getpass
import json
import os
from pathlib import Path
import re
import stat
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
NODE_SCRIPT = """
let raw = ''; for await (const chunk of process.stdin) { raw += chunk; if (raw.length > 16384) process.exit(2); }
try {
  const {key, email, password} = JSON.parse(raw);
  const response = await fetch('http://auth:9999/admin/users', {
    method:'POST', redirect:'error', signal:AbortSignal.timeout(15000),
    headers:{authorization:'Bearer '+key,'content-type':'application/json'},
    body:JSON.stringify({email,password,email_confirm:true})
  });
  await response.body?.cancel();
  if (!response.ok) process.exit(2);
  process.stdout.write('created');
} catch { process.exit(2); }
"""


def valid_input(email, password):
    if (not isinstance(email, str) or len(email) > 254 or not re.fullmatch(r"[^\s@\x00-\x1f]+@[^\s@\x00-\x1f]+\.[^\s@\x00-\x1f]+", email)
            or not isinstance(password, str) or not 12 <= len(password) <= 128):
        raise ValueError("use a valid email and a unique password of 12–128 characters")


def service_key(path):
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    try:
        info = os.fstat(fd)
        if not stat.S_ISREG(info.st_mode) or info.st_uid != 0 or stat.S_IMODE(info.st_mode) != 0o600 or info.st_nlink != 1 or info.st_size > 65536:
            raise ValueError("unsafe environment file")
        raw = os.read(fd, 65537).decode("utf-8")
        values = [line.split("=", 1)[1] for line in raw.splitlines() if line.startswith("SERVICE_ROLE_KEY=")]
        if len(values) != 1 or not re.fullmatch(r"[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+", values[0]):
            raise ValueError("missing service credential")
        return values[0]
    finally:
        os.close(fd)


def create_user(email, password, key, runner=subprocess.run):
    valid_input(email, password)
    image = json.loads((ROOT / "infra/ingress/compose.json").read_text(encoding="utf-8"))["services"]["gateway"]["image"]
    if not re.fullmatch(r"node:[a-zA-Z0-9.-]+@sha256:[a-f0-9]{64}", image):
        raise ValueError("unreviewed runtime image")
    argv = ["/usr/bin/docker", "run", "--rm", "--interactive", "--pull=never",
            "--network", "macserver-data_default", "--read-only", "--cap-drop=ALL",
            "--security-opt", "no-new-privileges:true", "--user", "65532:65532",
            "--memory", "192m", "--pids-limit", "64", image, "node", "--input-type=module", "-e", NODE_SCRIPT]
    result = runner(argv, input=json.dumps({"email": email, "password": password, "key": key}),
                    text=True, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, timeout=30, check=False)
    if result.returncode or result.stdout != "created":
        raise RuntimeError("Auth refused creation or the private runtime is unavailable; existing users were not changed")


def main():
    argparse.ArgumentParser(description=__doc__).parse_args()
    if sys.platform != "linux" or os.geteuid() != 0:
        print("Run with sudo on the qualified Debian appliance.", file=sys.stderr)
        return 2
    try:
        key = service_key("/etc/macserver/supabase.env")
        email = input("New user's email: ").strip()
        password = getpass.getpass("New user's password (12–128 characters): ")
        if password != getpass.getpass("Confirm password: "):
            raise ValueError("passwords did not match")
        create_user(email, password, key)
    except (OSError, ValueError, RuntimeError, EOFError, KeyboardInterrupt, subprocess.TimeoutExpired):
        print("User creation did not complete. Check the private stack and inputs; no credentials were printed.", file=sys.stderr)
        return 1
    print("User created with confirmed email. Share credentials privately; no email was sent.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
