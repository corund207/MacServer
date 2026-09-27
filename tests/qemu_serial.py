#!/usr/bin/env python3
"""Drive a QEMU serial console: wait for text, optionally answer, fail on timeout.

usage: qemu_serial.py SOCKET LOG TIMEOUT_S STEP [STEP ...]
STEP is "expect=>reply" (reply may be empty). Every serial byte is appended to LOG.
"""
import socket
import sys
import time


def main():
    path, log_path, timeout = sys.argv[1], sys.argv[2], float(sys.argv[3])
    steps = [s.split("=>", 1) if "=>" in s else [s, None] for s in sys.argv[4:]]
    deadline = time.time() + timeout
    sock = None
    while sock is None:
        try:
            sock = socket.socket(socket.AF_UNIX)
            sock.connect(path)
        except OSError:
            sock = None
            if time.time() > deadline:
                sys.exit("serial socket never appeared")
            time.sleep(1)
    sock.settimeout(2)
    seen = ""
    with open(log_path, "a", encoding="utf-8", errors="replace") as log:
        for expect, reply in steps:
            while expect not in seen:
                if time.time() > deadline:
                    sys.exit(f"timed out waiting for {expect!r}")
                try:
                    chunk = sock.recv(4096)
                except socket.timeout:
                    continue
                if not chunk:
                    sys.exit(f"serial closed while waiting for {expect!r}")
                text = chunk.decode("utf-8", "replace")
                log.write(text)
                log.flush()
                seen = (seen + text)[-20000:]
            print(f"seen: {expect}", flush=True)
            seen = seen[seen.index(expect) + len(expect):]
            if reply is not None:
                time.sleep(1)
                sock.sendall(reply.encode() + b"\n")


if __name__ == "__main__":
    main()
