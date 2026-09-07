# Phase 3 — read-only foundation checkpoint

2026-09-07. The user/coordinator requested a terminal checkpoint at 76% five-hour
usage and explicitly requested a local implementation commit after focused checks
passed. This commit delivers a read-only foundation, not the complete Phase 3
prompt or a deployed/production-qualified admin app. Do not start Phase 4.

Intake read all local prompts, README, context, nested AGENTS.md and data-service
and migration runbooks. Verified Phase 2 history through 90f4db7 and its 17 tests.
The worktree initially was clean; main has no remote/upstream.

Implemented in apps/admin:
- Exact dependency pins and lockfile; Fastify 5.12.3, TypeScript 7.0.2; Node >=24
  <27, tested locally on 26.8.1. Server and browser production compilation.
- Fixed read-only /usr/bin/tailscale status/whois calls; real socket-peer daemon
  identity, user/device matching, tagged-device rejection, assigned private bind.
  Forwarded identity headers are rejected. No proxy/Serve/Funnel trust.
- Separate random per-user administration tokens, scrypt verifier, protected
  credential files, 15-minute absolute user/device-bound sessions, secure cookies,
  login and session CSRF checks, canonical Host, CSP and security headers, bounded
  request/login/session capacity, sanitized errors and no request-body logging.
- Private append-and-sync fixed-schema audit, latest 200 in-process events,
  16 MiB durable-file refusal threshold. No tokens/SQL/file content in events.
- Optional curated-root file listing and <=1 MiB text attachments; descriptor-
  relative no-follow traversal, symlink/hardlink/hidden/path/type/size refusal.
- Observed CPU/RAM/root-filesystem/uptime/aggregate-network metrics and bounded
  sample history. Missing temperature, services and backup evidence are unavailable.
- Responsive themed interface with overview, alerts, service state, graphs,
  Tailscale, backups, files, database, updates, audit filtering and configuration.
  Unavailable actions are visibly disabled and execution endpoints return 501.
- Private TLS and exclusive credential-provisioning entry points, not executed.

Not implemented: SQL execution/database metadata connection; service/raw-log
collectors; privileged helper; file writes; backup/restore/restart/update execution;
re-authentication for those actions; browser terminal (intentionally excluded).
Disabled controls do not constitute delivery of those capabilities.

Actual validation:
- cd apps/admin && npm test: PASS, both TypeScript builds and all 8 focused tests.
  Covers private bind, daemon identity shape, independent authorization, session
  expiry/device binding, login/session CSRF, security headers, static assets,
  offline telemetry, unavailable operations, logout, audit content/failure,
  login limits, path/symlink/hardlink/size and credential-file protections.
- npm audit --omit=dev: zero reported vulnerabilities; subsequent lock refresh
  also reported zero across 59 packages. Not a source/security certification.
- python3 scripts/validate.py and scripts/supabase_config.py: PASS.
- python3 -m unittest discover -s tests -v: PASS, 17 tests, including offline
  Compose config. No Docker daemon use, services, SQL or production connections.
- Whitespace checks pass. Source reviewed at checkpoint; full security review
  remains outstanding. Initial unknown-error TypeScript typing failure was fixed.

Unexecuted checks and residual work: browser/mobile/theme/stale-state testing,
axe accessibility (dependencies exist but no suite/config yet), production TLS
integration, versioned Tailscale identity/expiry review, systemd deployment example,
CI admin checks, provisioning/audit-capacity/logout-failure/file-route integration
coverage, and full threat-model/operator/rollback review. Live ACLs, firewall,
certificate renewal, Debian/T2 and identity behavior remain target-only gates.

Security assumptions: unprivileged service account, no Docker/sudo/operator grant,
root-controlled code/configuration and read-only curated exports. Downloads are
not redacted; never allow secret/live database/backup roots. Audit is not resistant
to compromise of its service account or root and needs external archival. The
current identity parser needs upstream key-expiry/field review before deployment.
Metrics describe the process host, not a remotely inferred appliance; memory
includes cache, and network aggregates may double-count virtual interfaces.

Delivery: one focused local read-only foundation commit at the user's explicit
checkpoint request, using configured Git identity and no co-author trailer.
Resolve the implementation and checkpoint commit hashes with git log. Push skipped:
no remote/upstream exists. Phase 2 and every recovery/network/SQL/runtime gate are
preserved. No real secrets, host changes, listeners or appliance services created.
Phase 3 remains incomplete; this task ends safely at the checkpoint.

## Resumed after reset — 2026-09-07

The user authorized continuation after the usage reset. Tool verification showed
7% five-hour / 39% weekly usage, clean main at 0281a37 and no remote. This
supersedes the historical stop-only instructions above. Continue the remaining
Phase 3 verification/documentation; no Phase 4 work. Identity hardening and
additional synthetic tests are in progress; no new pass claimed yet.

### Resumed unit 1: identity and failure boundaries

Complete and validated: v1.102.3 daemon/status/expiry/device contract (official
versioned source hashes in docs/ADMIN-IDENTITY.md), bounded CLI concurrency, audit
capacity check before append, nonblocking special-file refusal, complete bounded
file reads, file-denial audit, stricter provisioning parent checks. Added synthetic
provisioning, audit, file-route and HTTPS tests. npm test PASS: both builds and all
13 tests. TLS test uses an ephemeral certificate and loopback listener only, then
closes it; no production service or tailnet query. Initial TLS fixture SNI/Host
mismatch was corrected so certificate trust and HTTP Host are tested separately.
Remaining resumed work: browser/a11y, deployment/operator/threat-model and CI.
Push skipped because no remote/upstream exists.
