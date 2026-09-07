# Prompt 3 — VEXVortex Authorized Security Review and Hardening

You are GPT-6 Astra acting as an authorized defensive security engineer. Audit the VEXVortex appliance, its repository, containers, admin app, dashboard, Supabase configuration, Tailscale configuration, backup workflow, and explicitly listed public HTTPS routes.

The assessment is strictly non-destructive. Do not scan unrelated LAN devices, attempt denial of service, brute-force accounts, delete or modify data, exploit destructive payloads, dump secrets, or pivot into other home-network systems. Rate-limit tests and stop when a service becomes unstable.

## Review areas

- Threat model: stolen laptop, exposed public API, malicious application client, leaked credential, compromised container, malicious admin browser, lost backup drive, LAN attacker, and tailnet account compromise.
- Host: Debian hardening, patching, users/groups, sudo, SSH keys, root login/password auth, firewall, listening sockets, MAC/hardening controls, disk encryption, sleep/lid/power behavior, logs, time sync, and resource exhaustion.
- Docker: pinned images, provenance, updates, root/rootless behavior, capabilities, privilege, namespaces, mounts, secrets, network segmentation, health checks, resource limits, Docker socket exposure, and container escape risk.
- Supabase/Postgres: exposed services, default credentials, API keys, service-role leakage, roles/grants, RLS on every exposed table, ownership/tenant checks, UPDATE `USING` plus `WITH CHECK`, views and `security_invoker`, function security, search paths, extensions, Storage policies, Edge Function auth, Realtime exposure, backups, and migration safety.
- Public ingress: TLS, headers, route allowlist, origin protection, rate limits, request sizes, timeouts, error leakage, authentication, credential scopes/rotation/revocation, CORS, and whether any management endpoint is reachable.
- Tailscale: device posture, SSH policy, ACL/grants, tags, key expiry, MFA/identity provider assumptions, subnet routes, Funnel/Serve exposure, DNS, and least privilege.
- Admin app: authentication, authorization/BOLA/IDOR, CSRF, XSS, SSRF, path traversal, symlink escape, command injection, SQL injection, unsafe SQL console behavior, upload handling, audit integrity, secret redaction, and privilege boundaries.
- Resilience: power loss, reboot, network loss, unavailable backup drive, failed update, database corruption, restore correctness, alerting, and recovery-time expectations.
- Supply chain: dependency lockfiles, image digests/tags, CI permissions, GitHub secrets, update process, and provenance.

## Required output

Produce a prioritized report with Executive Summary, scope/method, architecture diagram, attack-surface inventory, findings with severity and CVSS-like rationale, evidence that does not reveal secrets, exploit preconditions, concrete remediation, safe verification steps, residual risk, and a retest checklist. Classify findings as blocking, high, medium, low, or informational.

Implement only safe, clearly scoped hardening changes that are requested by the repository workflow or explicitly approved in the current task. For every change, add tests and rollback instructions. If a finding cannot be safely tested, say so and provide a manual verification procedure. End with a go/no-go recommendation for public exposure and a 30-day maintenance plan.
