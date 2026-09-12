# Product

<!-- impeccable:product-schema 1 -->

## Platform

web

## Stack

The appliance uses small Node.js services and dependency-light browser clients.
Infrastructure and recovery tooling use Python 3 from Debian 13. This stack is
inferred from the existing repository and the fixed appliance brief.

## Users

The primary user is the appliance owner operating a single 2019 Intel MacBook Air.
They administer remotely over Tailscale and glance at the laptop's local display
to decide whether the appliance is healthy or needs attention.

## Product Purpose

MacServer is a security-first, self-hosted Supabase-compatible backend appliance.
It keeps administration private, makes degraded and unverified state obvious, and
provides reviewable paths for backup, recovery, migration, and maintenance.

## Positioning

The product's defining mechanism is evidence-first operations: every displayed
state names its source and freshness, while unavailable evidence is never converted
into a reassuring but unsupported health claim.

## Operating Context

The appliance runs Debian 13 on an Intel MacBook. The local display is a fullscreen,
read-only status board. Remote administration uses Tailscale and key-only SSH or the
separately authenticated private admin app. Public traffic is limited to explicitly
approved HTTPS application routes through an outbound-only tunnel.

## Capabilities and Constraints

- One self-hosted Supabase-compatible deployment, with application separation at
  database/schema, credential, authorization, rate-limit, and RLS boundaries.
- No public PostgreSQL, Studio, Docker, SSH, metrics, dashboard, or admin app.
- No client identity claim, header, or IP address is an authorization boundary.
- Live state belongs on the internal SSD. Encrypted rotating backups target a
  separately identified 250 GB removable drive and must tolerate its absence.
- Destructive operations require immediate explicit confirmation and a verified
  backup/rollback path. The repository may stage them but never silently run them.
- Hardware qualification, real credentials, domains, tailnet identities, production
  migrations, and destructive restore drills remain operator-supplied gates.

## Evidence on Hand

The repository contains version-pinned service configuration, offline validators,
synthetic security tests, a private read-only admin slice, operator runbooks, and
phase handoffs. It contains no production telemetry, secrets, device identifiers,
domain, or successful target restore evidence; future work must not fabricate them.

## Product Principles

- Fail closed at every network, identity, and privilege boundary.
- Prefer observable evidence to inferred health.
- Keep changes declarative, pinned, idempotent, reversible, and reviewable.
- Separate display, administration, application ingress, and privileged execution.
- Treat restore verification—not backup creation—as the recovery gate.

## Accessibility & Inclusion

Browser surfaces must work by keyboard, honor reduced motion and color-scheme
preferences, maintain readable contrast, and expose equivalent text for charts and
status color. These requirements are inferred from the existing tested admin app.
