# Security design

## What is reachable, and from where

| Surface | Listens on | Reachable from |
| --- | --- | --- |
| Shell | Tailscale SSH (inside tailscaled) | Your tailnet, per your Tailscale policy |
| Admin page | 127.0.0.1:8090, published by `tailscale serve` on :443 | Approved tailnet logins only |
| Studio + full Supabase API | 127.0.0.1:8000, published by `tailscale serve` on :8443 | Your tailnet; Studio has its own password |
| PostgreSQL / pooler | 127.0.0.1:5432 / 6543 | This Mac only |
| Public app API | none: cloudflared connects out to Cloudflare | The Internet, only the paths below |

- **Firewall** (`host/nftables.conf`): input is dropped unless it arrives on
  `tailscale0`, is a reply, ICMP, DHCP, or Tailscale's UDP port. It lives in its own
  table and does not flush Docker's or Tailscale's rules.
- **Docker** (`host/docker-daemon.json`): `"ip": "127.0.0.1"` makes every published
  container port loopback-only, even if a compose file forgets to say so.
- **No OpenSSH, no router port forwarding.** The installer offers to disable OpenSSH
  if Debian installed it.

## Public route

`Cloudflare → cloudflared → Caddy (gateway/Caddyfile) → Supabase API gateway`.
Caddy passes only `/auth/v1/*`, `/rest/v1/*`, `/storage/v1/*`, `/realtime/v1/*`,
`/functions/v1/*` and `/graphql/v1`. It refuses `/auth/v1/admin*`,
`/realtime/v1/api/tenants*`, `/pg*` (postgres-meta), MCP and Studio. Anything else
returns 404. `tests/gateway_test.sh` checks this in CI against the real Caddy image,
including case, double-slash and percent-encoding variants.

Data protection for the public API is Supabase's: every table your apps use needs
**Row Level Security** policies. Keep public sign-up off unless you need it (the
installer asks). Add Cloudflare rate-limiting rules for `/auth/v1/*` when you go public.
`sudo macserver public off` removes public access at once.

## Admin page

A read-only Python standard-library server running as the unprivileged
`macserver-admin` user under a tight systemd sandbox. The loopback address plus the
`Tailscale-User-Login` header that `tailscale serve` sets is the identity check; the
login must be listed in `ADMIN_LOGINS` in `/etc/macserver/macserver.conf`. A process
already running on the Mac could forge that header, so treat local users as trusted.
Health data comes from a root collector that writes a JSON file containing no secrets
(only the publishable/anon keys, which are public by design).

The page has exactly one action: **Start / Stop Claude session**. The server accepts it
only from an allowlisted login, as same-origin JSON (`Origin` must be the tailnet
name, `Sec-Fetch-Site` same-origin; a cross-site page cannot send JSON without a CORS
preflight, which is never answered), and only the words `start` or `stop`. It writes
that word to `/run/macserver-admin/claude-request` and nothing else. The root-owned
`macserver-claude-control.path` reads it (ignoring symlinks) and starts or stops
`macserver-claude.service`, which runs `claude remote-control` as the owner in
`~/macserver-workspace`. The session is then reached through claude.ai/code or the
Claude app, signed in with the owner's claude.ai account. Anyone on the allowlist can
therefore start a Claude session with the owner's rights (but not their sudo password):
keep `ADMIN_LOGINS` to yourself. The session log is private to the owner (mode 0600).

## Supply chain

- **MacServer updates itself** (`installer/self-update.sh`, every 2 minutes, root).
  It follows `main` on github.com/corund207/MacServer over HTTPS and installs a commit
  only when the required CI jobs for that exact commit completed successfully:
  `checks` always, plus `disk-install`, `image` and `vm-test` when the change since
  the running version touches `iso/`, `installer/`, `host/`, `install.sh` or the
  workflows (or when the running version is unknown). It installs only a fast-forward
  of the running version (a rewritten history is refused). It backs up the database, re-runs
  the host, admin and claude steps, checks health and rolls back on failure; a
  rolled-back commit is not retried automatically. It never touches Supabase's
  version, Docker, the firewall or data. Trust: whoever can push to `main` and pass
  CI can run code as root on the server, so the GitHub account needs 2FA. Turn it off
  with `sudo macserver autoupdate off`.

- APT repositories (t2linux, Docker, Tailscale) are added only after the downloaded
  signing key matches the fingerprint pinned in `installer/lib.sh`, and each key is
  scoped to its own repository with `Signed-By`.
- The t2linux repository is pinned (`host/t2.pref`) so it can only supply packages
  whose names contain `t2` or start with `apple-`; it cannot replace Debian packages.
- Supabase is cloned at tag `self-hosted/v0.8.2`, and the installer refuses it unless
  the tag resolves to the pinned commit.
- Gateway images are pinned by version and digest.
- Claude Code is the native linux-x64 binary from the npm registry
  (`@anthropic-ai/claude-code-linux-x64`), pinned by version and the SHA-512 of its
  tarball (`installer/lib.sh`). No Node.js, no install script; its self-updater is off.

## Secrets

Generated on the Mac by Supabase's own scripts into `/opt/macserver/supabase/.env`
(mode 0600, directory 0700). The tunnel token lives in `/opt/macserver/gateway/.env`
(0600). Nothing secret is in this repository; `tests/test_repo.py` scans for
accidental tokens and keys.

## Known limits

- Full-disk encryption means a person must type the passphrase after any reboot.
- Backups are local dumps only; copy them off the Mac.
- The t2linux kernel is a community project. You trust its maintainers for the kernel.
