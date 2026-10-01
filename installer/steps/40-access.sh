# shellcheck shell=bash
# Steps: admin, public, finish.

install_tools() {
  install -d -m 0755 /usr/local/lib/macserver
  install -m 0644 "$SRC/installer/lib.sh" /usr/local/lib/macserver/lib.sh
  install -m 0644 "$SRC/installer/steps/"*.sh -t /usr/local/lib/macserver/
  install -m 0755 "$SRC/admin/collect_status.py" /usr/local/lib/macserver/collect_status.py
  install -m 0755 "$SRC/admin/idle.py" /usr/local/lib/macserver/idle
  install -m 0755 "$SRC/admin/tui.py" /usr/local/lib/macserver/dashboard
  install -m 0755 "$SRC/host/macserver-console" /usr/local/lib/macserver/console
  install -m 0755 "$SRC/installer/self-update.sh" /usr/local/lib/macserver/self-update
  install -d -m 0755 /usr/local/share/macserver
  cp -a "$SRC/host" "$SRC/gateway" /usr/local/share/macserver/
  install -m 0755 "$SRC/macserver" /usr/local/sbin/macserver
  # The command lives in sbin, which is not on unprivileged users' PATH, so SSH
  # logins would get "command not found". Link it into /usr/local/bin: read-only
  # commands (status, dashboard, doctor, idle status) then work without sudo,
  # while everything privileged still refuses via need_root.
  ln -sfn /usr/local/sbin/macserver /usr/local/bin/macserver
}

step_admin() {
  install_tools
  id macserver-admin >/dev/null 2>&1 ||
    useradd --system --no-create-home --home-dir /nonexistent --shell /usr/sbin/nologin macserver-admin
  install -d -m 0755 "$ADMIN_DIR"
  install -m 0644 "$SRC/admin/server.py" "$SRC/admin/terminal.py" -t "$ADMIN_DIR/"
  install -d -m 0755 "$ADMIN_DIR/static"
  install -m 0644 "$SRC/admin/static/"* -t "$ADMIN_DIR/static/"
  local unit
  for unit in macserver-status.service macserver-status.timer macserver-admin.service; do
    put_file "$SRC/admin/$unit" "/etc/systemd/system/$unit" 0644
  done
  systemctl daemon-reload
  systemctl enable --now macserver-status.timer >/dev/null
  systemctl start macserver-status.service
  systemctl enable --now macserver-admin.service >/dev/null
  systemctl restart macserver-admin.service

  local name; name=$(conf_get TAILNET_NAME)
  if tailscale serve --bg --https=443 "http://127.0.0.1:$ADMIN_PORT" >/dev/null &&
     tailscale serve --bg --https="$STUDIO_TAILNET_PORT" http://127.0.0.1:8000 >/dev/null; then
    ok "admin page:        https://$name/"
    ok "Supabase Studio:   https://$name:$STUDIO_TAILNET_PORT/  (tailnet only)"
    terminal_setup "$name" || warn "the web terminal was not set up; the rest of the admin page is unaffected"
  else
    warn "tailscale serve failed. Enable MagicDNS and HTTPS Certificates at"
    warn "https://login.tailscale.com/admin/dns, then run: sudo ./install.sh --redo admin"
    return 1
  fi
}

# terminal_setup NAME: the browser terminal. It runs as the owner (like the Claude session),
# listens on 127.0.0.1, and is mounted at /term on the admin page's HTTPS address.
terminal_setup() {
  local name=$1 user home group
  user=$(admin_user) || { warn "no owner account found; skipping the web terminal"; return 0; }
  home=$(getent passwd "$user" | cut -d: -f6)
  group=$(id -gn "$user")
  put_file "$SRC/admin/macserver-terminal.service" /etc/systemd/system/macserver-terminal.service 0644
  install -d -m 0755 /etc/systemd/system/macserver-terminal.service.d
  printf '[Service]\nUser=%s\nGroup=%s\nWorkingDirectory=%s\nEnvironment=HOME=%s\n' \
    "$user" "$group" "$home" "$home" > /etc/systemd/system/macserver-terminal.service.d/60-owner.conf
  systemctl daemon-reload
  systemctl enable --now macserver-terminal.service >/dev/null
  systemctl restart macserver-terminal.service
  if tailscale serve --bg --https=443 --set-path /term "http://127.0.0.1:$TERMINAL_PORT" >/dev/null; then
    ok "web terminal:      https://$name/#terminal  (a shell as $user, tailnet only)"
  else
    warn "could not publish the web terminal; the rest of the admin page is unaffected"
  fi
}

