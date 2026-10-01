#!/usr/bin/env bash
# macserver doomsday orchestrator (root): monitor, isolate, unlock, restore.
# Detection and TOTP live in admin/doomsday.py (stdlib only); this script does
# the privileged steps: alerting through the GitHub Action BEFORE the network
# is cut, stopping everything non-essential, applying the lockdown firewall,
# and restoring it all afterwards. Every action is idempotent and logged.
#
#   doomsday check                 timer tick: isolate when detection fires
#   doomsday status                what state the machine is in (no root needed)
#   doomsday isolate [manual]      cut the network now (asks first, unless --yes)
#   doomsday unlock --code XXXXXX  TOTP code -> DEBUG mode (still isolated)
#   doomsday restore --code XXXXXX TOTP code -> normal, everything auto-restored
#   doomsday on|off|test|allow|setup-totp|setup-gh
set -Eeuo pipefail

LIB=${MACSERVER_LIB:-/usr/local/lib/macserver/lib.sh}
# shellcheck source=installer/lib.sh
[[ -f $LIB ]] && source "$LIB"

DOOMSDAY_PY=${MACSERVER_DOOMSDAY_PY:-/usr/local/lib/macserver/doomsday.py}
RUN=/run/macserver/doomsday.json
KEEP=/var/lib/macserver/doomsday.json
SNAP=/var/lib/macserver/doomsday-snapshot.json
TOTP_FILE=/etc/macserver/doomsday-totp
GH_TOKEN_FILE=/etc/macserver/doomsday-gh-token
NFT_BACKUP=/var/lib/macserver/nftables.before-doomsday
LOG=/var/log/macserver-doomsday.log
SHARE=${MACSERVER_SHARE:-/usr/local/share/macserver}

log() { printf '%s doomsday: %s\n' "$(date -Is)" "$*" >>"$LOG" 2>/dev/null || true; }

need_root() { [[ $EUID -eq 0 ]] || die "run with sudo: sudo macserver doomsday $*"; }

state_get() {  # state_get KEY [default]
  local v=''
  [[ -f $KEEP ]] && v=$(jq -r --arg k "$1" '.[$k] // empty' "$KEEP" 2>/dev/null)
  printf '%s' "${v:-${2:-}}"
}

state_set() {  # state_set KEY VALUE (VALUE is raw JSON; quote strings with jq --argjson or pass '"x"')
  local key=$1 value=$2 tmp
  install -d -m 0700 "$(dirname "$KEEP")"
  [[ -f $KEEP ]] || printf '{}\n' > "$KEEP"
  chmod 0600 "$KEEP"
  tmp=$(mktemp)
  jq --argjson v "$value" --arg k "$key" '.[$k] = $v' "$KEEP" >"$tmp" && cat "$tmp" >"$KEEP"
  rm -f "$tmp"
  publish_run
}

publish_run() {  # dashboard + collector read the world-readable copy
  install -d -m 0755 /run/macserver
  jq '{mode, state, since, trigger, score, reasons}' "$KEEP" 2>/dev/null >"$RUN.tmp" || printf '{"mode":"on","state":"normal"}\n' >"$RUN.tmp"
  chmod 0644 "$RUN.tmp" && mv "$RUN.tmp" "$RUN"
}

snapshot_services() {
  local ssh_state="missing" ts_state="down" gateway="off" admin="off"
  systemctl is-active --quiet ssh.service 2>/dev/null && ssh_state="active" || ssh_state="inactive"
  systemctl list-unit-files ssh.service >/dev/null 2>&1 || ssh_state="missing"
  tailscale_running 2>/dev/null && ts_state="up" || ts_state="down"
  [[ -f $GATEWAY_DIR/compose.yml ]] && docker ps --format '{{.Names}}' 2>/dev/null | grep -q . && gateway="on" || gateway="off"
  systemctl is-active --quiet macserver-admin.service 2>/dev/null && admin="on" || admin="off"
  jq -n --arg ssh "$ssh_state" --arg ts "$ts_state" --arg gw "$gateway" --arg admin "$admin" \
    '{ssh: $ssh, tailscale: $ts, gateway: $gw, admin: $admin}' >"$SNAP.tmp" && mv "$SNAP.tmp" "$SNAP"
  chmod 0600 "$SNAP"
}

