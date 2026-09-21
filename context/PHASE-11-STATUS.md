# Phase 11 — scoped public application setup

Delivered a dependency-free Node gateway, pinned Node/Cloudflare Compose sidecar,
approval-gated systemd candidate, project bundle generator, and public-app runbook.
The tunnel joins only the edge network. Gateway joins edge and internal data;
neither publishes host ports. The original data Compose stays internal-only.

Supported: password/refresh Auth, get user/logout, and explicit table CRUD through
the api schema. Data requests require a scoped publishable key plus a user token
verified with Auth. RLS remains authoritative; public keys do not prove app identity.
Management, RPC, joins, anonymous data, signup, Storage, Functions and Realtime
routes are denied. Access-token revocation follows configured JWT expiry.

Bounds: body 1 MiB, response 2 MiB, 15-second request deadline, 32 active requests,
128 connections, global/app/Auth budgets, allowlisted headers and redacted audit.
No database or service-role credential is mounted in the public gateway.

Project scaffolding refuses overwrites and validates SQL identifiers/HTTPS origins.
Generated schemas use owner-scoped RLS and explicit authenticated grants. This is
an example, not an inferred production schema. Related apps share Auth; unrelated
trust domains still require separate deployments.

Validation: nine local gateway tests, three project/topology tests, existing offline
checks. CI additionally parses Compose and the systemd candidate on Linux.
No images were pulled or executed locally; official registry amd64 manifest digests
were resolved before pins were added. No real tunnel, account, endpoint or database
was provisioned. Hardware, restore and real two-user API tests remain required.
