# Phase 6 — repository security audit and hardening

2026-09-11. The authorized non-destructive repository audit is complete. No appliance,
endpoint, LAN host, Docker daemon, tailnet, database, backup media or secret was
contacted. The assessment therefore cannot certify runtime or public exposure.

## Delivered scope

- Prioritized report with scope/method, architecture, attack-surface inventory,
  CVSS-like rationale, evidence, exploit preconditions, remediation, safe verification,
  residual risk, retest checklist, 30-day plan and an explicit NO-GO verdict.
- Offline posture checker for Compose exposure/digests/resources, tracked secret-like
  paths, CI action/permission pins, host SSH/firewall, Tailscale policy negatives,
  admin/dashboard isolation, database/RLS fixtures and backup refusal gates.
- Negative tests prove the checker detects a published Compose port and an unpinned
  image rather than only passing the current repository.
- Corrected the concrete admin port mismatch: candidate host and tailnet policies now
  permit only private SSH 22 and admin HTTPS 8443, with deny tests for 443, PostgreSQL,
  dashboard and API ports. The admin alert now accurately describes Phase 5 state.

## Verification evidence

`scripts/security_posture.py`, deterministic Compose rendering and all unit parsers
pass. Python: 28 tests. Admin: 13 Node and 5 browser/accessibility tests. Dashboard:
3 Node and 4 browser tests. Both production dependency audits report zero findings.
Static/synthetic evidence cannot validate a target firewall, Tailscale grant, RLS
policy or recovery.

## Verdict and remaining blockers

NO-GO for production data and public exposure. Blocking findings are: absent public
route/application-credential/maintenance layer; no real application catalog/RLS/API
negative evidence; unqualified Debian/T2/Docker/firewall/Tailscale isolation; and no
physical/off-host restore proof. High findings include the kiosk browser below its
security floor, synthetic-only private admin identity/TLS, unqualified upstream
container privileges and unrehearsed shared legacy Supabase key lifecycle.

Phase 7 may complete repository-level end-to-end validation and a release manifest,
but cannot turn missing physical/runtime evidence into a GO. Target testing requires
the user to identify and authorize the actual appliance/endpoints separately.
