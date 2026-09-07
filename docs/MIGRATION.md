# Managed Supabase migration runbook

Status: offline foundation, no production connection or SQL execution performed.
A dump is not a verified backup. Use a new isolated private target and rehearse
before cutover. Never import into a populated deployment or reuse production
volumes. Every import, migration application, credential rotation and cutover needs
explicit operator confirmation immediately beforehand and a tested rollback path.

## Inventory and prerequisites

Record source/target PostgreSQL, Auth, Storage and extension versions; object sizes,
row counts, roles/memberships/default privileges, schema ownership, exposed schemas,
policies, views, RPCs, triggers, scheduled jobs, replication publications and slots.
Inventory app dependencies, expected JWT issuer/audience and session behavior, OAuth
redirects, SMTP, Storage bucket visibility and policies, function code/dependencies,
secret *names* and destinations (not values). Do not copy `user_metadata` into an
authorization model. Document the actual tenant membership/ownership model.

One target deployment has one Auth/key boundary. Do not merge independent managed
projects by importing their internal schemas together. Initially only `api` is
exposed by PostgREST. Existing `public` clients need a reviewed migration of objects
and schema selection, or an explicit audited exposed-schema change. Moving tables
can affect qualified SQL, dependencies, grants, functions and clients; this runbook
does not automatically rename them. Separate untrusted apps into separate stacks.

Before production data: verified encrypted rotating DB and Storage backups on the
250 GB drive, capacity/retention checks, absent-drive alert, offline secret escrow,
an off-device copy, and an isolated restore drill with measured recovery time.
Include PostgreSQL data/logical exports, db-config/pgsodium keys, Storage bytes,
function bundles, configuration and secrets. A database-only export lacks objects
and function code. Recovery implementation remains a Phase 5 prerequisite.

## Export (operator-only after source access confirmation)

The tooling pin is Supabase CLI 2.116.0; archive URL/SHA-256 are recorded in
`infra/supabase/release.lock.json`. The archive checksum and CLI help were verified
in this task; no DB commands ran. Discover flags again if changing versions:

```sh
supabase --version
supabase db dump --help
supabase db advisors --help
supabase migration new --help
```

Choose a direct or session-pooler connection, never a transaction pooler for a dump.
Use a password-free connection URI with `sslmode=verify-full` and a trusted CA.
Supply its password using a private mode-0600 PGPASSFILE, matching host, port, DB and
user exactly. The pinned CLI's explicit `--db-url` path uses pgconn.ParseConfig.
Verify credential-file resolution on an authorized disposable source before using
production. Do not place credentials in command arguments, Git, shell history or
logs; do not use `--debug` or `--dry-run` with credentials (the latter can expand
secrets into its printed script). Docker administrators can inspect export-container
credentials. CLI dumping requires Docker and creates a transient container; it is
not part of this task's offline tests.

On an authorized export workstation, in a NEW mode-0700 directory outside Git, set
umask 077 and an operator-approved password-free SOURCE_DB_URL and PGPASSFILE.
Ensure the three destination names do not exist; CLI dump overwrites existing files.
Freeze writes/jobs for the final export: independent dump calls do not share one
snapshot. Run these only after the preceding authorization and backup checks:

```sh
umask 077
supabase db dump --db-url "$SOURCE_DB_URL" --role-only --file roles.sql
supabase db dump --db-url "$SOURCE_DB_URL" --file schema.sql
supabase db dump --db-url "$SOURCE_DB_URL" --data-only --use-copy --file data.sql
```

Use the CLI's Supabase-aware filtering, not a blind pg_dumpall restore of managed
system roles. Review output privately for missing internal Auth data, incompatible
extensions/columns, privileged definitions and hard-coded secrets. Record excluded
objects; never silently discard errors or COPY sections. Protect all SQL exports as
sensitive. Source reads remain scoped, timed and non-destructive.

From the repository, seal the three exported SQL files and later verify them:

```sh
python3 scripts/migration_bundle.py seal /secure/operator/export-directory
python3 scripts/migration_bundle.py verify /secure/operator/export-directory
```

Expected: PASS. The manifest hashes nonempty regular 0600 files and refuses symlinks
or overwrite. It detects byte changes, not SQL safety, logical completeness or
malicious substitution of both files and manifest. Store its trusted hash separately
in encrypted backup evidence. Do not run exported SQL to inspect it.

## Schema and grants

`supabase/migrations/20260907091838_establish_api_boundary.sql` was created using
`supabase migration new establish_api_boundary`. It is a reviewed new-target template,
not an applied migration or a managed-schema import. It creates a NOLOGIN owner
without superuser/BYPASSRLS, api/app_private schemas, usage grants, and restrictive
owner defaults. It intentionally fails on name collisions instead of taking over
existing objects. It creates no app tables. Global default function EXECUTE is
revoked only for the new application owner; schema-local revocation alone would not
remove PostgreSQL's global PUBLIC default.

Reconcile this migration with exported role/schema definitions before import; do not
blindly apply both when names collide. Future app DDL runs as macserver_owner.
Explicitly grant each table action after enabling RLS. Policies must check actual
ownership/tenant membership, with SELECT supporting UPDATE and both USING and WITH
CHECK. Anonymous access is denied unless specifically reviewed. Use security_invoker
views; keep privileged RPCs private, revoke PUBLIC execution and fix search_path.
Review inherited memberships too: RLS does not constrain a superuser or BYPASSRLS
role, and owners normally bypass it unless FORCE RLS applies. Never give application
clients the owner or authenticator credentials.

