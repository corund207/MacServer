#!/usr/bin/env python3
"""Create a backup encryption password in a NEW private directory; print no secrets."""
import argparse
import os
from pathlib import Path
import secrets
import stat
import sys


def initialize(directory):
    directory = Path(os.path.abspath(directory))
    if directory.parent.resolve() != directory.parent or not directory.parent.is_dir():
        raise ValueError("use an existing parent without symlink components")
    # Exclusive directory creation deliberately refuses reruns and credential rotation.
    directory.mkdir(mode=0o700)
    try:
        target = directory / "restic-password"
        fd = os.open(target, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
        with os.fdopen(fd, "w") as stream:
            stream.write(secrets.token_urlsafe(48) + "\n")
            stream.flush()
            os.fsync(stream.fileno())
        if stat.S_IMODE(directory.stat().st_mode) != 0o700:
            raise ValueError("unexpected directory permissions")
    except Exception:
        # Leave partial output for explicit operator inspection; never delete credentials.
        raise


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--directory", required=True)
    args = parser.parse_args()
    try:
        initialize(args.directory)
    except (OSError, ValueError):
        print("Refused: destination must be new and private; inspect permissions or partial output.", file=sys.stderr)
        return 2
    print("Backup password created. Store encrypted escrow separately before use.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
