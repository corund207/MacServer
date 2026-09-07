-- ISOLATED TEST DATABASE ONLY, after establish_api_boundary. Explicit approval
-- required to run. Synthetic rows; all DDL/data rolls back. Not a production probe.
\set ON_ERROR_STOP on
BEGIN;
SET LOCAL statement_timeout = '10s';
SET LOCAL lock_timeout = '2s';
SET LOCAL ROLE macserver_owner;
CREATE TABLE api.phase2_rls_probe (id integer PRIMARY KEY, owner_id uuid NOT NULL, body text);
ALTER TABLE api.phase2_rls_probe ENABLE ROW LEVEL SECURITY;
ALTER TABLE api.phase2_rls_probe FORCE ROW LEVEL SECURITY;
CREATE POLICY read_own ON api.phase2_rls_probe FOR SELECT TO authenticated
  USING ((SELECT auth.uid()) = owner_id);
CREATE POLICY insert_own ON api.phase2_rls_probe FOR INSERT TO authenticated
  WITH CHECK ((SELECT auth.uid()) = owner_id);
CREATE POLICY update_own ON api.phase2_rls_probe FOR UPDATE TO authenticated
  USING ((SELECT auth.uid()) = owner_id) WITH CHECK ((SELECT auth.uid()) = owner_id);
GRANT SELECT, INSERT, UPDATE ON api.phase2_rls_probe TO authenticated;
SET LOCAL ROLE authenticated;
SELECT set_config('request.jwt.claims', '{"sub":"11111111-1111-4111-8111-111111111111","role":"authenticated"}', true);
INSERT INTO api.phase2_rls_probe VALUES (1, '11111111-1111-4111-8111-111111111111', 'synthetic');
DO $$ BEGIN
  IF (SELECT count(*) FROM api.phase2_rls_probe) <> 1 THEN RAISE EXCEPTION 'owner SELECT failed'; END IF;
  BEGIN
    UPDATE api.phase2_rls_probe SET owner_id='22222222-2222-4222-8222-222222222222' WHERE id=1;
    RAISE EXCEPTION 'owner reassignment succeeded';
  EXCEPTION WHEN insufficient_privilege THEN NULL; END;
  BEGIN
    INSERT INTO api.phase2_rls_probe VALUES (2, '22222222-2222-4222-8222-222222222222', 'denied');
    RAISE EXCEPTION 'foreign insert succeeded';
  EXCEPTION WHEN insufficient_privilege THEN NULL; END;
END $$;
SELECT set_config('request.jwt.claims', '{"sub":"22222222-2222-4222-8222-222222222222","role":"authenticated"}', true);
DO $$ DECLARE changed integer; BEGIN
  IF (SELECT count(*) FROM api.phase2_rls_probe) <> 0 THEN RAISE EXCEPTION 'cross-owner read'; END IF;
  UPDATE api.phase2_rls_probe SET body='denied' WHERE id=1;
  GET DIAGNOSTICS changed = ROW_COUNT;
  IF changed <> 0 THEN RAISE EXCEPTION 'cross-owner update'; END IF;
END $$;
SET LOCAL ROLE anon;
DO $$ BEGIN
  BEGIN
    PERFORM * FROM api.phase2_rls_probe;
    RAISE EXCEPTION 'anon SELECT succeeded';
  EXCEPTION WHEN insufficient_privilege THEN NULL; END;
END $$;
RESET ROLE;
ROLLBACK;
