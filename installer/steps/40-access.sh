# shellcheck shell=bash
# Steps: admin, public, finish.

install_tools() {
  install -d -m 0755 /usr/local/lib/macserver
  install -m 0644 "$SRC/installer/lib.sh" /usr/local/lib/macserver/lib.sh
  install -m 0644 "$SRC/installer/steps/"*.sh -t /usr/local/lib/macserver/
  install -m 0755 "$SRC/admin/collect_status.py" /usr/local/lib/macserver/collect_status.py
  install -d -m 0755 /usr/local/share/macserver
  cp -a "$SRC/host" "$SRC/gateway" /usr/local/share/macserver/
  install -m 0755 "$SRC/macserver" /usr/local/sbin/macserver
}

step_admin() {
  install_tools
  id macserver-admin >/dev/null 2>&1 ||
    useradd --system --no-create-home --home-dir /nonexistent --shell /usr/sbin/nologin macserver-admin
  install -d -m 0755 "$ADMIN_DIR"
  install -m 0644 "$SRC/admin/server.py" "$ADMIN_DIR/server.py"
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
  else
    warn "tailscale serve failed. Enable MagicDNS and HTTPS Certificates at"
    warn "https://login.tailscale.com/admin/dns, then run: sudo ./install.sh --redo admin"
    return 1
  fi
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

  App keys          sudo macserver keys
  Health            sudo macserver status
  Updates           sudo macserver update

Closing the lid is safe. Leave the charger connected.
EOF
  install -d -m 0700 "$MACSERVER_STATE_DIR"
  touch "$MACSERVER_STATE_DIR/firstboot.done"
}
