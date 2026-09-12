# MacServer

**A security-first, evidence-driven backend appliance for a 2019 Intel MacBook Air.**

MacServer is a Debian 13 foundation for self-hosted Supabase-compatible services,
private Tailscale administration, a local operations display, and encrypted recovery.
It is designed to report what is known, how it was verified, and what remains blocked.

> **Evidence before confidence.** This repository is source-verified, not an installed
> appliance and not a production or public-exposure approval. Passing CI is not proof
> of target health.

| Evidence boundary | State | Meaning |
| --- | --- | --- |
| Source bundle | **REPOSITORY-VERIFIED** | Offline validators, unit tests, browser tests, and CI cover the committed source. |
| MacBook appliance | **TARGET-UNVERIFIED** | Debian, T2-era hardware, storage, firewall, identity, and recovery evidence has not been collected. |
| Production data | **NOT APPROVED** | Real credentials, schema, RLS cases, migration evidence, and recovery objectives remain operator-supplied gates. |
| Public exposure | **NO-GO** | No public ingress exists. Domains, routes, applications, credential scopes, and abuse controls remain undefined. |

The repository does not start services, open ports, modify disks, install packages,
run migrations, or create production credentials. Read the [final verification
boundary](docs/FINAL-VERIFICATION.md) before treating any artifact as deployable.

## Start here

