# Phase 6 authorized security review

Date: 2026-09-11. Baseline: Git commit `3b2e0a2`. Scope is the public repository and
its inert candidates only. No appliance address or public HTTPS endpoint was supplied,
so this assessment did not contact a host, Docker daemon, tailnet, database, backup
drive or LAN device. It used source review, deterministic configuration rendering,
unit/browser tests, dependency audit output and offline negative assertions.

## Executive summary

The checked-in system is default-deny in its current inert state: Supabase publishes
no ports, its Docker network is internal, the console display has no network access, the admin app
requires direct Tailscale peer verification plus an independent credential, privileged
admin routes return 501, images/actions/runtimes are pinned, and no secret-like file is
tracked. The audit corrected a private-admin port mismatch between the 8443 service and
candidate host/tailnet rules.

Recommendation: **NO-GO for production data or any public exposure**. This is mostly a
qualification verdict, not evidence of an exploitable running service. There is no
public ingress authorization/rate-limit route layer, no real application/RLS evidence,
no target Docker/firewall/Tailscale proof, and no physical restore. Deploying around those gates would create
high-impact paths to data or administration.

## Architecture and attack surface

```text
Public client --X-- [no approved tunnel / route allowlist / app credential layer]
                              |
                      internal Envoy -> Auth / REST -> PostgreSQL + RLS
                                     -> Storage -> /srv/macserver/storage
                                     -> optional Functions / Realtime

Admin tailnet device -> Tailscale -> host input 8443 -> private HTTPS admin
                                    host input 22   -> key-only OpenSSH
Mac screen tty1 <- offline read-only console display <- bounded status file
Root-only collector -> Docker/Tailscale fixed reads ---------^
Root-only backup -> logical DB export + Storage/config -> encrypted removable restic
```

`--X--` is intentional: no public listener or tunnel exists. Origin, Referer,
User-Agent and client IP are not treated as application identity. The future public
layer must add its own scoped credential and server-side authorization before Envoy.

| Surface | Current repository boundary | Runtime evidence |
| --- | --- | --- |
| OpenSSH | keys only; no root/password/forwarding/tunnel | candidate parsed; effective target unknown |
| Host input | loopback plus Tailscale TCP 22/8443 | kernel/Docker integration untested |
| Supabase | digest-pinned, no published ports, internal network | images never started here |
| Admin | private TLS, actual socket-peer whois, per-user token, CSRF, limits | synthetic TLS/browser only |
| Dashboard | loopback reads only, no credentials/controls | local browser fixtures only |
| Backup | exact removable identity, encryption, confirmed prune/drill | fake runner; no physical media |
| Public HTTPS | absent | not authorized or implemented |
| GitHub/CI | public source, read-only job token, commit-pinned actions | hosted run must be inspected |

## Prioritized findings

Scores below are CVSS-like risk estimates for the stated misuse scenario; they are not
results from probing a live endpoint.

### Blocking — public ingress and application credential layer absent (9.1)

Evidence: Compose contains no published ports and no tunnel configuration exists.
This safely prevents current exposure, but does not deliver the required public app
path. Exploit precondition: an operator exposes Envoy or a service directly to obtain
functionality. That could expose upstream management routes or a broad bearer-key
surface without per-app scope, revocation, abuse limits or audited authorization.

Remediation: implement a separate outbound-only HTTPS ingress with an exact method/path
allowlist, authenticated origin, scoped per-app credentials, server-side ownership
checks, body/rate/concurrency/time limits, safe errors, CORS as browser policy only,
and tests proving Studio, admin, metrics, Docker, SSH and database ports unreachable.
Safe verification: use only explicitly authorized endpoint names and low-rate GET/HEAD
negative requests; stop on instability. Residual risk: public clients remain hostile
even after edge checks, so RLS and server-side authorization stay authoritative.

### Blocking — production schema, grants and RLS are not evidenced (9.1)

Evidence: the repository contains a safe boundary migration, catalog checker and a
synthetic two-user RLS fixture, but no application catalog/export. Exploit precondition:
production data is migrated before every exposed table/view/function and Storage policy
passes review. A missing `WITH CHECK`, owner test, `security_invoker`, fixed function
search path or grant can permit cross-user access or privilege escalation.

