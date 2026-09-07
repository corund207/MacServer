#!/usr/bin/env python3
"""Render/check the inert appliance adaptation; never calls Docker or starts services."""
import argparse
import copy
import hashlib
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
BASE = ROOT / 'infra/supabase'


def render():
    lock = json.loads((BASE / 'release.lock.json').read_text())
    for name, digest in lock['files'].items():
        if hashlib.sha256((BASE / 'upstream' / name).read_bytes()).hexdigest() != digest:
            raise ValueError(f'upstream source drift: {name}')
    config = copy.deepcopy(json.loads((BASE / 'upstream/compose.json').read_text()))
    config['name'] = 'macserver-data'
    del config['services']['supavisor']
    profiles = {'studio': 'management', 'meta': 'management', 'storage': 'storage',
                'imgproxy': 'storage', 'realtime': 'realtime', 'functions': 'functions'}
    limits = {'db': '2048m', 'auth': '256m', 'rest': '256m', 'api-gw': '256m',
              'studio': '768m', 'meta': '256m', 'storage': '512m',
              'imgproxy': '256m', 'realtime': '512m', 'functions': '512m'}
    for name, svc in config['services'].items():
        svc.pop('container_name', None)
        if name == 'realtime':
            svc['hostname'] = 'realtime-dev.supabase-realtime'
            # Upstream tenant discovery relies on this exact container name.
            svc['container_name'] = 'realtime-dev.supabase-realtime'
        svc.pop('ports', None)
        svc['platform'] = 'linux/amd64'
        svc['image'] = lock['images'][name]['tag'] + '@' + lock['images'][name]['amd64_digest']
        svc['profiles'] = [profiles.get(name, 'core')]
        svc['security_opt'] = ['no-new-privileges:true']
        svc['mem_limit'] = limits[name]
        svc['pids_limit'] = 256
        svc['logging'] = {'driver': 'local', 'options': {'max-size': '10m', 'max-file': '3'}}
        volumes = []
        for volume in svc.get('volumes', []):
            source, target, *options = volume.split(':')
            if source == './volumes/db/data': source = 'db-data'
            elif source == './volumes/storage': source = 'storage-data'
            elif source == './volumes/snippets': source = 'snippets'
            elif source == './volumes/functions': source = './functions'
            elif source.startswith('./volumes/'): source = './upstream/' + source[2:]
            readonly = source.startswith('./') or name == 'imgproxy'
            volumes.append(source + ':' + target + (':ro' if readonly else ''))
        if volumes: svc['volumes'] = volumes
    config['services']['api-gw']['depends_on'] = {'rest': {'condition': 'service_healthy'},
                                                 'auth': {'condition': 'service_healthy'}}
    config['services']['studio']['depends_on'] = {'meta': {'condition': 'service_started'}}
    fn = config['services']['functions']['environment']
    # Verify user tokens at Auth; never inject signing/database/service-role secrets.
    for key in ['JWT_SECRET', 'SUPABASE_SERVICE_ROLE_KEY', 'SUPABASE_DB_URL', 'SUPABASE_SECRET_KEYS']:
        fn.pop(key, None)
    fn['VERIFY_JWT'] = 'true'
    fn['SUPABASE_AUTH_URL'] = 'http://auth:9999'
    config['services']['realtime']['environment']['DB_ENC_KEY'] = '${REALTIME_DB_ENC_KEY:?required}'
    config['services']['rest']['environment']['PGRST_DB_SCHEMAS'] = 'api'
    config['services']['rest']['environment']['PGRST_DB_EXTRA_SEARCH_PATH'] = 'extensions'
    config['services']['studio']['environment']['PGRST_DB_SCHEMAS'] = 'api'
    config['services']['studio']['environment']['PGRST_DB_EXTRA_SEARCH_PATH'] = 'extensions'
    for svc in config['services'].values():
        for key, value in svc.get('environment', {}).items():
            if isinstance(value, str):
                for secret in ['POSTGRES_PASSWORD', 'JWT_SECRET', 'ANON_KEY', 'SERVICE_ROLE_KEY',
                               'DASHBOARD_PASSWORD', 'SECRET_KEY_BASE', 'PG_META_CRYPTO_KEY',
                               'S3_PROTOCOL_ACCESS_KEY_ID', 'S3_PROTOCOL_ACCESS_KEY_SECRET']:
                    value = value.replace('${' + secret + '}', '${' + secret + ':?required}')
                svc['environment'][key] = value
    config['networks'] = {'default': {'internal': True}}
    config['volumes'] = {k: {} for k in ['db-data', 'db-config', 'storage-data', 'deno-cache', 'snippets']}
    return config


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--write', action='store_true', help='regenerate tracked JSON, no deployment')
    args = parser.parse_args()
    content = json.dumps(render(), indent=2) + '\n'
    path = BASE / 'compose.json'
    if args.write:
        path.write_text(content)
    elif path.read_text() != content:
        raise SystemExit('Compose adaptation drift; inspect then regenerate with --write')
    print('PASS: pinned source and deterministic Compose adaptation; no deployment')


if __name__ == '__main__':
    main()
