# Architecture handoff

Phase 3 checkpoint (2026-09-07): apps/admin contains a tested read-only foundation,
not a fully verified or deployed admin app. See PHASE-3-STATUS.md. Its entry path
is direct private HTTPS -> local daemon whois of actual socket peer -> independent
per-user admin token -> user/device-bound session. It rejects forwarded headers;
no proxy/Serve/Funnel is supported. Loopback binds are allowed but real loopback
peers fail whois; test identity injection exists only in the factory/tests, not
as a production environment switch. The process has no Docker/DB client or
privileged helper. Host telemetry is read locally; optional curated files use
pinned Linux directory descriptors. SQL, file writes and disruptive operations
unconditionally deny execution. Browser/accessibility, private deployment and
live TLS/identity/security checks remain outstanding. Do not start Phase 4.


Phase 1 offline foundation and Phase 2 offline data-service configuration exist;
the appliance is not installed. User authorized local commits without a remote.
See docs/adr/0001-staged-foundation.md and docs/DATA-SERVICES.md.

Phase 2 pins the official Supabase v0.8.0 distribution and image digests. Core is
PostgreSQL 17, Auth, PostgREST and Envoy; Functions, Storage, Realtime and Studio/meta
are selected explicitly. No ports are published; one internal-only Docker network
holds the trusted services. External egress, private ingress and public application
routes remain gated. Functions receive no database/service-role/signing secret.
Only api is selected for REST exposure. A separate macserver_owner NOLOGIN role and
api/app_private schema migration are staged, not applied. Owner defaults deny
implicit client object access; app grants/RLS remain explicit. Read-only catalog
capture, offline structural audit, synthetic RLS probe and export manifests support
migration rehearsals. They do not replace real per-app authorization tests.

```text
Future application clients -> outbound tunnel -> HTTPS route allowlist
  -> scoped application authorization / limits -> data services + RLS
Future administrators -> Tailscale -> OpenSSH keys / authorized private admin app
Internal SSD -> future verified encrypted backup -> removable 250 GB flash drive
Local console -> future read-only operations dashboard
```

Offline preflight, staging, secret generation, candidate host settings, pinned
service configuration, tests and documentation exist. No arrows above are deployed.
The stage command reads infra, writes a private bundle and hashes its contents;
it never applies configuration. Preflight checks Debian 13 amd64, memory and space
on the selected state filesystem; hardware qualification remains manual.

Fixed decisions: Debian 13 minimal; encrypted internal live storage; no plaintext
disk swap; physical unlock after reboot initially; Tailscale-only management;
ordinary key-only OpenSSH; no router forwarding; no public management services.
Service and package pins are recorded; target validation must precede deployment. The native
nftables input candidate is not Docker forwarding protection and must pass a
backend-specific integration review. Journald, time sync, lid behavior, updates
and SSH templates remain unapplied.

Open: T2 hardware support; Docker backend integration; application
inventory and isolation model; domain/tunnel provider; route and credential design;
backup capacity and off-device destination; recovery-time requirements. No real
identity, domain, device UUID or secret belongs in tracked configuration.

Phase mapping is in PHASE-PLAN.md. Phase 2 handles data services/migration tooling;
Phase 3 admin; Phase 4 dashboard; Phase 5 backup/recovery; Phase 6 audit; Phase 7
readiness. Production deployment is gated on working recovery and exposure tests,
regardless of which phase produces the code.

Resumed Phase 3 identity boundary: only reviewed Tailscale daemon 1.102.3 is
accepted; online kernel-TUN self state and expiry are checked before peer whois.
Explicit machine authorization, matching user/node address, no tags/shares and
peer expiry validation precede app token/session checks. Synthetic HTTPS and
failure-path tests pass (13 total); actual tailnet identity remains a target gate.
