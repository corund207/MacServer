# MacServer workspace

This folder is where Claude Code sessions on this MacServer start (including the
ones started from the admin page's "Start Claude session" button).

This machine is a MacServer: a 2019 MacBook Air running Debian 13 + the T2 kernel,
self-hosted Supabase in Docker, reachable only over Tailscale, with an optional
outbound-only Cloudflare Tunnel for the public API. Use the `macserver` skill for
what runs here, how to connect apps and backends, and the rules for changing it.

Put the owner's app code and migrations in subfolders here (or clone their repos).
