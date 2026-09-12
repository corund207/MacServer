# MacServer Agent Instructions

MacServer is a security-first self-hosted backend appliance for a 2019 Intel MacBook Air. Treat this repository as self-contained: use its local prompts and context, and read every nested `AGENTS.md` before changing a subtree. Do not depend on or modify another project repository.

Use Debian 13 stable, pinned Docker Compose services, self-hosted Supabase-compatible services, Tailscale-only administration, SSH keys only, no router port forwarding, and encrypted rotating backups to the 250 GB flash drive. Public traffic may reach only explicitly required HTTPS application routes through an outbound-only tunnel. Never expose PostgreSQL, Studio, Docker, SSH, metrics, or the admin app publicly.

Never trust Origin, Referer, User-Agent, or client IP as proof of an approved app. Use scoped credentials, server-side authorization, rate limits, audit logs, and RLS. Never commit secrets. Destructive actions require explicit user confirmation immediately before execution and a tested backup/rollback path. Security testing must be non-destructive and limited to this appliance and its explicitly listed endpoints.

At every phase, update `context/PHASE-*-STATUS.md`, `context/ARCHITECTURE.md`, and `context/NEXT-PHASE.md`. If usage is nearing its limit, stop at a safe checkpoint and write `context/USAGE-CHECKPOINT.md`.

## Git delivery workflow

- Treat each completed feature or meaningful subfeature as a delivery unit.
- At the end of each delivery unit, run the relevant tests and checks, review the diff, and create one focused commit before starting unrelated work.
- Use a concise imperative commit subject that names the feature, for example `Add encrypted backup verification`.
- Commit using the Git user identity already configured for this repository. Do not invent an identity, impersonate another person, or rewrite existing commit history.
- Do not add `Co-authored-by` trailers or any other co-author attribution.
- Push each completed commit to the configured upstream branch after the checks pass. If no remote/upstream exists, stop and report the exact setup needed rather than silently creating one.
- Never commit secrets, credentials, private keys, generated sensitive data, or unreviewed destructive changes.
- If a feature is incomplete, blocked, fails validation, or requires user confirmation, do not commit it as complete; save the state in the appropriate context handoff file instead.
