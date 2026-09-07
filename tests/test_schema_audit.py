import copy
import unittest
from test_supabase import module

audit = module('schema_audit').audit


def catalog():
    return {'format': 1, 'schemas': [{'name': n, 'public_create': False} for n in ('api','app_private')],
            'roles': [{'name': n, 'superuser': False, 'bypassrls': False, 'login': False}
                      for n in ('anon','authenticated','macserver_owner')],
            'relations': [{'schema': 'api', 'name': 'items', 'kind': 'r', 'rls': True, 'invoker': False}],
            'policies': [], 'functions': [], 'extensions': []}


class SchemaAuditTests(unittest.TestCase):
    def test_baseline_and_incomplete_capture(self):
        self.assertEqual(audit(catalog()), [])
        with self.assertRaises(ValueError): audit({'format': 1})

    def test_rls_bypass_and_public_ddl(self):
        data = catalog()
        data['relations'][0]['rls'] = False
        data['roles'][1]['bypassrls'] = True
        data['schemas'][0]['public_create'] = True
        self.assertEqual(len(audit(data)), 3)

    def test_view_and_definer_exposure(self):
        data = catalog()
        data['relations'][0]['kind'] = 'v'
        data['functions'] = [{'schema': 'api', 'name': 'leak', 'definer': True, 'client_execute': True}]
        self.assertEqual(len(audit(data)), 2)
        data['relations'][0]['invoker'] = True
        data['functions'][0]['client_execute'] = False
        self.assertEqual(audit(data), [])

    def test_policy_update_and_editable_claims(self):
        data = catalog()
        data['policies'] = [{'schema': 'api', 'table': 'items', 'name': 'update', 'command': 'UPDATE',
                             'using': "auth.jwt()->'user_metadata' IS NOT NULL", 'check': None}]
        self.assertEqual(len(audit(data)), 3)
        data['policies'][0].update(using='auth.uid() = owner_id', check='auth.uid() = owner_id')
        read = copy.deepcopy(data['policies'][0]); read.update(name='read', command='SELECT')
        data['policies'].append(read)
        self.assertEqual(audit(data), [])
