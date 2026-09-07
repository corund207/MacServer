# Phase 2 status

2026-09-07 — safe offline services and migration foundation delivered. Not deployed
or runtime-qualified. No production connection, SQL execution or service startup.

User resume authorization permits local commits when no upstream exists. Pushes
are skipped because `git remote -v` is empty; no remote was invented.

Completed units:
- a790d54: record intake and local delivery authorization.
- 88854a0: pinned private Supabase configuration and secret bootstrap.
- b593d04: normalize three upstream comment-line spaces, retain provenance hashes
  and restore normal whitespace checks for every source file.
- Migration foundation: separate NOLOGIN application owner/schema migration created
  with verified CLI 2.116.0; offline catalog auditor; rollback-only synthetic RLS
  probe; tamper-detecting export manifest tooling; managed migration runbook.

Official service release: self-hosted/v0.8.0 at
241bb11c0627f2981746d37033f57dbfa81d29b0. Source hashes, linux/amd64 image digests,
Debian 13 Docker package pins and migration CLI archive checksum are recorded.
All Compose profiles publish no ports. Only api is configured for REST exposure;
Storage, Realtime, Functions and management are optional. Functions start with an
empty allowlist and receive no database/service-role/signing secret.

Validation: scripts/validate.py and scripts/supabase_config.py pass; all 17 Python
tests pass, including all-profile Docker Compose config on CLI 5.5.0 without daemon
use, source integrity, secret permissions/signatures/refusals, catalog negative
fixtures and bundle tamper/symlink/overwrite refusal. Whitespace checks pass.
The Debian target Compose pin is 5.5.1 and has not been installed/tested here.

Runtime gates remain: SQL parsing/execution and RLS probe on an approved isolated
Supabase target; actual app schema/grant/policy inventory and two-user API tests;
Auth/Storage compatibility and object parity; dispatcher/Edge Runtime tests; pinned
image startup/health/resource measurements; Docker/firewall/Tailscale forwarding;
private ingress and narrow egress; scoped app authorization/public route limits;
Debian/T2 hardware qualification; encrypted backups and isolated restores (Phase 5).
No RLS certification, production cutover or recovery-readiness claim is made.

Read docs/DATA-SERVICES.md and docs/MIGRATION.md for exact commands, limitations,
secret handling, one-project boundaries and rollback. No Phase 3 code was started.
The next-task prompt in NEXT-PHASE.md preserves all outstanding runtime gates.
