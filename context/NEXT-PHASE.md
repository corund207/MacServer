# Continue Phase 3 — do not start Phase 4

The implementing task reached a terminal usage checkpoint at the user's request.
Read PHASE-3-STATUS.md and USAGE-CHECKPOINT.md; verify git history/status and preserve
all work. The read-only foundation compiles and passes 8 admin and 17 existing
Python tests. It is not the complete Phase 3 deliverable or a deployed appliance.

When account usage permits, resume unfinished Phase 3 in the existing task:

> Read all applicable AGENTS.md, prompts, README, context and runbooks. Inspect
> status/history/remotes. Review apps/admin and the recorded limitations. Complete
> browser/login/theme/mobile/stale-data and axe accessibility checks; synthetic
> TLS/socket integration; official versioned Tailscale whois fields/expiry review;
> provisioning, file-route, audit capacity/failure tests; private systemd example,
> operator/rollback/threat-model documentation and CI integration. Read the browser
> skill before browser tooling. Keep every unavailable operation explicitly gated
> and do not create an authentication bypass or fake metrics. Review the complete
> diff, test and commit each completed unit with the configured identity and no
> co-author trailers. Push only if an upstream exists; otherwise report skipped.
> Update Phase 3 handoffs. Do not start services, connect to production, execute SQL,
> change the host, or perform destructive work. Preserve Phase 2 runtime/SQL/RLS/
> network/hardware gates and Phase 5 recovery prerequisites. Do not start Phase 4.

Only after Phase 3 is verified and delivered should a separate Phase 4 task build
its fullscreen local operations dashboard. It must isolate read-only telemetry
from private admin sessions, show stale/unavailable state honestly, add kiosk
recovery tests, and preserve all deployment/recovery gates. This is a conditional
future handoff, not authorization to skip remaining Phase 3 work.
