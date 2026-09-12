# Architecture handoff

Phase 4 adds a locally verified, loopback-only fullscreen status display. It has no
credentials or mutations. A separate fixed-command collector writes a bounded atomic
snapshot; the dashboard combines that with direct host observations and marks missing
or older-than-60-second evidence unavailable/stale. The collector's privileged Docker
socket access is never exposed as an HTTP helper. Candidate services remain inert behind
an untracked operator approval marker. A graphical user unit restarts the kiosk, but
target session/graphics/browser recovery is not qualified. The observed Debian Chromium
package is below the recorded security floor, so kiosk approval remains blocked.

Phase 3 read-only slice is locally verified and delivered; not deployed or the full
admin prompt. See PHASE-3-STATUS.md for commits/checks and deferred capabilities.
Direct private HTTPS -> local Tailscale daemon status/whois of actual socket peer
-> independent per-user token -> 15-minute user/device session. Reviewed daemon
1.102.3, self/peer expiry, explicit machine authorization and exact addresses are
required; tagged/shared/forwarded identity is denied. No proxy/Serve/Funnel.

App has no Docker/DB client, privileged helper or browser terminal. It reads host
observations and optional curated exports through pinned Linux directory descriptors,
and writes bounded private audit events. SQL, file writes and disruptive routes
unconditionally refuse execution. Service/backup/sensor data without collectors
remain unavailable. Synthetic TLS/backend/browser/a11y verification passes; the
production entry point has no environment-based identity bypass.

The inert dedicated-user systemd candidate uses loaded credentials, read-only
filesystem protection, private-network/resource limits and an absent approval
marker. Node runtime metadata, operator/rollback guide, threat model and pinned CI
are included. Parser stubs verify syntax only; actual Debian sandbox, TLS/renewal,
Tailscale permissions/policy/firewall and all recovery gates remain outstanding.
Phase 4 was not started. Scope and conditional future handoff are in NEXT-PHASE.md.

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
Local console -> loopback-only read-only operations dashboard
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
Phase 3 admin; Phase 4 dashboard (repository-complete); Phase 5 backup/recovery; Phase 6 audit; Phase 7
readiness. Production deployment is gated on working recovery and exposure tests,
regardless of which phase produces the code.

## Coordinator boundary — 2026-09-07T14:42:31.404314+00:00

Verified read-only Phase 3 delivery is at ca8e0f2; the implementing task is idle.
At 78% five-hour usage, Phase 4 remains unstarted. This scheduling checkpoint
changes no architecture, deployment state, deferred capability or recovery gate.
