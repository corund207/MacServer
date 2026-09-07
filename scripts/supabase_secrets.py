#!/usr/bin/env python3
"""Create a new private legacy-compatible Supabase environment; never rotate it."""
import argparse
import base64
import hashlib
import hmac
import json
import os
from pathlib import Path
import secrets
import stat
import time
from urllib.parse import urlsplit

ROOT = Path(__file__).resolve().parents[1]


def jwt(secret, role, now):
    def encode(value):
        return base64.urlsafe_b64encode(json.dumps(value, separators=(',', ':')).encode()).rstrip(b'=').decode()
    payload = encode({'alg': 'HS256', 'typ': 'JWT'}) + '.' + encode(
        {'role': role, 'iss': 'supabase', 'iat': now, 'exp': now + 365 * 86400})
    sig = base64.urlsafe_b64encode(hmac.new(secret.encode(), payload.encode(), hashlib.sha256).digest()).rstrip(b'=')
    return payload + '.' + sig.decode()


def url(value):
    parsed = urlsplit(value)
    if (parsed.scheme not in ('https', 'http') or not parsed.hostname or parsed.username
            or parsed.password or parsed.query or parsed.fragment
            or any(c.isspace() or c in "'\"$`\\" for c in value)
            or (parsed.scheme == 'http' and parsed.hostname not in ('localhost', '127.0.0.1'))):
        raise ValueError('URLs require HTTPS (HTTP allowed only for loopback), no credentials or interpolation')
    if parsed.path not in ('', '/'):
        raise ValueError('provide a base URL without a path')
    _ = parsed.port
    return value.rstrip('/')


def create(directory, api_url, site_url):
    api_url, site_url = url(api_url), url(site_url)
    directory = Path(directory).absolute()
    # Ancestors must exist and must not be symlinks. Never silently chmod existing directories.
    for parent in [directory, *directory.parents]:
        if parent.is_symlink():
            raise ValueError('symlink path refused')
    if not directory.exists():
        directory.mkdir(mode=0o700)
    info = directory.stat()
    if not stat.S_ISDIR(info.st_mode) or stat.S_IMODE(info.st_mode) != 0o700 or info.st_uid != os.getuid():
        raise ValueError('output directory must be owned by this user with mode 0700')
    values = dict(line.split('=', 1) for line in (ROOT / 'infra/supabase/.env.example').read_text().splitlines()
                  if line and not line.startswith('#'))
    values.update(SUPABASE_PUBLIC_URL=api_url, API_EXTERNAL_URL=api_url + '/auth/v1', SITE_URL=site_url)
    for key, size in {'POSTGRES_PASSWORD': 32, 'JWT_SECRET': 32, 'DASHBOARD_PASSWORD': 24,
                      'SECRET_KEY_BASE': 48, 'REALTIME_DB_ENC_KEY': 8,
                      'PG_META_CRYPTO_KEY': 32, 'S3_PROTOCOL_ACCESS_KEY_ID': 16,
                      'S3_PROTOCOL_ACCESS_KEY_SECRET': 32}.items():
        values[key] = secrets.token_hex(size)
    values['DASHBOARD_PASSWORD'] = 'A' + values['DASHBOARD_PASSWORD']
    now = int(time.time())
    for key, role in [('ANON_KEY', 'anon'), ('SERVICE_ROLE_KEY', 'service_role')]:
        values[key] = jwt(values['JWT_SECRET'], role, now)
    target = directory / '.env'
    fd = os.open(target, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    with os.fdopen(fd, 'w') as stream:
        stream.write('# Private generated environment; escrow encrypted before deployment.\n')
        stream.write('\n'.join(k + '=' + v for k, v in values.items()) + '\n')
        stream.flush()
        os.fsync(stream.fileno())
    return target


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--directory', required=True)
    parser.add_argument('--api-url', default='http://localhost:8000')
    parser.add_argument('--site-url', default='http://localhost:3000')
    args = parser.parse_args()
    create(args.directory, args.api_url, args.site_url)
    print('Created private .env; values not displayed. No services started.')


if __name__ == '__main__':
    main()
