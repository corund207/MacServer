# Handoff after Phase 5 repository delivery

The encrypted backup, retention, repository checking and isolated restore candidates
are implemented with synthetic tests. Read PHASE-5-STATUS and BACKUP-RESTORE before
changing them. No drive, repository, target database, container, service or timer was
touched. Production recovery remains unproved and the appliance remains NO-GO.

## Phase 6 task boundary

Perform the authorized, non-destructive repository security audit and safe hardening.
Read every instruction and prior status first. Review trust boundaries, secret flow,
systemd sandboxes, fixed-command subprocesses, file/path races, dependencies and supply
chain, Compose/network exposure, Envoy routes, Supabase/RLS migration checks, Tailscale
identity, dashboard/admin browser boundaries, backup loss/tampering and update rollback.

Use only offline fixtures and parsing unless the user explicitly identifies and
authorizes the target appliance/endpoints. Do not scan the LAN, start services, query a
real tailnet, open a Docker socket, read secrets, stress endpoints or mutate data.
Findings need severity, evidence, exploit preconditions, remediation and a safe
verification method. Fix repository-local issues that can be safely verified; preserve
deployment gates instead of papering them over. Add threat-model detail, automated
negative checks and PHASE-6-STATUS, then update ARCHITECTURE and NEXT-PHASE.

Review/test/commit the delivery unit with the configured Git identity and no co-author
trailers; push only if an upstream exists. Preserve unresolved Debian/T2 hardware,
Docker/firewall/Tailscale runtime, private admin TLS/identity, real production migration
and RLS, public domain/tunnel/credential/maintenance design, kiosk browser security
floor, physical backup/off-host recovery and target reboot/network/thermal/power-loss
gates. Public administration remains forbidden.
