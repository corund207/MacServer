# MacServer Agent Instructions

MacServer turns a 2019 Intel MacBook Air (Apple T2 chip) into a private backend
server: Debian 13 with the t2linux kernel, self-hosted Supabase in Docker, a private
admin page, and an optional public API route through a Cloudflare Tunnel. The whole
setup ships as a bootable USB image (`iso/`, Omarchy-style): it erases the SSD,
installs encrypted Debian with the T2 kernel, and first boot runs the guided,
resumable installer (`install.sh --unattended`). `install.sh` also works on a
manually installed Debian 13.

## Security rules

- Administration happens only over Tailscale: Tailscale SSH and `tailscale serve`.
  No SSH daemon, no router port forwarding, and nothing listens on the LAN.
- Docker publishes ports on 127.0.0.1 only (`host/docker-daemon.json`). Never
  publish PostgreSQL, Studio, postgres-meta, Docker or the admin page publicly.
- The public route is outbound-only (cloudflared) and passes only the Supabase
  API paths allowed in `gateway/Caddyfile`. Anything not listed there returns 404.
- The admin page trusts `Tailscale-User-Login` only on requests from 127.0.0.1
  (tailscale serve), and only for logins on the allowlist.
- Never commit secrets, tunnel tokens, `.env` files or real identities.
- Every third-party package source is pinned by key fingerprint, and every
  container image by version and digest. Do not add curl-to-shell installs.
- Installer steps must be idempotent, and must ask before any step that changes
  networking, the boot kernel, or public exposure.

## Work rules

- Keep `context/STATUS.md` current: what exists, what was verified, what was not.
- Run `tests/run.sh` (shell syntax, Python unit tests) before committing. CI also
  runs shellcheck, Caddy validation and Compose validation.
- Changes to `iso/`, `installer/` or `host/` must pass the `installer-image` workflow:
  it builds the image and installs it end to end in a UEFI QEMU VM.
- The USB installer erases a disk only after the operator types ERASE (or an
  answers file names the same disk in CONFIRM_ERASE).

## Git delivery workflow

- Treat each completed feature or meaningful subfeature as a delivery unit.
- At the end of each delivery unit, run the relevant tests and checks, review the diff, and create one focused commit before starting unrelated work.
- Use a concise imperative commit subject that names the feature.
- Commit using the Git user identity already configured for this repository. Do not invent an identity, impersonate another person, or rewrite existing commit history.
- Do not add `Co-authored-by` trailers or any other co-author attribution.
- Push each completed commit to the configured upstream branch after the checks pass. If no remote/upstream exists, stop and report the exact setup needed.
- Never commit secrets, credentials, private keys, generated sensitive data, or unreviewed destructive changes.
- If a feature is incomplete, blocked or fails validation, do not commit it as complete; record the state in `context/STATUS.md`.