send_alert() {  # send_alert SCORE REASONS_JSON: GitHub Action email BEFORE isolation; never blocks it
  local score=$1 reasons_json=$2 repo token body code
  repo=$(conf_get DOOMSDAY_GH_REPO "corund207/MacServer")
  [[ -f $GH_TOKEN_FILE ]] || { log "alert skipped: no GitHub token (macserver doomsday setup-gh)"; return 0; }
  token=$(cat "$GH_TOKEN_FILE")
  [[ -n $token ]] || { log "alert skipped: empty GitHub token"; return 0; }
  local host ips
  host=$(hostname 2>/dev/null || echo macserver)
  ips=$(hostname -I 2>/dev/null | tr -s ' ' | tr ' ' ',' || echo unknown)
  body=$(jq -n --arg host "$host" --arg ips "$ips" --arg score "$score" \
    --argjson reasons "$reasons_json" --arg trigger "${ALERT_TRIGGER:-auto}" --argjson at "$(date +%s)" \
    '{ref: "main", inputs: {hostname: $host, ip: $ips, trigger: $trigger,
      score: $score, details: ($reasons | join(" | ") | .[0:1200]),
      timestamp: ($at | tostring)}}')
  code=$(curl -s -o /dev/null -w '%{http_code}' --max-time 15 -X POST \
    -H "Accept: application/vnd.github+json" -H "Authorization: Bearer $token" \
    "https://api.github.com/repos/$repo/actions/workflows/doomsday-alert.yml/dispatches" \
    -d "$body" 2>/dev/null || echo "000")
  log "alert POST to $repo -> HTTP $code (score $score)"
  state_set notified "\"$code\""
}

do_check() {
  [[ $(conf_get DOOMSDAY_MODE on) == on ]] || exit 0
  [[ $(state_get state normal) == normal ]] || exit 0
  local out score fire reasons
  # doomsday.py exits 10 when it fires; the JSON is parsed either way.
  out=$(python3 "$DOOMSDAY_PY" check 2>/dev/null || true)
  score=$(jq -r '.score // 0' <<<"$out" 2>/dev/null || echo 0)
  fire=$(jq -r '.fire // false' <<<"$out" 2>/dev/null || echo false)
  reasons=$(jq -c '.reasons // []' <<<"$out" 2>/dev/null || echo '[]')
  if [[ $fire == true ]]; then
    log "detection fired (score $score)"
    ALERT_TRIGGER=auto isolate_now auto "$score" "$reasons"
  fi
}

