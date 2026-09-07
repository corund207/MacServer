# MacServer

Debian 13 backend-appliance foundation for a 2019 Intel MacBook Air. The source
brief calls the appliance VEXVortex; this saved project is the canonical workspace.

**Phases 1–2 are offline foundations, not an installed or production-ready server.**
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
