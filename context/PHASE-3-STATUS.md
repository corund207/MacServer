# Phase 3 — verified read-only slice

2026-09-07. The authorized resumed verification/documentation work is complete.
This is a delivered read-only slice, NOT the entire admin prompt, an installed
appliance, working recovery or production readiness. Phase 4 has not started.

## Delivery

- 0281a37: private read-only admin foundation and initial usage checkpoint.
- 684758a: versioned Tailscale identity/expiry and failure-boundary hardening.
- d8d5aca: browser/accessibility tests and visual verification.
- 8d8d511: private deployment/operator checks, threat model, runtime metadata,
  inert unit validator and CI.
- A final documentation-only commit records these exact delivery hashes.

Commits use the configured identity without co-author trailers. Push skipped for
all units: no remote/upstream exists. No remote was invented. Phase 2 history and
unrelated work are preserved. The working tree is checked at final delivery.

## Implemented scope

Fastify/TypeScript app with exact dependency lock; independent random user-specific
admin token/scrypt verifier; absolute 15-minute user/device-bound session; secure
cookies, login/session CSRF, canonical Host, CSP and security headers, bounded
requests/logins/sessions/CLI calls, generic errors and no request-body logging.

Direct private HTTPS only. The actual socket peer is resolved by fixed local CLI
calls. Reviewed daemon 1.102.3, online kernel-TUN/self expiry and assigned addresses
are checked; peer machine authorization, user ID, exact node address, stable ID
and expiry must validate. Tagged/shared/unsigned-only/WireGuard-only devices and
forwarded headers are denied. Versioned source hashes in docs/ADMIN-IDENTITY.md.
An independent app token remains mandatory; there is no production test bypass.

Observed host CPU/RAM/root disk/uptime/aggregate network with bounded sample history;
explicit unavailable readings, timestamps and staleness. Optional curated text
exports use pinned directory descriptors, no-follow traversal, single-link regular
files, size/type/listing bounds and audited attachment download. No content redaction
is promised for exports. Audit serializes append-and-sync fixed-schema events,
checks its 16 MiB limit before writing and shows 200 events since startup. Audit
failure blocks login/file delivery; logout still revokes its session.

Responsive dark/light UI includes overview, alerts, services, metrics, Tailscale,
backups, files, database, updates, audit filtering and configuration. Unsupported
controls are explicitly disabled; execution routes always return 501.

An inert systemd unit, runtime checksum pin, env example, private deployment and
certificate/credential/audit/rollback guide, scoped threat model, offline unit
parser and pinned CI checks complete the reviewed slice. No approval marker is
created and no automatic host/deployment apply command exists.

## Actual validation

Final clean `npm ci --ignore-scripts` succeeded. `npm test`: both production
TypeScript builds and all 13 backend tests PASS. Covers auth/CSRF/session/headers,
identity/expiry/version, provisioning/refusal, audit capacity/reopen/failure, file
routes/paths and synthetic TLS certificate/socket-peer/Host behavior.

`npm run test:browser`: all 5 Playwright tests PASS on local Chromium. Covers login,
independent credential, secure cookie, reload/logout; all ten views and theme
persistence; keyboard and mobile overflow; file download/audit filtering; network
failure, staleness and session expiry. Axe reported zero WCAG 2 A/AA and 2.1 AA
violations across 20 theme/view combinations plus mobile login. No page errors.
Agent-browser 0.36.0 manually verified login/navigation and no page errors; desktop
and mobile screenshots were visually inspected. Screenshots are ignored test
artifacts, not appliance health evidence or a screen-reader certification.

`npm audit --omit=dev`: zero reported vulnerabilities. Both repo validators PASS.
`python3 scripts/validate_admin_unit.py`: PASS with disposable executable/dependency
stubs, no service execution. All 18 Python tests PASS including offline Compose
config and inert admin deployment boundary. Staged whitespace/secret-marker review
performed before commit. Hosted GitHub CI has not run because there is no remote.

Initial TLS test SNI/Host mismatch was fixed in the fixture. A direct local unit
check found no template Node binary on the development host; disposable-root
parsing passed after supplying explicit parser stubs. Neither is a target pass.
Only temporary loopback TLS fixtures and synthetic secrets were used, then cleaned
up. No real tailnet status/identity queried; only installed CLI version/help read.

## Deferred features and target gates

NOT implemented: SQL execution/database metadata connection, service/raw-log
collectors, privileged helper, file writes, backup/restore/restart/update execution,
and re-authentication for those unavailable operations. Disabled UI is not delivery
of these features. A browser terminal is intentionally excluded; use SSH keys.

Before deployment: actual Debian 13/T2 qualification, authenticated runtime package
provenance, certificate trust/renewal, clock, daemon/user permissions, service
sandbox/cgroup/BPF and Docker/firewall/tailnet policy verification, approved/denied
user/device tests, restart/rollback and recovery. The existing policy's 443 grant
does not cover admin 8443; operator must reconcile and validate, not expose it.

Risks: daemon control knowledge may lag revocation; local root/daemon are trusted;
app compromise can read all OS-allowed curated exports; live files can change while
read; audit is not tamper-resistant and has no automatic external archival; resource
caps and Node/syscall compatibility require target testing. Missing temperature,
service/request rates/backup evidence are never claimed healthy. No production,
SQL/RLS, destructive-action or Phase 5 encrypted-recovery gate is waived.

See docs/ADMIN.md, security/ADMIN-THREAT-MODEL.md and NEXT-PHASE.md for exact remaining
work. Earlier 76–97% stop instructions are historical; resumption was explicitly
authorized after reset and verified at 7% five-hour usage. This task now ends at
completed read-only verification scope, not because a test failed.
