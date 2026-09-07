#!/usr/bin/env python3
"""Audit a private catalog.json captured with database/verification/catalog.sql.
Offline only. Structural checks supplement, never replace, per-app RLS tests.
"""
import argparse
import json
from pathlib import Path


def audit(data):
    if data.get('format') != 1 or any(not isinstance(data.get(k), list) for k in
            ('schemas', 'roles', 'relations', 'policies', 'functions', 'extensions')):
        raise ValueError('unsupported or incomplete catalog format')
    findings = []
    schemas = {s['name']: s for s in data['schemas']}
    for name in ('api', 'app_private'):
        if name not in schemas: findings.append(f'missing schema: {name}')
    for schema in data['schemas']:
        if schema['public_create']: findings.append(f'PUBLIC can create in schema: {schema["name"]}')
    roles = {r['name']: r for r in data['roles']}
    for name in ('anon', 'authenticated', 'macserver_owner'):
        role = roles.get(name)
        if not role or role['superuser'] or role['bypassrls'] or role['login']:
            findings.append(f'unsafe or missing application role: {name}')
    for relation in data['relations']:
        label = relation['schema'] + '.' + relation['name']
        if relation['kind'] in ('r', 'p') and not relation['rls']:
            findings.append(f'RLS disabled: {label}')
        if relation['kind'] == 'v' and not relation['invoker']:
            findings.append(f'view lacks security_invoker: {label}')
        if relation['kind'] in ('m', 'f'):
            findings.append(f'unsupported exposed relation requires review: {label}')
    for policy in data['policies']:
        label = policy['schema'] + '.' + policy['table'] + ':' + policy['name']
        if 'user_metadata' in ((policy['using'] or '') + (policy['check'] or '')).lower():
            findings.append(f'editable metadata used by policy: {label}')
        if policy['command'] in ('UPDATE', 'ALL'):
            if not policy['using'] or not policy['check']:
                findings.append(f'UPDATE needs explicit USING and WITH CHECK: {label}')
            if not any(p['schema'] == policy['schema'] and p['table'] == policy['table']
                       and p['command'] in ('SELECT', 'ALL') for p in data['policies']):
                findings.append(f'UPDATE has no SELECT policy: {label}')
    for function in data['functions']:
        if function['client_execute'] and (function['definer'] or function['schema'] == 'app_private'):
            findings.append(f'privileged/private function callable by clients: {function["schema"]}.{function["name"]}')
    return findings


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('catalog', type=Path)
    args = parser.parse_args()
    findings = audit(json.loads(args.catalog.read_text()))
    for finding in findings: print('FAIL: ' + finding)
    if findings: raise SystemExit(1)
    print('PASS: catalog structural checks; per-app authorization tests still required')


if __name__ == '__main__': main()
