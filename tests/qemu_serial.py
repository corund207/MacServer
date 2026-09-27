#!/usr/bin/env python3
"""Drive a QEMU serial console: echo it, wait for text, answer prompts, fail fast.

usage: qemu_serial.py --sock S --log L [--fail TEXT]... STEP [STEP ...]
STEP is "TEXT[=>REPLY][@SECONDS]": wait up to SECONDS (default 600) for TEXT,
then type REPLY (if given). Any --fail TEXT seen on the console aborts at once.
"""
import argparse
import re
import socket
import sys
import time

STEP = re.compile(r"^(?P<expect>.*?)(?:=>(?P<reply>.*?))?(?:@(?P<secs>\d+))?$")


def connect(path, wait_s=60):
    deadline = time.time() + wait_s
    while True:
        sock = socket.socket(socket.AF_UNIX)
        try:
            sock.connect(path)
            sock.settimeout(1)
            return sock
        except OSError:
            sock.close()
            if time.time() > deadline:
                sys.exit("serial socket never appeared")
            time.sleep(0.5)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--sock", required=True)
    parser.add_argument("--log", required=True)
    parser.add_argument("--fail", action="append", default=[])
    parser.add_argument("steps", nargs="+")
    args = parser.parse_args()

    sock = connect(args.sock)
    seen = ""
    with open(args.log, "a", encoding="utf-8", errors="replace") as log:
        for raw in args.steps:
            step = STEP.match(raw)
            expect, reply = step["expect"], step["reply"]
            deadline = time.time() + int(step["secs"] or 600)
            while expect not in seen:
                for bad in args.fail:
                    if bad in seen:
                        sys.exit(f"FAILED: console showed {bad!r}")
                if time.time() > deadline:
                    sys.exit(f"TIMEOUT: {expect!r} did not appear within {step['secs'] or 600}s")
                try:
                    chunk = sock.recv(4096)
                except socket.timeout:
                    continue
                if not chunk:
                    sys.exit(f"console closed while waiting for {expect!r}")
                text = chunk.decode("utf-8", "replace")
                log.write(text)
                log.flush()
                sys.stdout.write(text.replace("\r", ""))
                sys.stdout.flush()
                seen = (seen + text)[-50000:]
            print(f"\n>>> seen: {expect}", flush=True)
            seen = seen[seen.index(expect) + len(expect):]
            if reply is not None:
                time.sleep(1)
                sock.sendall(reply.encode() + b"\n")


if __name__ == "__main__":
    main()
