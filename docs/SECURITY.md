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
  `tailscale0`, is a reply, ICMP (pings rate-limited), DHCP, or Tailscale's UDP port.
  A forward chain that runs before Docker's drops every new connection from the Wi-Fi,
  a cable or a phone to a container, whatever address the container publishes on. It
  lives in its own table and does not flush Docker's or Tailscale's rules.
- **Container ports** stay on 127.0.0.1. Docker's `"ip": "127.0.0.1"`
  (`host/docker-daemon.json`) covers only its default bridge, not Compose networks, so
  Supabase's published ports (API gateway 8000, pooler 5432 and 6543) are pinned to
  127.0.0.1 by `docker-compose.macserver.yml`, which the supabase and firewall steps
  write. The firewall's forward chain covers any container that still gets this wrong.
- **DNS** (`host/resolved.conf`): systemd-resolved answers on 127.0.0.53 only, for
  the Mac and (through Docker) its containers. LLMNR and mDNS are off, so nothing
  answers name broadcasts from the LAN. It asks the network's DNS server and Quad9 /
  Cloudflare side by side, over TLS when a server offers it (opportunistic: this
  encrypts but does not authenticate). Tailscale does not manage the Mac's DNS
  (`--accept-dns=false`): tailscaled's own lookups cannot reach Tailscale's resolver.
- **Kernel network settings** (`host/sysctl.conf`): no ICMP redirects or source
  routing accepted or sent, SYN cookies, loose reverse-path filtering (strict mode
  breaks Tailscale).
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

### Web terminal (`/term`)

The admin page's **terminal** tab is a login shell in the browser. It is the one part of
the page that is not read-only, and it is deliberately not sandboxed: `macserver-terminal`
runs `admin/terminal.py` as the **owner's account** (the same drop-in pattern as the Claude
session), on `127.0.0.1:8091`, and `tailscale serve` mounts it at `/term` on the admin
page's own HTTPS address. It gives exactly what Tailscale SSH gives (the owner's shell; `sudo`
still asks for the password), so the same rule applies: keep `ADMIN_LOGINS` to yourself.

The gate is the admin page's (loopback plus an allowlisted `Tailscale-User-Login`), plus
two checks that browsers make necessary for WebSockets: `Origin` must be exactly
`https://<TAILNET_NAME>` and `Sec-Fetch-Site` must be same-origin, so a page on another
site cannot open a shell through the owner's browser. At most four shells are open at once;
closing the WebSocket, or stopping the service, hangs up the shell's whole process group.
The terminal page alone gets a relaxed style policy (xterm.js styles itself inline) and
is framed only by the admin page; xterm.js is vendored and pinned by SHA-256 in
`tests/test_admin.py`.

## Doomsday lockdown

When connections or processes look like an intrusion, the Mac isolates itself
(`installer/steps/50-doomsday.sh`, on by default; `sudo macserver doomsday off`
disables detection only).

- **Detection** (`admin/doomsday.py`, checked every minute): scores inbound
  LAN/public connections, unexpected listeners and outbound traffic, API 5xx
  spikes and unknown root processes. Tailscale SSH, loopback, DNS/NTP/Tailscale
  traffic and the allowlisted AI-agent/owner processes never score; LAN SSH is
  noted but cannot fire alone. Isolation needs a score over `DOOMSDAY_SCORE`
  (default 6) for `DOOMSDAY_CONSECUTIVE` (default 2) checks in a row.
- **Alert first**: the server POSTs a redacted payload (hostname, IPs, trigger,
  score, findings; secrets stripped) to the `doomsday-alert` GitHub workflow,
  which emails the owner. The workflow holds the SMTP secrets; the Mac holds
  only a dispatch token in `/etc/macserver/doomsday-gh-token` (0600, set up
  with `sudo macserver doomsday setup-gh`). The alert never blocks isolation.
- **Isolation**: stops the public tunnel, Supabase, the admin page and
  Tailscale; kills remaining containers; applies `host/nftables-doomsday.conf`
  (drop on input, forward AND output; only loopback, DHCP and SSH from private
  LAN addresses pass); drops swap, locks extra encrypted volumes
  (`DOOMSDAY_CRYPT_CLOSE`), and shows the lockdown screen (console banner plus
  a red dashboard incident). The root disk is already LUKS-encrypted; doomsday
  locks what can be locked without bricking the running system.
- **Recovery needs a Google Authenticator code** (TOTP, stdlib-only,
  `sudo macserver doomsday setup-totp`): `unlock --code` enters DEBUG mode
  (still isolated, amber banner, diagnose freely), `restore --code` returns to
  normal and auto-restores the firewall, Tailscale, Docker, Supabase, the
  public route and the admin page from a pre-isolation snapshot. OpenSSH is
  started only during lockdown (LAN only) and returned to its prior state.
  The lockdown firewall persists across reboots until restored.

## Supply chain

- **MacServer updates itself** (`installer/self-update.sh`, every 2 minutes, root).
  It follows `main` on github.com/corund207/MacServer over HTTPS and installs a commit
  only when the required CI jobs for that exact commit completed successfully:
  `checks` always, plus `disk-install`, `image` and `vm-test` when the change since
  the running version touches `iso/`, `installer/`, `host/`, `install.sh` or the
  workflows (or when the running version is unknown). It installs only a fast-forward
  of the running version (a rewritten history is refused). It backs up the database,
  re-runs the host, firewall, admin, claude and autoupdate steps (screen, DNS, kernel
  network settings, firewall, Supabase's local-only ports, admin page, Claude Code,
  the updater), checks health and rolls back on failure; a rolled-back commit is not
  retried automatically. It never changes Supabase's version, Docker or data.
  Trust: whoever can push to `main` and pass CI can run code as root on the server,
  so the GitHub account needs 2FA. Turn it off with `sudo macserver autoupdate off`.

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