isolate_now() {  # isolate_now auto|manual SCORE REASONS_JSON
  local how=$1 score=${2:-0} reasons=${3:-[]}
  need_root isolate
  [[ $(state_get state normal) == normal ]] || { say "already in state $(state_get state)"; exit 0; }
  ALERT_TRIGGER=$how send_alert "$score" "$reasons"
  say "Isolating this Mac (doomsday lockdown)..."
  log "isolating ($how, score $score)"
  snapshot_services
  # 1. Public route and containers first (outbound stops here).
  if [[ -f $GATEWAY_DIR/compose.yml ]]; then gateway_compose down >/dev/null 2>&1 || true; fi
  if [[ -f $SUPABASE_DIR/docker-compose.yml ]]; then (cd "$SUPABASE_DIR" && docker compose stop >/dev/null 2>&1) || true; fi
  mapfile -t _stopped < <(docker ps -q 2>/dev/null || true)
  if [[ ${#_stopped[@]} -gt 0 ]]; then docker stop "${_stopped[@]}" >/dev/null 2>&1 || true; fi
  # 2. Tailnet and the admin surfaces (non-essential in lockdown).
  systemctl stop macserver-admin.service macserver-terminal.service >/dev/null 2>&1 || true
  systemctl stop macserver-claude.service macserver-claude-control.path >/dev/null 2>&1 || true
  tailscale down >/dev/null 2>&1 || true
  # 3. Lockdown firewall (persists across reboots; restore puts the old one back).
  if [[ ! -f $NFT_BACKUP && -f /etc/nftables.conf ]]; then cp -p /etc/nftables.conf "$NFT_BACKUP"; fi
  if [[ -f $SHARE/host/nftables-doomsday.conf ]]; then
    nft -c -f "$SHARE/host/nftables-doomsday.conf" && cp -p "$SHARE/host/nftables-doomsday.conf" /etc/nftables.conf && nft -f /etc/nftables.conf
  fi
  # 4. Emergency SSH from the LAN only (needs the TOTP code to go further).
  if systemctl list-unit-files ssh.service >/dev/null 2>&1; then
    systemctl start ssh.service >/dev/null 2>&1 || true
  fi
  # 5. Data at rest: drop swap (keys in RAM), lock extra encrypted volumes, sync.
  swapoff -a 2>/dev/null || true
  local vol
  for vol in $(conf_get DOOMSDAY_CRYPT_CLOSE "" | tr ', ' '  '); do
    cryptsetup close "$vol" 2>/dev/null || true
  done
  sync
  # 6. Custom screen: banner on logins, alert on every terminal, badge for dashboard.
  state_set state '"doomsday"'
  state_set trigger "\"$how\""
  state_set score "$score"
  state_set reasons "$reasons"
  state_set since "$(date +%s)"
  if [[ -f $SHARE/host/doomsday-issue ]]; then cp -p "$SHARE/host/doomsday-issue" /etc/issue; fi
  wall -n "$(cat "$SHARE/host/doomsday-issue" 2>/dev/null || echo 'MACSERVER DOOMSDAY LOCKDOWN: this Mac is isolated. Run: sudo macserver doomsday status')" 2>/dev/null || true
  log "isolated"
  say "Isolated. Alert sent before the cut. Emergency SSH: LAN only. Unlock: sudo macserver doomsday unlock --code XXXXXX"
}

do_status() {
  publish_run 2>/dev/null || true
  local mode state since trigger score
  mode=$(state_get mode "$(conf_get DOOMSDAY_MODE on)")
  state=$(state_get state normal); since=$(state_get since ""); trigger=$(state_get trigger ""); score=$(state_get score "")
  say "doomsday mode: $mode   state: $state"
  [[ -n $since && $since != "" ]] && say "since: $(date -d "@$since" -Is 2>/dev/null || echo "$since")   trigger: ${trigger:--}   score: ${score:-0}"
  if [[ $state != normal ]]; then
    say "reasons:"
    jq -r '.reasons[]? | "  - \(.)"' "$KEEP" 2>/dev/null || true
    say "next: sudo macserver doomsday unlock --code XXXXXX  (DEBUG, still isolated)"
    say "then: sudo macserver doomsday restore --code XXXXXX  (normal, auto-restore)"
  else
    say "no lockdown. Detection: $(conf_get DOOMSDAY_MODE on) (change: sudo macserver doomsday on|off)"
  fi
}

require_code() {  # require_code CODE... -> echoes the 6-digit code or dies
  local code=""
  while [[ $# -gt 0 ]]; do case $1 in --code) code=${2:-}; shift 2 ;; *) shift ;; esac; done
  [[ $code =~ ^[0-9]{6}$ ]] || die "give a 6-digit Authenticator code: sudo macserver doomsday $DOOMSDAY_ACTION --code XXXXXX"
  printf '%s' "$code"
}

verify_totp() {
  [[ -f $TOTP_FILE ]] || die "no Authenticator secret enrolled (sudo macserver doomsday setup-totp)"
  python3 "$DOOMSDAY_PY" totp-verify "$1" >/dev/null 2>&1 || die "invalid code (wait for the next 30 s code and try again)"
}

do_unlock() {
  need_root unlock
  DOOMSDAY_ACTION=unlock
  local code; code=$(require_code "$@")
  [[ $(state_get state normal) == doomsday ]] || die "nothing to unlock (state: $(state_get state normal))"
  verify_totp "$code"
  state_set state '"debug"'
  log "unlocked to DEBUG by TOTP"
  wall -n "MacServer doomsday: DEBUG mode (still isolated). Diagnose, then: sudo macserver doomsday restore --code XXXXXX" 2>/dev/null || true
  ok "DEBUG mode: still isolated, diagnostics allowed. Restore: sudo macserver doomsday restore --code XXXXXX"
}

do_restore() {
  need_root restore
  DOOMSDAY_ACTION=restore
  local code; code=$(require_code "$@")
  local cur; cur=$(state_get state normal)
  [[ $cur == doomsday || $cur == debug ]] || die "nothing to restore (state: $cur)"
  verify_totp "$code"
  say "Restoring everything..."
  log "restoring from $cur"
  # Firewall back first so services can talk again.
  if [[ -f $NFT_BACKUP ]]; then cp -p "$NFT_BACKUP" /etc/nftables.conf; nft -f /etc/nftables.conf || true; fi
  systemctl start docker >/dev/null 2>&1 || true
  if [[ -f $SUPABASE_DIR/docker-compose.yml ]]; then (cd "$SUPABASE_DIR" && docker compose up -d --wait >/dev/null 2>&1) || true; fi
  local gw_was="off" ts_was="down" ssh_was="inactive"
  [[ -f $SNAP ]] && { gw_was=$(jq -r '.gateway // "off"' "$SNAP"); ts_was=$(jq -r '.tailscale // "down"' "$SNAP"); ssh_was=$(jq -r '.ssh // "inactive"' "$SNAP"); }
  if [[ $ts_was == up ]]; then tailscale up --ssh --accept-dns=false >/dev/null 2>&1 || tailscale up >/dev/null 2>&1 || true; fi
  if [[ $gw_was == on && -f $GATEWAY_DIR/compose.yml ]]; then gateway_compose up -d --wait >/dev/null 2>&1 || true; fi
  systemctl restart macserver-admin.service >/dev/null 2>&1 || true
  systemctl start macserver-terminal.service macserver-claude-control.path >/dev/null 2>&1 || true
  if [[ $ssh_was != active ]]; then systemctl disable --now ssh.service ssh.socket >/dev/null 2>&1 || true; fi
  swapon -a 2>/dev/null || true
  [[ -f /etc/nftables.conf.macserver-orig ]] || true
  state_set state '"normal"'
  state_set trigger '""'
  state_set score 0
  state_set reasons '[]'
  state_set since 'null'
  log "restored to normal"
  wall -n "MacServer doomsday: back to NORMAL, everything restored." 2>/dev/null || true
  ok "normal: network, Tailscale, Supabase, admin page and firewall restored"
  say "Extra encrypted volumes ($(conf_get DOOMSDAY_CRYPT_CLOSE "(none)")) stay locked: reopen by hand if you use them."
}

do_setup_totp() {  # do_setup_totp [SECRET]
  need_root setup-totp
  if [[ -n ${1:-} ]]; then
    local norm; norm=$(python3 -c 'import sys; sys.path.insert(0, "/usr/local/lib/macserver"); from doomsday import normalize_secret; print(normalize_secret(sys.argv[1]))' "$1")
    [[ ${#norm} -ge 16 ]] || die "that secret is too short to be a TOTP secret"
    install -d -m 0755 /etc/macserver
    printf '%s\n' "$norm" > "$TOTP_FILE.tmp" && chmod 0600 "$TOTP_FILE.tmp" && mv "$TOTP_FILE.tmp" "$TOTP_FILE"
    ok "Authenticator enrolled. Test it: sudo macserver doomsday unlock --help (or wait for lockdown)"
    return 0
  fi
  say "1. Run this on the Mac to print a new secret + URL:"
  say "     sudo python3 /usr/local/lib/macserver/doomsday.py totp-setup --account macserver"
  say "2. Add it to Google Authenticator (manual entry, then verify the 6-digit code)."
  say "3. Enrol it: sudo macserver doomsday setup-totp <SECRET>"
}

do_setup_gh() {  # do_setup_gh [owner/repo]
  need_root setup-gh
  local repo=${1:-$(conf_get DOOMSDAY_GH_REPO "corund207/MacServer")}
  [[ $repo =~ ^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$ ]] || die "'$repo' is not owner/repo"
  conf_set DOOMSDAY_GH_REPO "$repo"
  say "Create a fine-grained personal access token with Actions: read and write on $repo,"
  say "then paste it (input hidden). It is stored in $GH_TOKEN_FILE (root only, never committed)."
  local token; token=$(ask_secret "GitHub token")
  [[ ${#token} -ge 20 ]] || die "that does not look like a token"
  install -d -m 0755 /etc/macserver
  printf '%s\n' "$token" > "$GH_TOKEN_FILE.tmp" && chmod 0600 "$GH_TOKEN_FILE.tmp" && mv "$GH_TOKEN_FILE.tmp" "$GH_TOKEN_FILE"
  ok "alert target: $repo (workflow doomsday-alert.yml sends the email)"
}

main() {
  local cmd=${1:-status}; shift || true
  case $cmd in
    check) do_check ;;
    status) do_status ;;
    isolate) local yes=0; [[ ${1:-} == --yes ]] && { yes=1; shift; }; [[ ${1:-} == manual ]] && shift
      if [[ $yes == 0 ]]; then say "This cuts the network, stops services and locks the Mac (alert is sent first)."; confirm "Isolate now?" n || exit 0; fi
      isolate_now manual 0 '["manual isolation by the owner"]' ;;
    unlock) do_unlock "$@" ;;
    restore) do_restore "$@" ;;
    on|off) need_root "$cmd"; conf_set DOOMSDAY_MODE "$cmd"; state_set mode "\"$cmd\""; ok "doomsday detection $cmd" ;;
    test) python3 "$DOOMSDAY_PY" check || true ;;
    allow)
      say "trusted processes: claude, tailscaled, sshd, docker, cloudflared, caddy + $(conf_get DOOMSDAY_TRUSTED_PROCS "(none extra)")"
      say "trusted users: root, macserver-admin, macserver-console + $(conf_get DOOMSDAY_TRUSTED_USERS "(none extra)") + ADMIN_USER"
      say "Tailscale SSH and loopback never score; LAN SSH is noted but cannot fire alone." ;;
    setup-totp) do_setup_totp "$@" ;;
    setup-gh) do_setup_gh "$@" ;;
    *) say "Usage: sudo macserver doomsday [status|check|isolate|unlock --code X|restore --code X|on|off|test|allow|setup-totp|setup-gh]"; exit 2 ;;
  esac
}

main "$@"
