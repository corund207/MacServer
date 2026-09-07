# MacServer

Debian 13 backend-appliance foundation for a 2019 Intel MacBook Air. The source
brief calls the appliance VEXVortex; this saved project is the canonical workspace.

**Phase 1 is offline scaffolding, not an installed or production-ready server.**
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
