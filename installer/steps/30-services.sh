# shellcheck shell=bash
# Steps: tailscale, firewall, docker, supabase.

step_tailscale() {
  if ! command -v tailscale >/dev/null; then
    apt_key tailscale https://pkgs.tailscale.com/stable/debian/trixie.noarmor.gpg "$TAILSCALE_KEY_FPR"
    put_file "$SRC/host/tailscale.sources" /etc/apt/sources.list.d/tailscale.sources 0644
    apt_quiet update
    apt_quiet install tailscale
  fi
  systemctl enable --now tailscaled >/dev/null

  if ! tailscale_running; then
    local host key=$MACSERVER_ETC/tailscale-authkey
    host=$(ask "Name for this server on your tailnet" "$(conf_get HOSTNAME macserver)")
    if [[ -s $key ]]; then
      tailscale up --ssh --hostname="$host" --auth-key="file:$key"
      shred -u "$key" 2>/dev/null || rm -f "$key"
    else
      cat <<'EOF'
Add this Mac to your Tailscale network: scan the QR code with your phone, or open
the link on any computer, and sign in with the account that owns your tailnet.
Tailscale SSH is turned on, so you can then run `ssh USER@macserver` from your
own devices without copying keys.
EOF
      tailscale up --ssh --hostname="$host" --qr
    fi
  else
    tailscale set --ssh
  fi
  local name login
  name=$(tailnet_name)
  [[ -n $name ]] || die "Tailscale is not connected."
  ok "on the tailnet as $name"

  login=$(tailscale status --json | jq -r '.User[(.Self.UserID|tostring)].LoginName // empty')
  local admins; admins=$(ask "Tailscale logins allowed to use the admin page (comma separated)" "$(conf_get ADMIN_LOGINS "$login")")
  [[ -n $admins ]] || die "at least one admin login is required."
  conf_set ADMIN_LOGINS "$admins"
  conf_set TAILNET_NAME "$name"

  if systemctl is-enabled ssh.service >/dev/null 2>&1 || systemctl is-enabled ssh.socket >/dev/null 2>&1; then
    say "OpenSSH is installed. Tailscale SSH replaces it, and the firewall blocks it from the LAN anyway."
    if confirm "Turn OpenSSH off?" y; then
      systemctl disable --now ssh.service ssh.socket >/dev/null 2>&1 || true
      ok "OpenSSH off"
    fi
  fi
  cat <<EOF
In the Tailscale admin console (https://login.tailscale.com/admin/dns), make sure
${C_B}MagicDNS${C_0} and ${C_B}HTTPS Certificates${C_0} are enabled. The admin page needs them.
EOF
}

step_firewall() {
  say "The firewall drops everything from the LAN and Internet except Tailscale."
  say "Nothing on this Mac will be reachable from your Wi-Fi or router, only over Tailscale."
  confirm "Turn on the firewall?" y || { warn "skipped: services are still bound to localhost, but the host is unfiltered"; return 0; }
  nft -c -f "$SRC/host/nftables.conf" || die "firewall rules failed validation"
  put_file "$SRC/host/nftables.conf" /etc/nftables.conf 0755
  systemctl enable nftables >/dev/null
  nft -f /etc/nftables.conf
  ok "firewall on (rules in /etc/nftables.conf; original saved as /etc/nftables.conf.macserver-orig)"
}

step_docker() {
  if ! command -v docker >/dev/null; then
    apt_key docker https://download.docker.com/linux/debian/gpg "$DOCKER_KEY_FPR"
    put_file "$SRC/host/docker.sources" /etc/apt/sources.list.d/docker.sources 0644
    apt_quiet update
    apt_quiet install docker-ce docker-ce-cli containerd.io docker-buildx-plugin docker-compose-plugin
  fi
  put_file "$SRC/host/docker-daemon.json" /etc/docker/daemon.json 0644
  systemctl enable docker >/dev/null
  systemctl restart docker
  docker info >/dev/null || die "Docker did not start"
  ok "Docker $(docker version --format '{{.Server.Version}}'); container ports bind to 127.0.0.1 only"
}

# supabase_urls: point Supabase at the public domain if there is one, else the tailnet.
supabase_urls() {
  local domain public site env=$SUPABASE_DIR/.env
  domain=$(conf_get PUBLIC_DOMAIN)
  if [[ -n $domain ]]; then public="https://$domain"
  else public="https://$(conf_get TAILNET_NAME):$STUDIO_TAILNET_PORT"; fi
  site=$(conf_get SITE_URL "$public")
  env_set "$env" SUPABASE_PUBLIC_URL "$public"
  env_set "$env" API_EXTERNAL_URL "$public/auth/v1"
  env_set "$env" SITE_URL "$site"
}

step_supabase() {
  local env=$SUPABASE_DIR/.env
  if [[ ! -f $env ]]; then
    say "Downloading Supabase $SUPABASE_REF (verified against commit ${SUPABASE_COMMIT:0:12})..."
    local tmp; tmp=$(mktemp -d)
    git clone --quiet --filter=blob:none --no-checkout --depth=1 --branch "$SUPABASE_REF" \
      https://github.com/supabase/supabase.git "$tmp/src"
    [[ $(git -C "$tmp/src" rev-parse HEAD) == "$SUPABASE_COMMIT" ]] ||
      { rm -rf "$tmp"; die "Supabase $SUPABASE_REF does not match the pinned commit. Refusing to install."; }
    git -C "$tmp/src" sparse-checkout set docker
    git -C "$tmp/src" checkout --quiet
    install -d -m 0755 "$MACSERVER_OPT"
    install -d -m 0700 "$SUPABASE_DIR"
    cp -a "$tmp/src/docker/." "$SUPABASE_DIR/"
    rm -rf "$tmp"
    printf '# Managed by MacServer\nref=%s\n' "$SUPABASE_REF" > "$SUPABASE_DIR/.supabase-version"
    install -m 0600 "$SUPABASE_DIR/.env.example" "$env"
    say "Generating passwords and API keys (kept in $env, readable by root only)..."
    (cd "$SUPABASE_DIR" && sh utils/generate-keys.sh --update-env >/dev/null && sh utils/add-new-auth-keys.sh --update-env >/dev/null)
    rm -f "$env.old" "$env.bak"
    chmod 0600 "$env"
  fi

  local site signup
  site=$(ask "Web address of your app (used for sign-in email links)" "$(conf_get SITE_URL "http://localhost:3000")")
  conf_set SITE_URL "$site"
  if confirm "Let anyone sign up for an account in your apps? (No = you create users yourself)" n; then signup=false; else signup=true; fi
  env_set "$env" DISABLE_SIGNUP "$signup"
  env_set "$env" ENABLE_PHONE_SIGNUP false
  env_set "$env" ENABLE_ANONYMOUS_USERS false
  supabase_urls

  say "Starting Supabase (the first start downloads about 3 GB of images)..."
  (cd "$SUPABASE_DIR" && sh run.sh start)
  ok "Supabase is running (API and Studio on 127.0.0.1:8000 only)"
}
