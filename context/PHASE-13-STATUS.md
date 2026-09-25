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

## Per-client Auth budgets — 2026-09-25

The shared 60/min public Auth budget let any holder of the public app key block
sign-in for everyone. Auth routes now take a per-client budget of 20/min, keyed on
Cloudflare's `CF-Connecting-IP` (IPv6 grouped by /64; a missing or malformed value
uses one shared "unattributed" bucket), under a total ceiling of 300/min. Tracking
covers at most 4,096 clients, and new clients are refused while all are active. The
address is used only for throttling. It never authorizes a request and is never
logged or forwarded. Gateway tests cover key parsing, cross-client isolation, the
total ceiling and audit redaction. Residual risk: a flood from many addresses; use
Cloudflare edge rate limiting.

## Per-client gateway limits — 2026-09-25

The remaining shared gateway limits are now also counted per client, using the same
`CF-Connecting-IP` key (throttling only). All requests: 240/min per client before the
3,000/min total. In flight: 8 per client, 32 in total. Each app: optional
`clientRequestsPerMinute` (default min(60, `requestsPerMinute`)) before the app total.
New bundles use 600 total and 60 per client. Tests cover each limit and fail against
the previous gateway. docs/PUBLIC-APPS.md documents the limits and the shared-address
trade-off.

## Admin redesign and terminal console display — 2026-09-25

The private admin console was restyled within BRAND.md: grouped navigation, sticky
top bar, gauge meters, colour-coded service states with counts, a split sign-in panel,
and refined dark and light themes. Every ID, label, route and security behaviour is
unchanged. The admin UI tests, including axe checks of all 11 views in both themes,
pass locally. The TLS browser suite and file-mode unit tests need Linux, so CI runs them.

The Chromium kiosk and loopback web dashboard were removed. The always-on screen is now
`apps/console/macserver_top.py`, a stdlib curses display on tty1 run by
`macserver-console.service`: AF_UNIX only, IPAddressDeny=any, no capabilities, ignores
the keyboard, sanitises status text before it reaches the tty, and has VT-font glyph
fallbacks. This resolves the security audit's High kiosk-browser finding by removal.
tests/test_console.py covers sampling, evidence validation, verdicts, rendering at
80x24 to 213x67, and a Linux pty smoke test. Target console font, tty1 takeover and
reboot behaviour still need appliance qualification (docs/DASHBOARD.md).