Remediation: capture the approved managed-project catalog, resolve every finding, and
run two-user plus anonymous/service-role negative tests through the actual API for each
operation. Safe verification commands and rollback boundaries are in `docs/MIGRATION.md`.
Never paste catalog secrets or data into Git. Residual risk: structural checks cannot
prove business ownership rules; application-specific adversarial cases are required.

### Blocking — host and container isolation are not target-qualified (8.9)

Evidence: templates and parser tests exist, but there is no Debian 13/T2 hardware,
encrypted-disk, effective SSH, nftables/Docker forwarding, listener, user/group,
systemd sandbox, AppArmor, Tailscale or reboot evidence. Exploit precondition: inert
files are installed as though parser success proved effective isolation. A backend or
additive-policy mismatch could expose a service to LAN/tailnet users.

Remediation: rehearse in a target-equivalent VM, then validate only the designated
appliance with console recovery and an unauthorized test identity/client. Safe checks:
`sshd -T`, `systemd-analyze security`, `nft list ruleset`, `ss -lntup`, effective
tailnet policy tests and Docker network inspection; redact addresses and do not scan
the broader LAN. Residual risk: the Docker daemon is root-equivalent and the collector/
backup trust boundary includes root.

### Blocking — disaster recovery is not physically proved (8.6)

Evidence: the encrypted workflow and synthetic drill pass, but no physical drive,
real dump, off-host copy, power interruption or recovery-time measurement was used.
Online Storage files can change after the database export. Exploit precondition: SSD
loss/corruption or an update during an unproved recovery window.

Remediation: qualify rotating media, independent password escrow and an off-host
repository; drain writes in verified maintenance mode; restore real representative data
into new isolated volumes and run RLS/application parity tests. Safe procedure is in
`docs/BACKUP-RESTORE.md`. Residual risk: one local drive shares theft/fire/operator-error
risk and restic integrity does not make a cross-resource online snapshot atomic.

### Resolved 2026-09-25 — kiosk browser was below the recorded security floor (8.1)

Status: resolved by removal. The Chromium kiosk and loopback web dashboard were replaced by
an offline terminal display on tty1 (`apps/console`, `macserver-console.service`): no browser,
no listener, no network address family and no keyboard input. The original finding follows.

Evidence: Phase 4 observed Debian Chromium 152.0.7977.82 while the recorded minimum was
153.0.8010.36 due to known fixed-upstream issues. Exploit precondition: enabling the
kiosk with that build and rendering attacker-influenced browser content. The dashboard
is loopback and has strict CSP, reducing but not eliminating browser-engine risk.

Remediation: wait for an authenticated Debian security update at or above the floor,
then rerun browser, policy, sandbox and offline/crash tests. Do not bypass this with an
unreviewed binary source. Safe verification: compare `chromium --version` to the lock;
do not start kiosk mode during the check. Residual risk: renderer vulnerabilities remain
possible and browser updates need continuing review.

### High — private admin identity/TLS is synthetic only (8.0)

Evidence: direct socket-peer whois, exact Tailscale version/expiry/device checks,
independent tokens, TLS, CSRF and session limits have tests; no real certificate,
revocation, daemon permission or allowed/denied device was exercised. Exploit
precondition: incorrect target certificate trust, daemon semantics, additive grant or
service bind. Remediation and safe allow/deny tests are in `docs/ADMIN.md`. Residual
risk: local root/tailscaled compromise defeats this layer; audit storage is local.

### High — upstream container privilege behavior is unqualified (7.8)

Evidence: services have `no-new-privileges`, process/memory bounds, digest pins and no
Docker socket, but retain upstream users and capability defaults because blanket drops
can break initialization. Exploit precondition: compromise of a service with a usable
kernel/container escape or writable mount. Remediation: record effective UID,
capabilities, seccomp/AppArmor and mounts after startup; drop each proven-unneeded
capability per image and retest initialization/upgrades. Residual risk: digest pinning
fixes identity, not vulnerability status.

### High — shared legacy Supabase key lifecycle is unrehearsed (7.7)

