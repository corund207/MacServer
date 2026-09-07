# Prompt 2 — VEXVortex Tailscale-Only Admin App

You are GPT-6 Astra. Build a polished, secure private control-plane web application in the VEXVortex monorepo. It manages one Debian 13 MacBook backend appliance and is reachable only over Tailscale. This is an administrative app, not a public Supabase client.

## Scope

Provide a responsive desktop-first interface with: overview and alerts; CPU/RAM/temperature/disk/network graphs; Docker and Supabase service health; logs with safe filtering; Tailscale device/status view; backup status, manual backup, retention, and restore workflow; file browser for explicitly allowlisted storage roots with upload/download/delete safeguards; database metadata viewer and carefully guarded SQL console; service restart/update controls; configuration status; audit log; and links to SSH documentation. Do not include a browser terminal—SSH is the only terminal.

## Security architecture

- Bind only to localhost/Tailscale; add a startup assertion that fails closed if a public interface is used.
- Require Tailscale identity context plus a separate admin authorization layer. Do not trust a source IP alone.
- Use short-lived sessions, secure HttpOnly/SameSite cookies, CSRF protection, strict CSP, no inline arbitrary scripts, secure headers, input validation, output encoding, and re-authentication for destructive actions.
- Use a backend service account with least privilege and explicit operation allowlists. Never send service-role keys, DB passwords, Docker socket access, or private keys to the browser.
- Prefer a narrow privileged helper API over mounting `/var/run/docker.sock`; if a socket is unavoidable, isolate and justify it.
- Require confirmation, reason, audit event, and backup/health preflight for destructive or disruptive actions.
- SQL console defaults to read-only, blocks dangerous statements, uses a transaction/timeout/row limit, and requires an explicit elevated mode for migrations. Never bypass RLS casually.
- File operations are confined beneath configured roots, prevent traversal/symlink escapes, enforce size/type limits, and use atomic writes.

## UX requirements

Make it calm and production-like: clear current health, degraded states, timestamps, stale-data indicators, keyboard-accessible controls, dark/light themes, mobile-safe layout, and no fake metrics. Every action reports what happened and links to logs/audit evidence. Secrets are redacted by default.

## Engineering requirements

Use the repository's existing stack if present; otherwise choose a maintained TypeScript web stack with a pinned lockfile and a small server API. Separate UI, read-only telemetry, and privileged operations. Add unit tests, authorization tests, route tests, accessibility checks, and a production build. Include `.env.example`, local development instructions, Tailscale-only deployment configuration, and an operator guide.

Before coding, inspect `AGENTS.md` and all applicable nested instructions. Verify current Tailscale and Supabase behavior from official documentation. Do not invent APIs. Finish with a threat model, changed-file summary, test results, and explicit residual risks.
