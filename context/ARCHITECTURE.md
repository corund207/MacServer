# Architecture handoff

Phase 1 offline foundation is implemented; the appliance is not installed.
See docs/adr/0001-staged-foundation.md for the accepted decision and tradeoffs.

```text
Future application clients -> outbound tunnel -> HTTPS route allowlist
  -> scoped application authorization / limits -> data services + RLS
Future administrators -> Tailscale -> OpenSSH keys / authorized private admin app
Internal SSD -> future verified encrypted backup -> removable 250 GB flash drive
Local console -> future read-only operations dashboard
```

Only the offline preflight, bundle staging, backup-password generation, candidate
host settings, tests and documentation exist today. No arrows above are deployed.
The stage command reads infra, writes a private bundle and hashes its contents;
it never applies configuration. Preflight checks Debian 13 amd64, memory and space
on the selected state filesystem; hardware qualification remains manual.

Fixed decisions: Debian 13 minimal; encrypted internal live storage; no plaintext
disk swap; physical unlock after reboot initially; Tailscale-only management;
ordinary key-only OpenSSH; no router forwarding; no public management services.
Future service pins must be exact before deployment is enabled. The native
nftables input candidate is not Docker forwarding protection and must pass a
backend-specific integration review. Journald, time sync, lid behavior, updates
and SSH templates remain unapplied.

Open: T2 hardware support; exact Docker/backend and service pins; application
inventory and isolation model; domain/tunnel provider; route and credential design;
backup capacity and off-device destination; recovery-time requirements. No real
identity, domain, device UUID or secret belongs in tracked configuration.

Phase mapping is in PHASE-PLAN.md. Phase 2 handles data services/migration tooling;
Phase 3 admin; Phase 4 dashboard; Phase 5 backup/recovery; Phase 6 audit; Phase 7
readiness. Production deployment is gated on working recovery and exposure tests,
regardless of which phase produces the code.