Evidence: one self-hosted project uses legacy HS256 compatibility; the service role is
powerful and application clients are not yet inventoried. Functions deliberately lack
signing/database/service-role secrets. Exploit precondition: leaked service-role/JWT
secret or reuse across trust domains. Remediation: keep service role server-only,
separate unrelated trust domains, inventory clients, rehearse coordinated rotation and
prefer reviewed asymmetric/opaque-key migration when supported. Residual risk: rotation
is disruptive and self-hosting lacks managed-platform recovery controls.

### Medium — admin policy port mismatch (6.4), remediated

Evidence: the admin service used 8443 while Phase 1 firewall/tailnet examples allowed
443. Phase 6 changed both candidates to only 22/8443 and added deny cases for 443,
5432, 7460 and 8000. Exploit precondition before correction: an operator either widened
rules ad hoc or left admin unreachable. Safe verification:
`python3 scripts/security_posture.py`, then validate the complete additive tailnet policy and
effective target firewall. Rollback: revert the Phase 6 commit; do not reintroduce the
old mismatch on a deployed target. Residual risk: examples do not control existing
broad grants.

### Medium — local admin audit has finite, root-mutable history (5.3)

Evidence: append-and-sync events are bounded to 16 MiB and audit failure blocks sensitive
app actions, but local root can modify the file and capacity exhaustion causes denial
of administration. Exploit precondition: local-root compromise or sustained authorized
activity. Remediation: ship a redacted copy through a reviewed authenticated local or
off-host log path with rotation/alerts; preserve fail-closed behavior. Safe verification:
use disposable files and synthetic actors only. Residual risk: external logging adds
credentials, availability and privacy dependencies.

### Informational — public repository contains no detected secret-like tracked file

Evidence: the offline posture checker enumerates tracked paths and rejects `.env`, key,
certificate, dump, backup and `secrets/` paths; examples contain placeholders only.
This is filename defense, not content scanning or proof that Git history never held a
secret. Before every push, review the staged diff and GitHub secret-scanning alerts.

## Verification and retest checklist

Repository-safe checks:

```sh
python3 scripts/security_posture.py
python3 scripts/validate.py
python3 scripts/supabase_config.py
python3 -m unittest discover -s tests -v
(cd apps/admin && npm ci --ignore-scripts && npm test && npm run test:browser && npm audit --omit=dev)
```

Before changing the verdict, additionally require evidence for:

- Debian/T2 boot, encryption, thermal/lid behavior, time, security updates and recovery;
- effective SSH, listeners, firewall plus Docker forwarding, AppArmor/systemd and an
  unauthorized LAN/tailnet negative client;
- every selected container's health, UID/capabilities/mounts/resources and reboot;
- real admin allow/deny/revocation/TLS and console display tty/crash/reboot recovery;
- per-app public route/credential/limit/CORS tests and management-route denials;
- full schema/grant/function/extension/RLS/Storage/Realtime audit and two-user negatives;
- absent-drive alert, real backup, removal/power interruption, isolated/off-host restore,
  secret escrow and measured recovery objectives;
- failed update, compatible rollback and maintenance-mode write-drain proof.

## 30-day maintenance plan

- Days 1–3: keep deployment/public exposure disabled; review this report and assign
  owners for hardware, identity, ingress, application authorization and recovery.
- Days 4–10: qualify Debian/T2 and Docker/firewall/Tailscale in a disposable rehearsal;
  close the browser floor and record exact effective settings.
- Days 11–17: inventory applications and managed Supabase catalogs; build and test RLS,
  Storage and Edge Function migration cases using synthetic or approved scrubbed data.
- Days 18–23: implement the minimal public ingress/credential/maintenance layer and run
  low-rate allow/deny tests only against explicitly authorized routes.
- Days 24–27: execute removable and off-host restore drills into new isolated targets;
  measure recovery time and verify secret escrow with a second operator procedure.
- Days 28–30: rehearse update/rollback and reboot/network/power failure; rerun the full
  checklist, triage GitHub dependency/secret alerts, and issue a new signed-off verdict.

Any blocking failure keeps the verdict NO-GO. No schedule pressure justifies exposing
management endpoints, bypassing RLS, deleting volumes or restoring over live data.