step_claude() {
  local user home dir=/usr/local/lib/claude-code/$CLAUDE_CODE_VERSION tgz
  user=$(admin_user) || { warn "no owner account found; skipping Claude Code"; return 0; }
  home=$(getent passwd "$user" | cut -d: -f6)
  if [[ ! -x $dir/claude ]]; then
    say "Downloading Claude Code $CLAUDE_CODE_VERSION (about 70 MB)..."
    tgz=$(mktemp)
    curl -fsSL --retry 3 -o "$tgz" \
      "https://registry.npmjs.org/@anthropic-ai/claude-code-linux-x64/-/claude-code-linux-x64-$CLAUDE_CODE_VERSION.tgz" ||
      { rm -f "$tgz"; die "could not download Claude Code"; }
    if ! echo "$CLAUDE_CODE_SHA512  $tgz" | sha512sum -c --quiet >/dev/null 2>&1; then
      rm -f "$tgz"; die "the Claude Code download does not match its pinned SHA-512; not installing it"
    fi
    install -d -m 0755 "$dir"
    tar -xzf "$tgz" -C "$dir" --strip-components=1 --no-same-owner package/claude
    rm -f "$tgz"
    chmod 0755 "$dir/claude"
  fi
  install -d -m 0755 /usr/local/bin
  ln -sfn "$dir/claude" /usr/local/bin/claude
  # Updates come with MacServer (a new pinned version), not from the self-updater.
  grep -qx 'DISABLE_AUTOUPDATER=1' /etc/environment 2>/dev/null || echo 'DISABLE_AUTOUPDATER=1' >> /etc/environment

  # The macserver skill for every session of the owner, and a workspace to start in.
  local group; group=$(id -gn "$user")
  install -d -m 0755 -o "$user" -g "$group" "$home/.claude" "$home/.claude/skills" \
    "$home/.claude/skills/macserver" "$home/macserver-workspace"
  install -m 0644 -o "$user" -g "$group" "$SRC/host/claude/SKILL.md" "$home/.claude/skills/macserver/SKILL.md"
  [[ -f $home/macserver-workspace/CLAUDE.md ]] ||
    install -m 0644 -o "$user" -g "$group" "$SRC/host/claude/CLAUDE.md" "$home/macserver-workspace/CLAUDE.md"

  # Remote Control sessions started from the admin page run as the owner, in the workspace.
  install -D -m 0755 "$SRC/host/claude/claude-control" /usr/local/lib/macserver/claude-control
  local unit
  for unit in macserver-claude.service macserver-claude-control.service macserver-claude-control.path; do
    put_file "$SRC/host/claude/$unit" "/etc/systemd/system/$unit" 0644
  done
  install -d -m 0755 /etc/systemd/system/macserver-claude.service.d
  printf '[Service]\nUser=%s\nGroup=%s\nWorkingDirectory=%s\nEnvironment=HOME=%s\n' \
    "$user" "$group" "$home/macserver-workspace" "$home" \
    > /etc/systemd/system/macserver-claude.service.d/60-owner.conf
  systemctl daemon-reload
  systemctl enable --now macserver-claude-control.path >/dev/null
  ok "Claude Code $(/usr/local/bin/claude --version 2>/dev/null | cut -d' ' -f1) installed with the macserver skill"
  say "The admin page's 'Start Claude session' button opens a Remote Control session as $user."
  say "Sign in once as $user: run 'claude' and follow the login link (needs a claude.ai plan)."
}

step_autoupdate() {
  install_tools
  local unit
  for unit in macserver-self-update.service macserver-self-update.timer; do
    put_file "$SRC/host/$unit" "/etc/systemd/system/$unit" 0644
  done
  cat <<'EOF'
MacServer can install its own new versions from GitHub (github.com/corund207/MacServer)
by itself: every 2 minutes it looks for a new commit, and installs it only once
every automatic test has passed for it, including a full install in a virtual machine.
It backs up the database first, re-applies MacServer's settings (screen, DNS, firewall,
admin page), and goes back to the old version if anything is wrong afterwards. It never
changes Supabase's version or your data.
EOF
  if unattended; then        # first boot or a self-update: keep the owner's choice (default on)
    conf_set AUTO_UPDATE "$(conf_get AUTO_UPDATE on)"
  elif confirm "Install new MacServer versions automatically?" y; then conf_set AUTO_UPDATE on
  else conf_set AUTO_UPDATE off; fi
  systemctl daemon-reload
  systemctl enable --now macserver-self-update.timer >/dev/null
  ok "automatic updates $(conf_get AUTO_UPDATE on) (change: sudo macserver autoupdate on|off; now: sudo macserver upgrade)"
}

