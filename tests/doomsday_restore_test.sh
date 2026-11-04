#!/usr/bin/env bash
# Tests the doomsday snapshot -> restore path in host/macserver-doomsday.sh with stub docker, systemctl,
# tailscale and friends: the snapshot taken before isolation records every running container, and restore
# starts each of them again. The real Mac's apps, tunnels and RustDesk use `restart: unless-stopped`, so after
# the first real lockdown (2026-11-04) nothing else brought them back. Needs jq; no root, no network.
#
#   bash tests/doomsday_restore_test.sh
# shellcheck disable=SC2016,SC2034  # check() evaluates its single-quoted condition (and $rc) later
set -uo pipefail
ROOT=$(cd "$(dirname "$0")/.." && pwd)
command -v jq >/dev/null || { echo "doomsday restore test skipped: jq is not installed"; exit 0; }
W=$(mktemp -d); trap 'rm -rf "$W"' EXIT
fails=0
check() { if eval "$2"; then echo "  ok $1"; else echo "  FAIL $1"; fails=$((fails + 1)); fi; }

# --- stub commands; docker remembers which containers run in $W/running
mkdir -p "$W/bin" "$W/opt/supabase" "$W/etc" "$W/var"   # /var/lib/macserver exists on an installed Mac
: > "$W/opt/supabase/docker-compose.yml"
cat > "$W/bin/docker" <<'EOF'
#!/usr/bin/env bash
R=$STUB_DIR/running
[[ ${STUB_DOCKER_DOWN:-0} == 1 ]] && { echo "Cannot connect to the Docker daemon" >&2; exit 1; }
case $1 in
  ps) if [[ ${2:-} == -q ]]; then sed 's/^/id-/' "$R"; else cat "$R"; fi ;;
  stop) : > "$R" ;;
  start) shift
    for n in "$@"; do
      [[ $n == gone-* ]] && { echo "Error response from daemon: No such container: $n" >&2; exit 1; }
      grep -qx "$n" "$R" || echo "$n" >> "$R"
      echo "$n" >> "$STUB_DIR/started"
    done ;;
esac
exit 0
EOF
cat > "$W/bin/systemctl" <<'EOF'
#!/usr/bin/env bash
case "$*" in *is-active*|*list-unit-files*) exit 1 ;; esac   # nothing active, no SSH server
exit 0
EOF
cat > "$W/bin/tailscale" <<'EOF'
#!/usr/bin/env bash
[[ $1 == status ]] && echo '{"BackendState":"Running"}'
exit 0
EOF
for c in nft swapon swapoff wall sync cryptsetup umount; do printf '#!/bin/sh\nexit 0\n' > "$W/bin/$c"; done
chmod +x "$W"/bin/*
export PATH="$W/bin:$PATH" STUB_DIR=$W

# --- the script under test, sourced without its last line (main "$@"), pointed at the scratch folder
grep -qx 'main "$@"' "$ROOT/host/macserver-doomsday.sh" || { echo "FAIL: the script no longer ends in main"; exit 1; }
sed '/^main "\$@"$/d' "$ROOT/host/macserver-doomsday.sh" > "$W/doomsday.sh"
cat > "$W/run" <<EOF
#!/usr/bin/env bash
MACSERVER_LIB=$ROOT/installer/lib.sh
source "$W/doomsday.sh"
MACSERVER_CONF=$W/etc/macserver.conf SUPABASE_DIR=$W/opt/supabase GATEWAY_DIR=$W/opt/gateway
RUN=$W/run-state/doomsday.json KEEP=$W/var/doomsday.json SNAP=$W/var/snapshot.json LOG=$W/doomsday.log
NFT_BACKUP=$W/var/no-backup TOTP_FILE=$W/etc/totp GH_TOKEN_FILE=$W/etc/no-token
need_root() { :; }
verify_totp() { :; }
"\$@"
EOF
chmod +x "$W/run"

echo "snapshot before isolation"
printf '%s\n' supabase-db macserver-apps-beltline-1 macserver-gateway-cloudflared-1 macserver-rustdesk-hbbs-1 > "$W/running"
"$W/run" snapshot_services
check "records every running container by name" \
  '[[ $(jq -c .containers "$W/var/snapshot.json") == "[\"supabase-db\",\"macserver-apps-beltline-1\",\"macserver-gateway-cloudflared-1\",\"macserver-rustdesk-hbbs-1\"]" ]]'
check "keeps the service facts" '[[ $(jq -r .tailscale "$W/var/snapshot.json") == up && $(jq -r .ssh "$W/var/snapshot.json") == missing ]]'

echo "restore after isolation"
docker stop x >/dev/null   # isolation stopped everything
jq '.containers += ["gone-app-1"]' "$W/var/snapshot.json" > "$W/s.tmp" && mv "$W/s.tmp" "$W/var/snapshot.json"
printf '{"state":"doomsday"}\n' > "$W/var/doomsday.json"
"$W/run" do_restore --code 123456 > "$W/out" 2>&1; rc=$?
check "restore succeeds" '[[ $rc == 0 ]]'
check "every recorded container runs again" \
  '[[ $(sort "$W/running" | tr "\n" " ") == "macserver-apps-beltline-1 macserver-gateway-cloudflared-1 macserver-rustdesk-hbbs-1 supabase-db " ]]'
check "a container that no longer exists is named, not fatal" 'grep -q "could not start: gone-app-1" "$W/out"'
check "the restored count is reported" 'grep -q "firewall and 4 container(s) restored" "$W/out"'
check "back to normal" '[[ $(jq -r .state "$W/var/doomsday.json") == normal ]]'
check "the log says what happened" 'grep -q "containers started again: 4, failed: 1" "$W/doomsday.log"'

echo "snapshot when docker is down"
STUB_DOCKER_DOWN=1 "$W/run" snapshot_services; rc=$?
check "still writes the snapshot" '[[ $rc == 0 && $(jq -c .containers "$W/var/snapshot.json") == "[]" ]]'

echo "restore from a snapshot taken by the old script (no container list)"
printf '{"ssh":"missing","tailscale":"up","gateway":"off","admin":"on"}\n' > "$W/var/snapshot.json"
printf '{"state":"debug"}\n' > "$W/var/doomsday.json"
: > "$W/started"
"$W/run" do_restore --code 123456 > "$W/out" 2>&1; rc=$?
check "restore still succeeds" '[[ $rc == 0 && $(jq -r .state "$W/var/doomsday.json") == normal ]]'
check "starts nothing it did not record" '[[ ! -s "$W/started" ]]'

if [[ $fails -gt 0 ]]; then echo "doomsday restore test: $fails failure(s)"; exit 1; fi
echo "doomsday restore test: all passed"
