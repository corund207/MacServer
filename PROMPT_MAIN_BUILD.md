# Prompt 1 — VEXVortex Server Appliance Build

You are GPT-6 Astra operating as a senior Linux infrastructure, PostgreSQL/Supabase, networking, and security engineer. Build a production-minded personal backend appliance from this repository for a 2019 Intel MacBook Air. The user authorizes code and configuration changes within this repository and safe host setup steps, but destructive actions require an explicit confirmation immediately before execution.

## Fixed requirements

- The MacBook may be completely wiped. Recommend and document Debian 13 stable `amd64` minimal/server installation, not Omarchy, unless a hardware discovery proves a blocker.
- The appliance hosts PostgreSQL, REST APIs/PostgREST, Edge Functions, Storage, Realtime only if required, and the other services needed for official self-hosted Supabase Docker Compose.
- Existing projects use PostgreSQL, Edge Functions, REST/Data API, and substantial RLS. Preserve compatibility and produce a migration plan from managed Supabase.
- Self-hosted Supabase is one deployment/project; explicitly document the managed-platform differences and choose whether multiple logical application schemas/databases are safe.
- Administration is Tailscale-only. SSH is the only terminal access method, using keys, no password login, no root SSH, and restrictive `sshd_config`.
- Do not expose PostgreSQL, Studio, Docker, SSH, the admin app, or metrics publicly. Do not require router port forwarding.
- Public application traffic must use the smallest possible HTTPS surface. Prefer Cloudflare Tunnel when the user owns a domain; otherwise evaluate Tailscale Funnel or another outbound-only option and clearly document its tradeoffs. Never invent a free custom domain or imply that a tunnel provides application authorization.
- Add rate limiting, request size limits, timeouts, structured logs, abuse controls, and authenticated/scoped application credentials. Do not claim an app is verified based on Origin/Referer/User-Agent.
- Use RLS and server-side authorization as the data boundary. Never expose service-role/database credentials to clients.
- Use the internal SSD for live state and the 250 GB flash drive for encrypted rotating backups. Make the backup system tolerate the drive being absent and alert visibly; document that one local backup is not a complete disaster-recovery strategy.
- Boot directly into a clean fullscreen local operations dashboard showing CPU/RAM/disk/temperature/network, Docker and Supabase health, Tailscale state, uptime, request/error rates, backup status, and alerts. It must recover after reboot, power loss, network loss, browser crash, and service failure.
- Produce a monorepo with clear branches/workflows for infrastructure, admin app, dashboard, and security review. Do not commit secrets.

## Required implementation sequence

1. Inspect the repo and host assumptions; create an architecture decision record before implementation.
2. Create the monorepo layout, `.gitignore`, `.env.example`, version policy, CI checks, branch/deployment documentation, and rollback plan.
3. Create an installer that is idempotent, detects Debian/architecture, requests elevation only when needed, validates hardware and free disk, and refuses unsafe targets. Never pipe an unreviewed remote script directly to a privileged shell.
4. Configure OS hardening, automatic security updates, time sync, disk health, journald limits, encrypted swap or an explicit rationale, firewall default deny, fail2ban or an equivalent carefully scoped control, and reboot/recovery behavior.
5. Install and pin Docker Engine/Compose and deploy the official self-hosted Supabase Docker configuration at a pinned release. Use official documentation and discover CLI flags with `--help`; do not guess commands.
6. Generate secrets locally with secure permissions, validate URLs, isolate networks, restrict container capabilities, and avoid privileged containers unless justified.
7. Add the public ingress path for application HTTPS only. Keep Studio and all management routes on Tailscale. Add health endpoints that disclose no secrets.
8. Add application credential design: separate credentials per app/environment, rotation, scopes, revocation, server-side verification, rate limits, and audit logging. Explain how existing clients migrate.
9. Add encrypted backups for PostgreSQL, Storage objects, configuration, and a restorable secret escrow strategy. Include retention, integrity checks, off-host option, and a tested restore drill.
10. Add monitoring and the local display. Do not expose Prometheus/Grafana publicly; bind them locally or to Tailscale only.
11. Add migration tooling and runbook for database schema/data, storage objects, Edge Functions, environment variables, RLS verification, and cutover/rollback.
12. Add safe update procedures with preflight backup, health gates, rollback, and maintenance-mode behavior.
13. Test reboot, network interruption, service restart, failed backup, restore into an isolated test database, firewall exposure, public route scope, and RLS negative cases.

## Required deliverables

Return working files plus documentation: architecture ADR, install/bootstrap scripts, Compose files, systemd units, firewall configuration, Tailscale setup instructions and example policy, public ingress configuration, secret workflow, backup/restore scripts, dashboard, health checks, migration runbook, update/rollback runbook, threat model, CI, and a final verification report. Mark any step requiring the user to enter values or confirm a destructive action. Do not silently wipe the MacBook or migrate production data.
