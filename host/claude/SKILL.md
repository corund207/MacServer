---
name: macserver
description: What this machine is (a MacServer: a 2019 MacBook Air running Debian 13 with self-hosted Supabase behind Tailscale) and how to operate it safely. Use for any question about this server, its Supabase backend, connecting apps to it, the public API route, backups, updates or troubleshooting.
---

# MacServer

You are running on a **MacServer**: a 2019 Intel MacBook Air (Apple T2 chip,
MacBookAir8,2) turned into a private backend server. The owner uses it to host
the backend for their own apps. Project and guides: https://github.com/corund207/MacServer

## What runs here

- Debian 13 (trixie) with the t2linux kernel (`linux-t2-lts`). The T2 drivers
  (`t2bce_*` keyboard/trackpad, `brcmfmac` Wi-Fi, `applesmc`) come from it.
- Self-hosted **Supabase** (`self-hosted/v0.8.2`) in Docker, in `/opt/macserver/supabase`.
  Its API gateway (Envoy, container `supabase-api-gw` or similar) listens on
  `127.0.0.1:8000` only. The database container is `supabase-db`.
- **Tailscale** is the only way in. `tailscale serve` publishes, on the tailnet only:
  - admin page: `https://<name>.<tailnet>.ts.net/` (read-only health page, plus the
    button that started this Claude session)
  - Supabase Studio and the private API: `https://<name>.<tailnet>.ts.net:8443/`
- Optional **public API route**: `cloudflared` (outbound-only Cloudflare Tunnel) ->
  Caddy (`/opt/macserver/gateway`, `gateway/Caddyfile` in the project) -> Supabase.
  Only `/auth/v1`, `/rest/v1`, `/storage/v1`, `/realtime/v1`, `/functions/v1` and
  `/graphql/v1` pass; everything else (Studio, `/pg`, admin APIs) returns 404.
- The console screen (tty1) always shows a read-only dashboard.

Find the live names with `tailscale status`, `sudo macserver status` and
`cat /etc/macserver/macserver.conf` (TAILNET_NAME, PUBLIC_DOMAIN, SITE_URL).

## The `macserver` command (needs sudo)

| Command | Does |
| --- | --- |
| `sudo macserver status` | health: Mac, Tailscale, every container |
| `sudo macserver keys` | API keys and Studio password (SECRET: do not print them into chat unless the owner asks; never put secret keys in client code) |
| `sudo macserver logs [auth\|rest\|db\|storage\|...]` | follow Supabase logs |
| `sudo macserver restart` | restart Supabase and the public route |
| `sudo macserver backup` | database dump to `/var/backups/macserver` (keeps 10) |
| `sudo macserver update` | backup, then Debian, T2 kernel and container updates |
| `sudo macserver public setup\|on\|off` | the Cloudflare public API route |
| `sudo macserver wifi` / `doctor` | Wi-Fi setup / network, DNS and T2 checks |

`sudo` asks for the owner's password. If you cannot get it, give the owner the
exact command to run instead.

## Connecting an app

- **Supabase URL**: the public domain (`https://<PUBLIC_DOMAIN>`) for apps on the
  Internet, or `https://<name>.<tailnet>.ts.net:8443` for apps/devices on the tailnet.
- **Client apps** (web, mobile) get the publishable / anon key only.
- **Servers you control** (edge functions, backends) may use the secret /
  service_role key; it must never ship in a browser or mobile app.
- Use the normal Supabase client libraries (`@supabase/supabase-js`,
  `supabase-flutter`, `supabase-py`, ...) with that URL and key.
- Auth email links use `SITE_URL` (in `macserver.conf` and the Supabase `.env`).
- Schema changes: write SQL migrations and apply them with
  `docker exec -i supabase-db psql -U postgres -d postgres < migration.sql`, or use
  Studio's SQL editor. Keep migrations in the owner's app repository.
- Row Level Security: enable RLS on every table exposed through `/rest/v1`, and
  write policies before telling the owner an app is ready.

## Rules you must follow here

- **Never expose anything new.** No router port forwarding, no ports published on
  0.0.0.0, no OpenSSH, and never make Postgres, Studio, postgres-meta, Docker or the
  admin page public. In a compose file always write the address:
  `ports: ["127.0.0.1:8081:8080"]` (Docker's 127.0.0.1 default covers only its default
  bridge, not Compose networks; the firewall also drops LAN connections to containers).
  To let the Internet reach a new API path, explain the change to `Caddyfile` and let
  the owner decide.
- **DNS** goes through systemd-resolved (`resolvectl status`); Docker hands every
  container's lookups to it. Do not hard-code DNS servers (`dns:`) in compose files,
  and do not edit `/etc/resolv.conf` or turn on Tailscale DNS (`--accept-dns`).
- **Back up before risky database work**: `sudo macserver backup`.
- Do not edit `/opt/macserver/supabase/docker-compose.yml` or pinned image
  versions/digests by hand; updates go through `sudo macserver update`.
- Never print, commit or send secrets (`.env`, keys, tunnel token, passwords).
- The Mac must stay on: do not suspend, change the kernel, GRUB or networking
  without asking the owner first.
- Work in `~/macserver-workspace` (or the owner's own project folders).

## Troubleshooting

- Containers down: `sudo macserver status`, then `sudo macserver logs <service>`.
- Network/DNS: `sudo macserver doctor`, `resolvectl status`. Wrong clock breaks
  HTTPS: check `date` (`systemd-timesyncd` keeps it right). A container that cannot
  resolve names after a DNS change needs a restart.
- Disk: `df -h /`; old Docker images: `docker image prune` (ask first).
- Temperature/fans: `sensors`; the Mac should be on its charger, lid may be closed.
- Logs of the installer: `/var/log/macserver-firstboot.log`,
  `/var/log/macserver-setup.log`. Docs: `docs/TROUBLESHOOTING.md` in the project.
