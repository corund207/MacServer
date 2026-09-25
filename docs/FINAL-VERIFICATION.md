# Final repository verification and readiness boundary

Date: 2026-09-12. Eight repository phases are complete, including fail-closed target
commissioning and versioned update tooling. The result is not an installed server and
not a GO for production or public exposure. Missing physical/runtime evidence is
intentionally represented as blocked rather than inferred from synthetic tests.

## Consolidated verifier

From a clean checkout with Python, Node dependencies and Playwright Chromium already
installed, run:

```sh
python3 scripts/release_verify.py --full --require-clean --require-upstream-sync \
  --output build/release-verification.json
```

The verifier uses argument-vector subprocesses without a shell, suppresses child output
from its manifest, and writes a mode-0600 atomic JSON file only directly under the
ignored `build/` directory. It records commit, branch, clean state, individual pass/fail
states, clean/upstream synchronization and exact scope. It always records `targetEvidence: false`,
`publicExposureApproved: false` and `verdict: NO-GO`; source tests cannot change those
facts. On failure, run the named underlying command from the audit checklist to obtain
diagnostics. Do not publish the build artifact as runtime certification.

Production dependency advisory checks are separate because they query package registry
state and can change independently:

```sh
(cd apps/admin && npm audit --omit=dev)
```

## Main-build requirement traceability

| # | Requirement | Repository implementation/evidence | Status |
| --- | --- | --- | --- |
| 1 | Inspect and decide architecture | ADR, phased context and architecture handoff | Repository-complete |
| 2 | Layout, ignores, examples, pins, CI, rollback | Monorepo files, commit-pinned CI, locked runtimes/images | Repository-complete |
| 3 | Safe Debian installer | Preflight plus qualification-gated immutable commissioning | Target execution unverified |
| 4 | Host hardening and recovery behavior | SSH/nftables/journal/update/time/lid candidates and runbooks | Target-unverified |
| 5 | Pinned Docker/Supabase | Official pinned source, amd64 digests, internal Compose, no ports | Target-unverified |
| 6 | Secrets and isolation | Exclusive private generators, required substitutions, internal network | Target-unverified |
| 7 | Minimal public HTTPS | No public ingress or listener exists | Blocked on domain/routes/apps/credentials |
| 8 | Per-app credentials and abuse controls | Design requirements and admin limits only | Public application layer blocked |
| 9 | Encrypted backup and restore | restic workflow, retention/check/drill, disposable tests | Physical/off-host restore unverified |
| 10 | Monitoring and local display | Offline tty1 console display and fixed-command collector | Target console font/tty/hardware unverified |
| 11 | Migration tooling | Bundle, catalog/RLS checks and runbook | Real app schema/cutover blocked |
| 12 | Safe updates | Backup/restore/rollback gates and maintenance requirements | Write-drain mechanism blocked with ingress |
| 13 | Resilience/security tests | Offline negative, browser, unit and interruption fixtures | Target reboot/network/power/exposure unverified |

## Deliverable inventory

- Architecture ADR/handoff: `docs/adr/0001-staged-foundation.md`,
  `context/ARCHITECTURE.md`.
- Install/bootstrap, host candidates, firewall and Tailscale example: `scripts/bootstrap.py`,
  `docs/INSTALL.md`, `infra/host`, `infra/tailscale-policy.example.json`.
- Supabase, secrets and migration: `infra/supabase`, `scripts/supabase_secrets.py`,
  `scripts/migration_bundle.py`, `scripts/schema_audit.py`, `docs/MIGRATION.md`.
- Private read-only admin and console display: `apps/admin`, `apps/console`, their
  inert systemd candidates and operator guides. A browser terminal is intentionally absent.
- Backup/update/recovery: `scripts/backup.py`, `infra/backup`,
  `docs/BACKUP-RESTORE.md`, `docs/UPDATE-ROLLBACK.md`.
- Security and verification: `security/SECURITY-AUDIT.md`, threat models,
  `scripts/security_posture.py`, this matrix and the consolidated verifier.

The public repository is source distribution only. Approval markers, environment files,
TLS keys, app tokens, database credentials, real UUIDs, tailnet identities, domains and
recovery evidence must remain outside Git.

## Safe deployment and qualification order

1. Read the Phase 6 findings and keep the NO-GO verdict. Choose recovery objectives,
   domain/public routes, application inventory and trust-domain separation.
2. Qualify Debian 13 on the exact Intel MacBook with recovery boot, encrypted SSD,
   console access, networking, temperature, lid/power and reboot tests. OS installation
   or disk changes require fresh explicit confirmation and separate backups.
3. Rehearse authenticated package installation and every host candidate in an isolated
   target-equivalent VM. Preserve originals and prove rollback one subsystem at a time.
4. Establish Tailscale MFA/device controls and key-only SSH. Validate complete additive
   grants plus unauthorized identity and LAN negative cases before considering admin.
5. Place Docker state on encrypted storage; validate effective firewall forwarding,
   listeners, service UID/capabilities/mounts/resources/health and reboot behavior.
6. Initialize rotating encrypted media and independent password escrow. Pass absent-
   drive, real backup, integrity, isolated restore and off-host restore tests.
7. Inventory and rehearse managed Supabase migration with approved scrubbed data. Close
   every catalog finding and pass per-app two-user/anonymous/RLS/Storage/function tests.
8. Implement the separately reviewed public ingress, app credentials and real write-
   drain maintenance behavior. Test only explicit routes at low rate; management denial
   is mandatory. No router forwarding.
9. Qualify admin TLS and identity, and the console display's tty, font,
   crash/network/reboot behavior. Only then consider creating individual approval markers.
10. Rehearse a failed update and compatible rollback. Rerun the complete checklist and
    issue a new evidence-backed GO/NO-GO decision before production data or exposure.

## Rollback and retest

Repository rollback is `git revert` of the focused delivery commit followed by the full
verifier; do not rewrite shared history. Host rollback restores the exact captured
pre-change files while console and a second SSH session remain available. Service/data
rollback follows `docs/UPDATE-ROLLBACK.md` and restores only into new isolated state
after immediate explicit approval—never delete volumes or overwrite live data.

Retest after every runtime/dependency/image/policy/schema/credential/browser change and
at least monthly. A failed or missing manual evidence item remains blocked. The Phase 6
30-day plan is the minimum path toward a future target-qualified verdict.
