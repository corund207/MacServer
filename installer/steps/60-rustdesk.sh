# shellcheck shell=bash
# Step: rustdesk (optional self-hosted RustDesk server: hbbs + hbbr).
#
# This is the one service that is reachable from the Internet without Tailscale or
# the tunnel: host/nftables.conf admits TCP 21115-21117 and UDP 21116, and the
# owner forwards the same ports on the router. hbbs only brokers the handshake;
# hbbr relays only when two clients cannot connect directly.

step_rustdesk() {
  cat <<'EOF2'
RustDesk lets you control another computer from anywhere. This runs your own
server for it, so the two computers do not depend on RustDesk's public servers.
It needs the router to forward TCP 21115-21117 and UDP 21116 to this Mac (the
firewall here already admits those ports), and a public name or IP for it.
EOF2
  confirm "Set up the RustDesk server now? (You can do it later: sudo macserver rustdesk setup)" n ||
    { ok "skipped"; return 0; }
  rustdesk_setup
}

rustdesk_setup() {
  local host key _
  host=$(ask "Public DNS name (or IP) your RustDesk clients use to reach this server" "$(conf_get RUSTDESK_RELAY)")
  [[ $host =~ ^[A-Za-z0-9]([A-Za-z0-9.-]*[A-Za-z0-9])?$ ]] || die "'$host' is not a DNS name or IP."

  install -d -m 0700 "$RUSTDESK_DIR" "$RUSTDESK_DIR/data"
  install -m 0644 "$SRC/rustdesk/compose.yml" "$RUSTDESK_DIR/compose.yml"
  (umask 077; printf 'RUSTDESK_RELAY=%s\n' "$host" > "$RUSTDESK_DIR/.env")
  conf_set RUSTDESK_RELAY "$host"
  rustdesk_compose up -d
  for _ in $(seq 1 30); do [[ -s $RUSTDESK_DIR/data/id_ed25519.pub ]] && break; sleep 1; done
  key=$(cat "$RUSTDESK_DIR/data/id_ed25519.pub" 2>/dev/null || true)
  [[ -n $key ]] || die "hbbs did not create its key. See: sudo docker logs hbbs"
  ok "RustDesk server running"
  say "  ID server and relay server: $host"
  say "  Key (public): $key"
  say "  Router: forward TCP 21115-21117 and UDP 21116 to this Mac. Back up $RUSTDESK_DIR/data."
}

rustdesk_key() { cat "$RUSTDESK_DIR/data/id_ed25519.pub"; }
