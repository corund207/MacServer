#!/usr/bin/env bash
# Unit tests for installer/lib.sh helpers that edit configuration files.
set -euo pipefail
ROOT=$(cd "$(dirname "$0")/.." && pwd)
# shellcheck source=installer/lib.sh
source "$ROOT/installer/lib.sh"
tmp=$(mktemp -d); trap 'rm -rf "$tmp"' EXIT
# shellcheck disable=SC2034  # read by the sourced helpers
MACSERVER_ETC=$tmp/etc MACSERVER_CONF=$tmp/etc/macserver.conf MACSERVER_STATE_DIR=$tmp/state MACSERVER_STATE=$tmp/state/install.state
fail() { echo "FAIL: $*" >&2; exit 1; }

conf_set ADMIN_LOGINS a@example.com
conf_set SITE_URL https://app.example.com
conf_set ADMIN_LOGINS 'a@example.com,b@example.com'
[[ $(conf_get ADMIN_LOGINS) == 'a@example.com,b@example.com' ]] || fail conf_set replace
[[ $(grep -c ADMIN_LOGINS "$MACSERVER_CONF") == 1 ]] || fail conf_set duplicate
[[ $(conf_get MISSING fallback) == fallback ]] || fail conf_get default

printf 'A=1\n# B=comment\nB=2\n' > "$tmp/.env"
env_set "$tmp/.env" B 'x=y&z'
env_set "$tmp/.env" C 3
[[ $(env_get "$tmp/.env" B) == 'x=y&z' ]] || fail env_set special characters
[[ $(env_get "$tmp/.env" C) == 3 ]] || fail env_set append
grep -qx '# B=comment' "$tmp/.env" || fail env_set touched a comment

state_add docker; state_add docker; state_add t2
state_has docker || fail state_has
[[ $(grep -c docker "$MACSERVER_STATE") == 1 ]] || fail state_add duplicate
state_del docker
! state_has docker || fail state_del

echo "put_file" > "$tmp/new"; echo "original" > "$tmp/dest"
put_file "$tmp/new" "$tmp/dest" 0644
[[ $(cat "$tmp/dest") == put_file && $(cat "$tmp/dest.macserver-orig") == original ]] || fail put_file backup
echo "second" > "$tmp/new"; put_file "$tmp/new" "$tmp/dest" 0644
[[ $(cat "$tmp/dest.macserver-orig") == original ]] || fail put_file keeps first original

echo "lib tests passed"