## Import rehearsal (not executed here)

Inspect dumps as executable privileged code, including psql meta-commands, role
changes, SECURITY DEFINER functions and triggers. Provision an isolated target from
the pinned official initialization first; do not mount these exports into container
init scripts. Never point PG17 at older physical data files. Confirm source/target
extension and Auth/Storage schema compatibility. Keep original exports immutable;
make reviewed, hashed import copies for required compatibility changes.

After explicit confirmation and a tested target rollback, connect using a private
PGSERVICE/PGPASSFILE through a reviewed administrative path. No Postgres host port
is published by this repository. A future operator runner on the internal network
must be separately reviewed; do not invent a connection to localhost:5432.
Use `psql -X --single-transaction --set ON_ERROR_STOP=1` with the reviewed roles,
schema and data files in that order. The official restoration procedure sets
`session_replication_role=replica` for data import to avoid replaying triggers such
as encryption hooks. Treat this as privileged constraint/trigger bypass: separately
approve it, scope it to the import transaction, and check FK integrity, sequences,
triggers and row counts afterward. Stop on any error; do not disable transactional
safety to make an import appear successful. The provided schema migration already
contains BEGIN/COMMIT; do not nest it inside that restore command.

## Verification and remaining test gates

After an approved isolated restore, capture only metadata with the configured
PGSERVICE/PGPASSFILE (psql prints one JSON value because quiet/unaligned/tuples-only
suppress command tags):

```sh
umask 077
psql -X -qAt --set ON_ERROR_STOP=1 --file database/verification/catalog.sql > /secure/operator/catalog.json
python3 scripts/schema_audit.py /secure/operator/catalog.json
```

Expected: structural PASS or actionable FAIL with exit 1. The query uses a read-only
transaction and bounded timeouts. Captured names and policy expressions may be
sensitive; keep the output private. The auditor checks exposed RLS, invoker views,
unsafe roles, public schema CREATE, editable claims, UPDATE policy structure and
client execution of private/privileged functions. It cannot prove policy semantics,
inherited grants, extension behavior, dynamic SQL, or effective gateway exposure.
It currently audits fixed api/app_private/storage schemas; update query and tests
together before changing the exposed-schema allowlist.

On the isolated disposable test database only, after confirmation, run:

```sh
psql -X --set ON_ERROR_STOP=1 --file database/verification/rls_negative.sql
```

Expected: no assertion errors and final ROLLBACK. The test creates one synthetic
table inside a transaction, verifies owner access and rejects cross-owner reads,
updates, inserts, reassignment and anon access, then rolls everything back. It is a
reference policy test, not certification of migrated app policies. SQL parsing,
execution and this RLS probe are unverified until a target is authorized. Run the
pinned CLI security advisors against that target as well, with failure on findings.

For each real app, repeat negative cases through REST using two users from different
tenants: missing/expired/forged JWT, unauthenticated anon, forged user_metadata,
revoked membership, foreign SELECT/INSERT/UPDATE/DELETE and RPC/view access. Confirm
permission errors versus empty results as appropriate; never use service_role for
these tests. Test exact grants, exposed schemas, owner behavior and row counts.

## Storage, functions, cutover and rollback

Storage requires separate byte transfer using authenticated Storage APIs with
pagination and bounded concurrency. Inventory every bucket/key, size, content type,
metadata and SHA-256; compare counts/bytes/checksums on the target. Do not treat SQL
Storage metadata as object bytes or write arbitrary files into its backend layout.
Start buckets private; review storage.objects policies for bucket plus owner/tenant
and path rules. Upsert requires INSERT, SELECT and UPDATE. Test signed URL expiry,
foreign object read/write/delete, overwrite and listing without service_role. Audit
public buckets separately and avoid enabling S3 credentials unless required.

Copy reviewed function source and locally bundled exact dependency versions into
`infra/supabase/functions/<name>`; preserve the appliance main dispatcher. Add the
name to its empty allowlist in a reviewed commit. Restart/deploy only in an approved
maintenance window. Self-hosted Functions are filesystem deployments, not managed
platform deploy API targets. Verify worker auth, per-operation scopes and JWT
forwarding. Inventory secrets separately; the current dispatcher deliberately passes
only API URL and anon key. New function secrets require an explicit allowlist design.

Cut over only after successful rehearsal, all per-app negative tests, object parity,
function tests, restored backup evidence and authorized private/public routing.
Quiesce source writes, take final exports, import to the isolated target, compare,
then change app URLs/keys and OAuth callbacks. Issue fresh sessions; managed JWTs
are not assumed valid with new keys. Keep the old source intact and read-only for
an agreed rollback window. Never dual-write without a reconciliation design.

On failure before target writes, restore clients to the unchanged source after
checking credentials. After target writes, freeze both sides, preserve a fresh target
backup, reconcile the delta with reviewed tooling and confirmation before reverting.
Never discard post-cutover writes or assume an old image undoes database migrations.

References: [official restore guide](https://supabase.com/docs/guides/self-hosting/restore-from-platform),
[official self-hosted Functions](https://supabase.com/docs/guides/self-hosting/self-hosted-functions),
[Storage access control](https://supabase.com/docs/guides/storage/security/access-control).
Some restore-guide PostgreSQL defaults lag the pinned distribution; inspect actual
versions and schema compatibility instead of assuming downgrade safety.