| Your goal | First stop | Outcome |
| --- | --- | --- |
| Understand the current verdict | [Final verification](docs/FINAL-VERIFICATION.md) | See requirement traceability, blockers, and the safe qualification order. |
| Validate a clean checkout | [Verify the source bundle](#verify-the-source-bundle) | Reproduce the repository-only checks and machine-readable manifest. |
| Prepare the physical appliance | [Installation and target qualification](docs/INSTALL.md) | Review prerequisites, non-mutating preflight, staging, rollback, and hardware gates. |
| Continue the project safely | [Current handoff](context/NEXT-PHASE.md) | Gather the missing decisions and evidence before any deployment or exposure work. |

## Verify the source bundle

Prerequisites are Python 3.11+, installed Node dependencies, and Playwright Chromium.
From a clean checkout whose current branch tracks its upstream, run:

```sh
python3 scripts/release_verify.py --full --require-clean --require-upstream-sync \
  --output build/release-verification.json
```

A successful run verifies the committed source and writes a mode-`0600` manifest under
the ignored `build/` directory. The manifest deliberately retains
`targetEvidence: false`, `publicExposureApproved: false`, and `verdict: NO-GO`.

If a grouped check fails, run its reported command directly for diagnostics. Use
[`git revert`](docs/FINAL-VERIFICATION.md#rollback-and-retest) for repository rollback;
do not rewrite shared history. The [verification guide](docs/FINAL-VERIFICATION.md#consolidated-verifier)
documents scope, recovery, and the separate registry-dependent advisory checks.

## Choose your task

### Plan and understand

| Task | Guide | What it covers |
| --- | --- | --- |
| Understand the system | [Architecture](context/ARCHITECTURE.md) · [staging ADR](docs/adr/0001-staged-foundation.md) | Trust boundaries, service topology, and why deployment remains staged. |
| Understand the product | [Product principles](PRODUCT.md) · [design system](DESIGN.md) | Evidence-first operations, users, constraints, and interface language. |
| Review delivery evidence | [Final verification](docs/FINAL-VERIFICATION.md) · [security audit](security/SECURITY-AUDIT.md) | Requirement traceability, findings, and the repository-versus-target boundary. |

### Install and operate

| Task | Guide | What it covers |
| --- | --- | --- |
| Qualify and install | [Installation](docs/INSTALL.md) | Debian and hardware prerequisites, safe staging, host candidates, and rollback. |
| Configure data services | [Data services](docs/DATA-SERVICES.md) | Pinned Supabase-compatible services, secrets, isolation, validation, and recovery. |
| Migrate an application | [Migration](docs/MIGRATION.md) | Inventory, bundle creation, schema/RLS checks, cutover gates, and rollback. |
| Operate private administration | [Admin operator guide](docs/ADMIN.md) · [identity guide](docs/ADMIN-IDENTITY.md) | Tailscale-only access, TLS, identity, provisioning, audit, and recovery. |
| Run the local display | [Dashboard operator guide](docs/DASHBOARD.md) · [dashboard component](apps/dashboard/README.md) | Loopback kiosk deployment, evidence collection, browser floor, and recovery. |
| Back up and restore | [Backup and restore](docs/BACKUP-RESTORE.md) | Exact removable-media gates, encryption, retention, integrity, and isolated drills. |
| Update or roll back | [Update and rollback](docs/UPDATE-ROLLBACK.md) | Maintenance gates, backups, staged updates, failure recovery, and retesting. |

### Secure and contribute

| Task | Guide | What it covers |
| --- | --- | --- |
| Review system threats | [System threat model](security/THREAT-MODEL.md) | Assets, adversaries, trust boundaries, controls, and residual risks. |
| Review admin threats | [Admin threat model](security/ADMIN-THREAT-MODEL.md) | Identity, session, audit, network, browser, and privilege boundaries. |
| Reproduce the security audit | [Security audit](security/SECURITY-AUDIT.md) | Authorized scope, offline tests, dependency checks, findings, and remediation order. |
| Develop and deliver | [Development workflow](docs/DEVELOPMENT.md) · [admin component](apps/admin/README.md) | Local checks, focused commits, secret review, rollback, and app-specific commands. |

There is intentionally no public-ingress guide or configuration. That capability is
blocked until the exact domain, HTTPS routes, applications, server-side authorization,
credential scopes, rate limits, audit events, and RLS tests are reviewed. Management
surfaces must never become public.

## Architecture at a glance

```text
Public clients  -- blocked; no ingress is implemented

Operator        -- Tailscale --> key-only SSH / private admin
Local display   -- loopback  --> read-only operations dashboard
Applications    -- internal  --> Supabase-compatible data services
Recovery media  -- exact ID  --> encrypted, rotating restic backups
```

Public traffic may eventually reach only explicitly approved HTTPS application routes
through an outbound-only tunnel. PostgreSQL, Studio, Docker, SSH, metrics, the local
dashboard, and the admin app are never public endpoints. Origin, Referer, User-Agent,
and client IP are not authorization proof.

## Safety boundaries

- Keep administration private to Tailscale and use SSH keys only; never add router
  port forwarding.
- Enforce scoped credentials, server-side authorization, rate limits, audit logs, and
  RLS for every application boundary.
- Keep secrets, private keys, real device identifiers, domains, and approval evidence
  outside Git.
- Require immediate explicit confirmation plus a tested backup and rollback path before
  disk, restore, migration, credential, or production-data changes.
- Limit security testing to this appliance and its explicitly listed endpoints; never
  scan the broader LAN.

## Repository map

| Path | Purpose |
| --- | --- |
| `apps/admin/` | Private, read-only administration control plane. |
| `apps/dashboard/` | Loopback-only local operations display and bounded collector. |
| `infra/` | Inert, pinned host, service, systemd, Tailscale, and backup candidates. |
| `scripts/` | Non-mutating staging, validation, migration, security, backup, and release tooling. |
| `tests/` | Offline safety, policy, interruption, and regression tests. |
| `docs/` | Operator procedures, recovery paths, and the architecture decision record. |
| `security/` | System and admin threat models plus the authorized audit. |
| `context/` | Phase evidence, architecture state, and the current handoff. |

## What remains blocked

Target qualification is the next legitimate work. It requires the exact appliance and
explicitly listed test endpoints, followed by evidence for:

- Debian 13, recovery boot, encrypted internal storage, networking, thermals, lid and
  power behavior, reboot, and local console recovery;
- effective Docker isolation, firewall forwarding, Tailscale policy, admin TLS and
  identity, and a supported kiosk browser;
- application, domain, route, credential-scope, rate-limit, and audit inventories;
- real schema, RLS, Storage, function, migration, and two-user isolation cases; and
- physical encrypted-media, isolated restore, off-host restore, update rollback, and
  interruption rehearsals.

Keep production data and public exposure disabled until the complete target matrix is
independently reviewed. The [current handoff](context/NEXT-PHASE.md) records the exact
decisions to gather before work continues.

<details>
<summary><strong>Seven-phase repository delivery record</strong></summary>

| Phase | Delivery | Evidence |
| --- | --- | --- |
| 1 | Staged foundation and host candidates | [Phase 1 status](context/PHASE-1-STATUS.md) |
| 2 | Pinned data services and migration tooling | [Phase 2 status](context/PHASE-2-STATUS.md) |
| 3 | Private read-only administration | [Phase 3 status](context/PHASE-3-STATUS.md) |
| 4 | Local operations dashboard | [Phase 4 status](context/PHASE-4-STATUS.md) |
| 5 | Encrypted backup and isolated recovery | [Phase 5 status](context/PHASE-5-STATUS.md) |
| 6 | Repository security audit and hardening | [Phase 6 status](context/PHASE-6-STATUS.md) |
| 7 | Final verification and traceability | [Phase 7 status](context/PHASE-7-STATUS.md) |

The source brief used the name VEXVortex; MacServer is the canonical project workspace.
See the [phase plan](context/PHASE-PLAN.md) for the original delivery sequence.

</details>
