-- Metadata only. Use psql -X -q -A -t -v ON_ERROR_STOP=1 -f this-file with a
-- separately approved PGSERVICE/PGPASSFILE. Never includes row data or passwords.
BEGIN READ ONLY;
SET LOCAL statement_timeout = '10s';
SET LOCAL lock_timeout = '2s';
SELECT jsonb_build_object(
  'format', 1,
  'schemas', COALESCE((SELECT jsonb_agg(jsonb_build_object('name', n.nspname,
    'public_create', EXISTS (SELECT 1 FROM aclexplode(COALESCE(n.nspacl, acldefault('n', n.nspowner))) a
                            WHERE a.grantee = 0 AND a.privilege_type = 'CREATE')))
    FROM pg_namespace n WHERE n.nspname IN ('api', 'app_private', 'storage')), '[]'::jsonb),
  'roles', COALESCE((SELECT jsonb_agg(jsonb_build_object('name', rolname,
    'superuser', rolsuper, 'bypassrls', rolbypassrls, 'login', rolcanlogin))
    FROM pg_roles WHERE rolname IN ('anon','authenticated','service_role','macserver_owner')), '[]'::jsonb),
  'relations', COALESCE((SELECT jsonb_agg(jsonb_build_object('schema', n.nspname,
    'name', c.relname, 'kind', c.relkind, 'rls', c.relrowsecurity,
    'invoker', 'security_invoker=true' = ANY(COALESCE(c.reloptions, ARRAY[]::text[]))))
    FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace
    WHERE (n.nspname='api' AND c.relkind IN ('r','p','v','m','f'))
       OR (n.nspname='storage' AND c.relname IN ('objects','buckets'))), '[]'::jsonb),
  'policies', COALESCE((SELECT jsonb_agg(jsonb_build_object('schema', schemaname,
    'table', tablename, 'name', policyname, 'command', cmd, 'using', qual,
    'check', with_check, 'roles', roles))
    FROM pg_policies WHERE schemaname IN ('api','storage')), '[]'::jsonb),
  'functions', COALESCE((SELECT jsonb_agg(jsonb_build_object('schema', n.nspname,
    'name', p.proname, 'definer', p.prosecdef,
    'client_execute', has_function_privilege('anon', p.oid, 'EXECUTE')
                   OR has_function_privilege('authenticated', p.oid, 'EXECUTE')))
    FROM pg_proc p JOIN pg_namespace n ON n.oid=p.pronamespace
    WHERE n.nspname IN ('api','app_private')), '[]'::jsonb),
  'extensions', COALESCE((SELECT jsonb_agg(jsonb_build_object('name', extname, 'version', extversion))
    FROM pg_extension), '[]'::jsonb)
);
ROLLBACK;
