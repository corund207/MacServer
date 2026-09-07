import base64
import hashlib
import hmac
import importlib.util
import json
import os
from pathlib import Path
import stat
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]

def module(name):
    spec = importlib.util.spec_from_file_location(name, ROOT / 'scripts' / (name + '.py'))
    result = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(result)
    return result

config = module('supabase_config')
secrets = module('supabase_secrets')


class SupabaseSafetyTests(unittest.TestCase):
    def test_pinned_offline_topology(self):
        rendered = config.render()
        self.assertEqual(rendered, json.loads((config.BASE / 'compose.json').read_text()))
        self.assertTrue(rendered['networks']['default']['internal'])
        for name, svc in rendered['services'].items():
            self.assertNotIn('ports', svc)
            self.assertNotIn('privileged', svc)
            self.assertNotIn('network_mode', svc)
            self.assertRegex(svc['image'], r'@sha256:[0-9a-f]{64}$')
            self.assertEqual(svc['platform'], 'linux/amd64')
            self.assertTrue(svc['profiles'])
            self.assertNotIn('docker.sock', json.dumps(svc))
        self.assertEqual(rendered['services']['rest']['environment']['PGRST_DB_SCHEMAS'], 'api')
        fn = rendered['services']['functions']['environment']
        for name in ('JWT_SECRET', 'SUPABASE_SERVICE_ROLE_KEY', 'SUPABASE_DB_URL', 'SUPABASE_SECRET_KEYS'):
            self.assertNotIn(name, fn)

    def test_private_secrets_and_correct_role_signatures(self):
        with tempfile.TemporaryDirectory() as temp:
            target = secrets.create(Path(temp) / 'private', 'https://api.example.test', 'https://app.example.test')
            self.assertEqual(stat.S_IMODE(target.stat().st_mode), 0o600)
            values = dict(line.split('=', 1) for line in target.read_text().splitlines() if not line.startswith('#'))
            self.assertEqual(values['API_EXTERNAL_URL'], 'https://api.example.test/auth/v1')
            self.assertEqual(len(values['REALTIME_DB_ENC_KEY']), 16)
            for key, role in [('ANON_KEY', 'anon'), ('SERVICE_ROLE_KEY', 'service_role')]:
                header, claims, sig = values[key].split('.')
                body = json.loads(base64.urlsafe_b64decode(claims + '=='))
                self.assertEqual(body['role'], role)
                expected = hmac.new(values['JWT_SECRET'].encode(), (header + '.' + claims).encode(), hashlib.sha256).digest()
                self.assertEqual(expected, base64.urlsafe_b64decode(sig + '=='))
            original = target.read_bytes()
            with self.assertRaises(FileExistsError):
                secrets.create(target.parent, 'https://api.example.test', 'https://app.example.test')
            self.assertEqual(original, target.read_bytes())

    def test_unsafe_destinations_and_urls(self):
        with tempfile.TemporaryDirectory() as temp:
            parent = Path(temp)
            (parent / 'link').symlink_to(parent, target_is_directory=True)
            with self.assertRaises(ValueError):
                secrets.create(parent / 'link' / 'secret', 'http://localhost:8000', 'http://localhost:3000')
            (parent / 'wide').mkdir(mode=0o755)
            with self.assertRaises(ValueError):
                secrets.create(parent / 'wide', 'http://localhost:8000', 'http://localhost:3000')
            for value in ['http://example.test', 'https://u:p@example.test', 'https://example.test/$X',
                          'https://example.test\nX=y', 'https://example.test?token=x']:
                with self.assertRaises(ValueError): secrets.url(value)

    def test_compose_config_without_daemon(self):
        # This command only parses configuration. No pull/create/up/exec/run calls.
        if subprocess.run(['sh', '-c', 'command -v docker >/dev/null']).returncode:
            self.skipTest('Docker CLI unavailable; structural checks still run')
        if subprocess.run(['docker', 'compose', 'version'], capture_output=True).returncode:
            self.skipTest('Compose plugin unavailable')
        with tempfile.TemporaryDirectory() as temp:
            target = secrets.create(Path(temp) / 'private', 'http://localhost:8000', 'http://localhost:3000')
            command = ['docker', 'compose', '--env-file', str(target), '-f', str(config.BASE / 'compose.json'),
                       '--profile', '*', 'config', '--quiet']
            clean = {k: v for k, v in os.environ.items() if k in ('PATH', 'HOME', 'DOCKER_CONFIG')}
            result = subprocess.run(command, env=clean, capture_output=True)
            self.assertEqual(result.returncode, 0, 'Compose rejected configuration (output redacted)')
            self.assertEqual(result.stderr, b'', 'Compose emitted warnings (output redacted)')
