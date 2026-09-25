# Phase 13 — usable initial accounts and database verification

Added private Auth user provisioning with a hidden password prompt. It sends only
the service credential and new user inputs through stdin into an ephemeral,
read-only, capability-dropped, memory-bounded Node container on the internal data
network. The runtime is the previously reviewed image; --pull=never forbids new
downloads. The host environment must be root-owned mode 0600. No public Auth admin
route was added, no existing account is updated, and no credentials are printed.

The public guide includes exact first-schema import and user-creation commands.
The source tests verify validation, credential placement and redacted failure.
A new isolated PostgreSQL CI job executes the real generated schema and verifies
positive own-row access, cross-user read/update/delete/insertion/reassignment
denial, and anonymous denial. It is fixture evidence, not production app validation.

No user was created and no SQL was applied on an appliance. CI runtime is isolated
with --network none and uses only synthetic rows; the official image is pinned.

Hosted project-sql CI passed against the generated schema and real PostgreSQL.
The current architecture/handoff now replace stale phase-era next-step language;
the README and installation/deployment guides link the delivered public workflow.

## Source review hardening — 2026-09-25

A source review found and fixed three defects, each with regression tests:
- Ingress: `Prefer: resolution=merge-duplicates` and `on_conflict` let an insert-only
  table scope perform updates (RLS still applied). Both now need the table's PATCH scope.
- Appliance: qualification checks accepted truthy non-booleans such as `"false"`, and
  timestamps without an offset. Only JSON `true` and offset-aware timestamps now pass.
  Updates pull images before replacing the unit. Rollback checks that the target's
  release metadata matches its commit, and it installs that release's unit.
- Collector: a JSON array in a status input or from Tailscale crashed the collector.
  It now reports that input as unavailable.

No appliance was contacted. Tests ran locally for the ingress (Node) and appliance
(Python) code. Collector tests need Linux-only `os` flags, so hosted CI covers them.
