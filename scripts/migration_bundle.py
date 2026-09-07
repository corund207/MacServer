#!/usr/bin/env python3
"""Seal/verify already-exported SQL files offline. Never connects or executes SQL."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import stat

FILES = ('roles.sql', 'schema.sql', 'data.sql')


def inventory(directory):
    directory = Path(directory).absolute()
    if directory.resolve() != directory or not directory.is_dir():
        raise ValueError('use an existing directory without symlink components')
    if stat.S_IMODE(directory.stat().st_mode) != 0o700:
        raise ValueError('bundle directory must have mode 0700')
    result = {}
    for name in FILES:
        path = directory / name
        info = path.lstat()
        if not stat.S_ISREG(info.st_mode) or stat.S_IMODE(info.st_mode) != 0o600 or info.st_size == 0:
            raise ValueError(f'{name} must be a nonempty regular mode-0600 file')
        digest = hashlib.sha256()
        with path.open('rb') as stream:
            for chunk in iter(lambda: stream.read(1024 * 1024), b''): digest.update(chunk)
        result[name] = {'bytes': info.st_size, 'sha256': digest.hexdigest()}
    return {'format': 1, 'files': result}


def seal(directory):
    report = inventory(directory)
    path = Path(directory) / 'manifest.json'
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    with os.fdopen(fd, 'w') as stream:
        json.dump(report, stream, indent=2)
        stream.write('\n')
    return report


def verify(directory):
    report = inventory(directory)
    path = Path(directory) / 'manifest.json'
    if path.is_symlink() or json.loads(path.read_text()) != report:
        raise ValueError('bundle integrity mismatch')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action', choices=('seal', 'verify'))
    parser.add_argument('directory', type=Path)
    args = parser.parse_args()
    (seal if args.action == 'seal' else verify)(args.directory)
    print('PASS: bundle ' + args.action + '; integrity only, no restore or completeness claim')


if __name__ == '__main__': main()
