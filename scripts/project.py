#!/usr/bin/env python3
"""Generate a new private project setup bundle. Never connects, installs, or imports."""
import argparse
import json
import os
from pathlib import Path
import re
import secrets
from urllib.parse import urlsplit


def origin(value):
    parsed = urlsplit(value)
    if (parsed.scheme != "https" or not parsed.hostname or value != "https://" + parsed.hostname or parsed.username or parsed.password
            or parsed.path or parsed.query or parsed.fragment or parsed.netloc != parsed.hostname
            or not re.fullmatch(r"[a-z0-9.-]+", parsed.hostname) or "." not in parsed.hostname):
        raise ValueError("use an HTTPS origin such as https://app.example.test, without a path or port")
    return value


def create(name, table, api_url, site_url, output):
    for value in (name, table):
        if not re.fullmatch(r"[a-z][a-z0-9_]{0,62}", value) or value in {"rpc", "admin"}:
            raise ValueError("names must use lowercase letters, digits and underscores")
    origin(api_url); origin(site_url)
    output = Path(output).absolute()
    if output.exists() or output.is_symlink() or any(p.is_symlink() for p in output.parents):
        raise ValueError("choose a new directory below a trusted existing parent")
    if not output.parent.is_dir():
        raise ValueError("parent directory must already exist")
    output.mkdir(mode=0o700)
    key = "ms_pub_" + secrets.token_urlsafe(32)
    config = {"format": 1, "apps": [{"id": name, "key": key, "origins": [site_url],
              "tables": {table: ["GET", "HEAD", "POST", "PATCH", "DELETE"]}, "requestsPerMinute": 600,
              "clientRequestsPerMinute": 60}]}
    sql = f"""-- Review before applying to a NEW project. This performs no destructive SQL.
-- Prerequisite: apply the repository's establish_api_boundary migration first.
BEGIN;
GRANT REFERENCES (id) ON auth.users TO macserver_owner;
SET LOCAL ROLE macserver_owner;
CREATE TABLE api.{table} (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  owner_id uuid NOT NULL DEFAULT auth.uid() REFERENCES auth.users(id),
  name text NOT NULL CHECK (char_length(name) BETWEEN 1 AND 200),
  created_at timestamptz NOT NULL DEFAULT now()
);
ALTER TABLE api.{table} ENABLE ROW LEVEL SECURITY;
ALTER TABLE api.{table} FORCE ROW LEVEL SECURITY;
CREATE POLICY owner_access ON api.{table} FOR ALL TO authenticated
  USING ((SELECT auth.uid()) = owner_id)
  WITH CHECK ((SELECT auth.uid()) = owner_id);
GRANT USAGE ON SCHEMA api TO authenticated;
GRANT SELECT, INSERT, UPDATE, DELETE ON api.{table} TO authenticated;
COMMIT;
"""
    client = f"""import {{ createClient }} from '@supabase/supabase-js'

// Public, scoped app key. User sessions and RLS authorize every data request.
export const supabase = createClient(
  '{api_url}',
  '{key}',
  {{ db: {{ schema: 'api' }} }}
)
"""
    instructions = f"""# {name} setup bundle

No services were started and no schema was imported.

1. Follow docs/PUBLIC-APPS.md to configure the tunnel and private ingress config.
2. Review schema.sql, apply the repository API boundary migration first, then
   apply this schema to an empty target using psql with ON_ERROR_STOP=1.
3. Provision two synthetic users privately. Test each user's CRUD and cross-user
   denial before using production data. Login/signup admin routes are not public.
4. Install ingress.json at /etc/macserver/ingress.json as root:65532 mode 0640.
5. Use client.ts in your app. Configure its site URL in Auth privately.

To add another app on this same trust boundary, merge its app entry into the
existing private ingress.json apps array. Preserve unique keys and IDs; restart
only ingress after validation. Never overwrite an existing project's config.
Unrelated trust domains need separate appliances/stacks; shared Auth is not isolation.
"""
    for filename, content in {"ingress.json": json.dumps(config, indent=2) + "\n", "schema.sql": sql,
                              "client.ts": client, "README.md": instructions}.items():
        fd = os.open(output / filename, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as handle:
            handle.write(content)
    return output


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ("name", "table", "api-url", "origin", "output"):
        parser.add_argument("--" + name, required=True)
    args = parser.parse_args()
    try:
        create(args.name, args.table, args.api_url, args.origin, args.output)
    except (ValueError, OSError) as error:
        parser.exit(2, f"Project setup refused: {error}\n")
    print("Project bundle created. Review its README; no services or data were changed.")


if __name__ == "__main__":
    main()
