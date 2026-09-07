from pathlib import Path
import tempfile
import unittest
from test_supabase import module

bundle = module('migration_bundle')


class MigrationBundleTests(unittest.TestCase):
    def fixture(self, directory):
        for name in bundle.FILES:
            p = Path(directory) / name
            p.write_text('-- synthetic non-secret fixture\n')
            p.chmod(0o600)

    def test_tamper_and_no_overwrite(self):
        with tempfile.TemporaryDirectory() as temp:
            self.fixture(temp)
            bundle.seal(temp)
            bundle.verify(temp)
            with self.assertRaises(FileExistsError): bundle.seal(temp)
            (Path(temp) / 'schema.sql').write_text('-- changed\n')
            with self.assertRaises(ValueError): bundle.verify(temp)

    def test_refuses_unsafe_export(self):
        with tempfile.TemporaryDirectory() as temp:
            self.fixture(temp)
            p = Path(temp) / 'roles.sql'
            p.chmod(0o644)
            with self.assertRaises(ValueError): bundle.seal(temp)
            p.unlink()
            p.symlink_to(Path(temp) / 'schema.sql')
            with self.assertRaises(ValueError): bundle.seal(temp)
