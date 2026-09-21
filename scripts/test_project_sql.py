#!/usr/bin/env python3
"""Exercise generated RLS in the disposable, network-isolated CI Postgres container."""
import subprocess
import tempfile
from pathlib import Path
from project import create

ROOT = Path(__file__).resolve().parents[1]


def main():
    with tempfile.TemporaryDirectory(prefix="macserver-sql-") as temporary:
        bundle = create("testapp", "test_items", "https://api.example.test", "https://app.example.test", Path(temporary) / "project")
        fixture = """
CREATE ROLE anon NOLOGIN;
CREATE ROLE authenticated NOLOGIN;
CREATE ROLE service_role NOLOGIN;
CREATE SCHEMA auth;
CREATE TABLE auth.users (id uuid PRIMARY KEY);
CREATE FUNCTION auth.uid() RETURNS uuid LANGUAGE sql STABLE AS
  $$ SELECT nullif(current_setting('request.jwt.claim.sub', true), '')::uuid $$;
GRANT USAGE ON SCHEMA auth TO authenticated;
GRANT EXECUTE ON FUNCTION auth.uid() TO authenticated;
INSERT INTO auth.users VALUES ('11111111-1111-4111-8111-111111111111'), ('22222222-2222-4222-8222-222222222222');
"""
        assertions = """
SET ROLE authenticated;
SET request.jwt.claim.sub = '11111111-1111-4111-8111-111111111111';
INSERT INTO api.test_items (name) VALUES ('User A');
DO $$ BEGIN
  IF (SELECT count(*) FROM api.test_items) <> 1 THEN RAISE EXCEPTION 'own row unreadable'; END IF;
END $$;
SET request.jwt.claim.sub = '22222222-2222-4222-8222-222222222222';
DO $$ BEGIN
  IF (SELECT count(*) FROM api.test_items) <> 0 THEN RAISE EXCEPTION 'foreign row leaked'; END IF;
  UPDATE api.test_items SET name = 'stolen';
  IF FOUND THEN RAISE EXCEPTION 'foreign update allowed'; END IF;
  DELETE FROM api.test_items;
  IF FOUND THEN RAISE EXCEPTION 'foreign delete allowed'; END IF;
  BEGIN
    INSERT INTO api.test_items (name, owner_id) VALUES ('stolen', '11111111-1111-4111-8111-111111111111');
    RAISE EXCEPTION 'foreign ownership insert allowed';
  EXCEPTION WHEN insufficient_privilege THEN NULL;
  END;
END $$;
INSERT INTO api.test_items (name) VALUES ('User B');
DO $$ BEGIN
  BEGIN
    UPDATE api.test_items SET owner_id = '11111111-1111-4111-8111-111111111111';
    RAISE EXCEPTION 'owner reassignment allowed';
  EXCEPTION WHEN insufficient_privilege THEN NULL;
  END;
END $$;
RESET ROLE;
SET ROLE anon;
DO $$ BEGIN
  BEGIN
    PERFORM * FROM api.test_items;
    RAISE EXCEPTION 'anonymous read allowed';
  EXCEPTION WHEN insufficient_privilege THEN NULL;
  END;
END $$;
RESET ROLE;
"""
        sql = fixture + (ROOT / "supabase/migrations/20260907091838_establish_api_boundary.sql").read_text(encoding="utf-8") + (bundle / "schema.sql").read_text(encoding="utf-8") + assertions
        subprocess.run(["docker", "exec", "-i", "macserver-schema-test", "psql", "-U", "postgres", "-v", "ON_ERROR_STOP=1"], input=sql, text=True, check=True, timeout=30)
    print("PASS: generated schema installs; own rows work and foreign/anonymous access is denied.")


if __name__ == "__main__":
    main()
