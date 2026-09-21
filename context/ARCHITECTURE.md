# Architecture handoff

Phase 12 connects the private admin to optional bounded, fresh collector evidence.
The fixed root-owned status file is a read-only boundary, not a privileged helper.
The project connection assistant generates a command locally and preserves every
Tailscale/user/device/token/session check. Original branding and local fonts load
through explicit static routes after Tailscale identity checks.

Phase 11 adds optional public ingress in a separate macserver-ingress Compose
project. Cloudflared has only edge-network access; the dependency-free Node gateway
joins edge plus macserver-data_default and routes directly to Auth/PostgREST.
There are no host ports or routes to Envoy/Studio. Explicit table/method scopes,
Auth-verified user tokens, api schema, RLS, bounded requests and redacted events
form the public boundary. Public keys select scope; they are not app identity.
The project generator prepares a new private config/client/schema bundle only.
Activation still needs target evidence; see docs/PUBLIC-APPS.md for supported scope.

Phase 10 adds a separately built public documentation site. Scrollable code samples
are keyboard focusable across browsers. Its only deployed artifact is build/site;
it contains no appliance connection, credentials, or live
status. Original branding, local fonts and GSAP are shared visual foundations.
The short README links to the full operator guide and preserves qualification gates.

Phase 8 adds a private deployment boundary: clean synchronized GitHub source becomes a
root-owned versioned release only after commit-specific target evidence. An approval-
gated systemd unit starts digest-pinned internal-only Compose profiles and waits for
health. Updates require current backup/restore evidence, switch the release symlink
atomically, and return to the previous release on activation failure. Data migrations
remain separately reviewed and are never inferred from a code rollback.

MacServer is an independent repository and product boundary. Local specifications,
agent instructions, source, deployment candidates, evidence, and operations docs must
not depend on or modify another project workspace.

The root README is the concise operational entry point for the architecture. Its
ordered setup path preserves the same trust boundaries and NO-GO state: Tailscale-only
administration, internal-only data services, encrypted live/backup storage, optional
private interfaces, and no public management ingress. It does not add an installer,
deployment approval, public tunnel, or runtime evidence.

Phase 7 closes the repository plan with a consolidated no-secret verification manifest
and requirement traceability. All source-only checks may pass while the manifest remains
NO-GO with target/public evidence false. This prevents CI, a clean tree or synthetic
browser success from being mistaken for installed-host, RLS, ingress or recovery proof.

Phase 6 audited the inert repository and preserved a NO-GO runtime verdict. An offline
posture check now asserts that Compose remains internal/no-published-port/digest-pinned,
CI actions and permissions remain narrow, tracked secret-like paths are absent, private
admin and loopback dashboard boundaries remain intact, and RLS/backup refusal fixtures
do not drift. Candidate host and tailnet rules are aligned to private admin TCP 8443
plus SSH 22; 443, PostgreSQL, dashboard and API ports have explicit tailnet deny cases.
These are source assertions, not effective-firewall or additive-policy evidence.

Phase 5 adds an inert encrypted-backup candidate. A fixed-command root process makes
PostgreSQL logical exports into private internal-SSD staging, then restic snapshots
those exports with `/srv/macserver/storage`, `/etc/macserver`, and deployed infra to a
removable repository. Writes require an exact mountpoint, filesystem UUID, private
sentinel, supported filesystem/mount flags, unique removable 200–300 GiB device and
free-space gate. The independently escrowed restic password arrives through a systemd
credential. An absent/substituted drive fails visibly without stopping live services.

Daily snapshot and weekly subset-check units remain inert behind an untracked approval
marker. Retention pruning and restore drills require the freshly observed UUID. Drills
restore to a new path, verify dump hashes and load a pinned PostgreSQL container with no
network and tmpfs data; no live restore or volume deletion exists. Storage now uses an
explicit encrypted-SSD bind path so object files are in the backup boundary. A future
ingress maintenance/drain mechanism is still required for cross-database/object
consistency, and a second off-host repository plus real recovery test remain open.

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
Phase 4 is repository-complete but undeployed. Scope and conditional future handoff are
in its status and NEXT-PHASE.md.

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
Internal SSD -> candidate encrypted backup -> removable 250 GB flash drive
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
off-device destination; recovery-time requirements. No real
identity, domain, device UUID or secret belongs in tracked configuration.

Phase mapping is in PHASE-PLAN.md. Phase 2 handles data services/migration tooling;
Phase 3 admin; Phase 4 dashboard; Phase 5 backup/recovery (repository-complete); Phase 6 audit; Phase 7
readiness. Production deployment is gated on working recovery and exposure tests,
regardless of which phase produces the code.

## Coordinator boundary — 2026-09-07T14:42:31.404314+00:00

Verified read-only Phase 3 delivery is at ca8e0f2; the implementing task is idle.
At 78% five-hour usage, Phase 4 remains unstarted. This scheduling checkpoint
changes no architecture, deployment state, deferred capability or recovery gate.

## Phase 9 installation corrections

Canonical GitHub origin and Python CLI invocation now match the actual checkout.
Full source verification is documented; runtime and public exposure remain gated.
The requested next delivery adds a branded installation site and public-app setup.
