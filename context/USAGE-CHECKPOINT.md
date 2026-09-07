# Usage and delivery checkpoint — 2026-09-07

Current resumed run: tool-confirmed usage was 7% five-hour / 39% weekly at intake,
and 68% five-hour / 49% weekly at final verification. No reset credit was consumed
by this task. The coordinator's original 76–97% stop requests preceded an explicitly
authorized usage reset/resumption; those stop-only instructions are superseded.
Do not start Phase 4 or duplicate this task. Check actual usage before new work.

Implementing task: 01a07b2f-e64a-76f0-a6d4-3fed4d4a01ee. Initial main was clean at
0281a37; no remote/upstream exists. Completed code units:
- 0281a37: private read-only foundation (previous usage checkpoint).
- 684758a: versioned identity/expiry and failure-boundary hardening.
- d8d5aca: browser/accessibility flow verification.
- 8d8d511: private deployment/operator checks, threat model, runtime pin and CI.

Code delivery HEAD is 8d8d511, verified clean after commit. A final documentation-only
commit records these hashes; use git log -1 for that handoff commit. No upstream
exists; push is skipped for the final unit and handoff as for every earlier unit.

The resumed task's requested verification/documentation scope is complete. Full
admin capability is not claimed: SQL/metadata connections, service/raw-log
collectors, file writes and privileged backup/restore/update/restart are unavailable.
No production or appliance service was started. Only synthetic loopback TLS/browser
fixtures ran; all fixtures and the manual browser session were closed. Tests created
only temporary synthetic credentials/certificates and removed them.

Final clean-install validation: npm ci --ignore-scripts PASS; production server and
browser TypeScript builds PASS; 13 backend tests PASS; 5 Playwright tests PASS;
21 axe scans zero listed WCAG A/AA violations; npm audit --omit=dev zero reported
vulnerabilities; 18 Python tests PASS; both repository validators PASS; systemd
parser with disposable dependency/executable stubs PASS. Hosted CI and actual
Debian systemd/Tailscale/TLS/firewall behavior are not claimed tested.

Files include apps/admin source, locked package/build config, unit/TLS/browser
fixtures, README/env; infra/admin unit/runtime metadata; offline unit validator
and deployment test; pinned CI; docs/ADMIN.md and ADMIN-IDENTITY.md; scoped threat
model; and four current handoffs. Build/node_modules/browser outputs are ignored.
No generated secrets or real identity data is tracked. Diff and secret-marker
checks precede commit. All commits use configured identity without co-author
trailers; push skipped because there is no remote/upstream, and none was invented.

Restart instructions: read PHASE-3-STATUS.md and NEXT-PHASE.md, inspect clean Git
and actual usage, and continue only newly authorized scope. The present verification
work needs no retry. Any future privileged/SQL/collector work must honor its explicit
authorization and Phase 5 recovery gates. Conditional Phase 4 instructions are in
NEXT-PHASE.md; no Phase 4 implementation occurs in this task. Public administration
remains forbidden and all Phase 2 SQL/RLS/runtime/network/hardware gates remain.
