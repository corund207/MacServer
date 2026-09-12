# MacServer

Debian 13 backend-appliance foundation for a 2019 Intel MacBook Air. The source
brief calls the appliance VEXVortex; this saved project is the canonical workspace.

**The repository is an offline foundation, not an installed or production-ready server.**
No service starts, port opens, disk changes, package installs, or migrations occur.

```sh
python3 scripts/bootstrap.py preflight
python3 scripts/bootstrap.py stage --output /tmp/macserver-stage
python3 scripts/validate.py
python3 -m unittest discover -s tests -v
```

Preflight returns nonzero on unsupported hosts (including the current Omarchy
development machine). Staging works on a development machine and creates an
immutable configuration bundle; repeat runs verify it rather than overwrite edits.

Read [installation](docs/INSTALL.md), [architecture](context/ARCHITECTURE.md),
[Git workflow](docs/DEVELOPMENT.md), and [phase status](context/PHASE-1-STATUS.md).

Layout: `apps/admin` private control plane; `apps/dashboard` local display;
`infra` inert templates; `scripts` offline tooling; `tests` safety tests;
`security` threat model; `docs` operator guides; `context` phase handoffs.

Phase 2: [data services](docs/DATA-SERVICES.md), [migration runbook](docs/MIGRATION.md),
[status and validation limits](context/PHASE-2-STATUS.md). No service startup or SQL
execution is authorized by these configuration artifacts.

Phase 3: [private read-only admin](apps/admin/README.md), [operator guide](docs/ADMIN.md),
[threat model](security/ADMIN-THREAT-MODEL.md) and [verified scope](context/PHASE-3-STATUS.md).
Synthetic tests are local-only; no appliance service is deployed. SQL, collectors,
file writes and privileged operations remain unavailable.

Phase 4: [local operations dashboard](apps/dashboard/README.md), [deployment and
recovery guide](docs/DASHBOARD.md), and [verified scope](context/PHASE-4-STATUS.md).
The loopback display and bounded collector are tested with synthetic evidence; the
candidate Chromium version is below the recorded security floor, so target deployment
remains blocked pending a Debian security update and hardware/session qualification.

Phase 5: [encrypted backup and isolated restore](docs/BACKUP-RESTORE.md) with exact
removable-drive identity gates, logical PostgreSQL exports, Storage/configuration
coverage, retention and integrity checks. It remains an undeployed candidate pending
target drive qualification and a real restore rehearsal.

Phase 6: [authorized repository security audit](security/SECURITY-AUDIT.md), offline
regression checks, and private-admin port-policy hardening. The verdict remains NO-GO
for production or public exposure until the documented runtime blockers are closed.

Phase 7: [final verification and requirement traceability](docs/FINAL-VERIFICATION.md)
plus a consolidated machine-readable verifier. The seven-phase repository plan is
complete; physical deployment and production approval are explicitly separate work.