step_public() {
  cat <<'EOF'
Your apps on the Internet can reach Supabase through a Cloudflare Tunnel. The Mac
connects out to Cloudflare, so no router ports are opened. Only the app API paths
(/auth/v1, /rest/v1, /storage/v1, /realtime/v1, /functions/v1, /graphql/v1) are
passed through. Studio, the database and the admin page are never public.

You need a domain on Cloudflare (free plan is fine). In the Cloudflare dashboard:
  1. Zero Trust > Networks > Tunnels > Create a tunnel > Cloudflared. Name it "macserver".
  2. On the install page choose Docker and copy the token (the long text after --token).
  3. Public Hostnames > Add: e.g. api.example.com, Service type HTTP, URL  caddy:8080
EOF
  confirm "Set up the public API route now? (You can do it later: sudo macserver public setup)" n ||
    { ok "skipped; everything stays private"; return 0; }
  public_setup
}

public_setup() {
  local domain token
  domain=$(ask "Public hostname you added in Cloudflare (e.g. api.example.com)" "$(conf_get PUBLIC_DOMAIN)")
  [[ $domain =~ ^[A-Za-z0-9.-]+\.[A-Za-z]{2,}$ ]] || die "'$domain' is not a hostname."
  token=$(ask_secret "Tunnel token (input hidden)")
  [[ $token =~ ^[A-Za-z0-9._=-]{40,}$ ]] || die "that does not look like a tunnel token."

  install -d -m 0700 "$GATEWAY_DIR"
  install -m 0644 "$SRC/gateway/compose.yml" "$GATEWAY_DIR/compose.yml"
  install -m 0644 "$SRC/gateway/Caddyfile" "$GATEWAY_DIR/Caddyfile"
  (umask 077; printf 'TUNNEL_TOKEN=%s\n' "$token" > "$GATEWAY_DIR/.env")
  conf_set PUBLIC_DOMAIN "$domain"
  supabase_urls
  (cd "$SUPABASE_DIR" && docker compose up -d --wait)
  gateway_compose up -d --wait
  ok "tunnel started"
  public_check "$domain"
}

public_check() {
  local domain=$1 code _
  say "Checking https://$domain (DNS can take a minute)..."
  for _ in 1 2 3 4 5 6; do
    code=$(curl -s -o /dev/null -w '%{http_code}' --max-time 10 "https://$domain/auth/v1/health" || true)
    [[ $code == 200 ]] && break
    sleep 10
  done
  check "public Auth API answers" "public Auth health returned '$code'. Check the hostname in Cloudflare points to caddy:8080." test "$code" = 200
  code=$(curl -s -o /dev/null -w '%{http_code}' --max-time 10 "https://$domain/" || true)
  check "Studio is not reachable publicly (404)" "https://$domain/ returned '$code' (expected 404)." test "$code" = 404
  code=$(curl -s -o /dev/null -w '%{http_code}' --max-time 10 "https://$domain/pg/tables" || true)
  check "database admin API is not reachable publicly (404)" "https://$domain/pg/ returned '$code' (expected 404)." test "$code" = 404
}

step_finish() {
  local name domain env=$SUPABASE_DIR/.env
  name=$(conf_get TAILNET_NAME); domain=$(conf_get PUBLIC_DOMAIN)
  cat <<EOF

${C_B}MacServer is running.${C_0}

  Admin page        https://$name/                      (your tailnet only)
  Supabase Studio   https://$name:$STUDIO_TAILNET_PORT/                 (your tailnet only)
  Studio login      user $(env_get "$env" DASHBOARD_USERNAME), password: sudo macserver keys
  Private API URL   https://$name:$STUDIO_TAILNET_PORT
  Public API URL    ${domain:+https://$domain}${domain:-not enabled (sudo macserver public setup)}
  Shell access      ssh $(logname 2>/dev/null || echo USER)@${name%%.*}          (Tailscale SSH)
  Claude Code       ssh in, then: cd ~/macserver-workspace && claude   (sign in once)

  App keys          sudo macserver keys
  Health            sudo macserver status
  Updates           sudo macserver update

Closing the lid is safe. Leave the charger connected.
EOF
  install -d -m 0700 "$MACSERVER_STATE_DIR"
  touch "$MACSERVER_STATE_DIR/firstboot.done"
}
