#!/usr/bin/env bash
# Tests installer/self-update.sh without touching the machine or GitHub: in a private
# mount namespace (tmpfs over /opt, /usr/local, /var/lib/macserver, /run/macserver,
# /etc/macserver, /var/log), against a local repository and saved CI answers.
#
#   sudo bash tests/self_update_test.sh
# shellcheck disable=SC2016  # check() evaluates its single-quoted condition later
set -euo pipefail
ROOT=$(cd "$(dirname "$0")/.." && pwd)
[[ $EUID -eq 0 ]] || exec sudo bash "$0" "$@"
if [[ ${1:-} != --inside ]]; then
  exec unshare --mount --propagation private bash "$0" --inside
fi

for d in /opt /usr/local /var/lib/macserver /run/macserver /etc/macserver /var/log; do
  mkdir -p "$d"; mount -t tmpfs tmpfs "$d"
done
W=$(mktemp -d)
fails=0
check() { if eval "$2"; then echo "  ok $1"; else echo "  FAIL $1"; fails=$((fails + 1)); fi; }
state() { jq -r .state /run/macserver/self-update.json; }

# A repository with three commits: v1 -> v2 -> v3, plus an unrelated one.
mkdir -p "$W/origin"
tar -C "$ROOT" --exclude=./.git --exclude=./build --exclude=__pycache__ -cf - . | tar -C "$W/origin" -xf -
git init -q -b main "$W/origin"
g() { git -C "$W/origin" -c user.name=t -c user.email=t@t "$@"; }
g add -A >/dev/null; g commit -qm v1; v1=$(g rev-parse HEAD)
echo 2 > "$W/origin/VERSION"; g add VERSION; g commit -qm v2; v2=$(g rev-parse HEAD)
echo 3 > "$W/origin/VERSION"; g add VERSION; g commit -qm v3; v3=$(g rev-parse HEAD)
git clone -q --bare "$W/origin" "$W/remote.git"
set_main() { git -C "$W/remote.git" update-ref refs/heads/main "$1"; }

# Saved CI answers per commit: all | failed | pending
ci() {  # ci SHA all|failed|pending
  local conclusion=success status=completed
  [[ $2 == failed ]] && conclusion=failure
  [[ $2 == pending ]] && { status=in_progress; conclusion=null; }
  mkdir -p "$W/api/commits/$1"
  jq -n --arg s "$status" --argjson c "$( [[ $conclusion == null ]] && echo null || echo "\"$conclusion\"")" \
    '{check_runs: [ {name: "checks"}, {name: "disk-install"}, {name: "image"}, {name: "vm-test"} ]
      | map(. + {status: $s, conclusion: $c, started_at: "2026-09-28T00:00:00Z"})}' \
    > "$W/api/commits/$1/check-runs"      # curl ignores the ?query for file:// addresses
}
export MACSERVER_UPDATE_REPO=$W/remote.git MACSERVER_UPDATE_API=file://$W/api

# The installed MacServer: lib, CLI and the updater, as install_tools puts them.
install -d /usr/local/lib/macserver /usr/local/sbin
install -m 0644 "$ROOT/installer/lib.sh" /usr/local/lib/macserver/lib.sh
install -m 0644 "$ROOT/installer/steps/"*.sh -t /usr/local/lib/macserver/
install -m 0755 "$ROOT/installer/self-update.sh" /usr/local/lib/macserver/self-update
install -m 0755 "$ROOT/macserver" /usr/local/sbin/macserver
touch /var/lib/macserver/firstboot.done /var/lib/macserver/install.state
echo AUTO_UPDATE=on > /etc/macserver/macserver.conf
git clone -q "$W/remote.git" /opt/macserver-src && git -C /opt/macserver-src checkout -q "$v1"
U=/usr/local/lib/macserver/self-update
head_is() { [[ $(git -C /opt/macserver-src rev-parse HEAD) == "$1" ]]; }

echo "== new commit still in CI"
set_main "$v2"; ci "$v2" pending
$U >/dev/null || true
check "waits for CI" '[[ $(state) == waiting-ci ]] && head_is $v1'

echo "== new commit failed CI"
ci "$v2" failed
$U >/dev/null || true
check "skips it" '[[ $(state) == skipped ]] && head_is $v1'

echo "== new commit passed CI"
ci "$v2" all
$U >/dev/null || true
check "installs it" '[[ $(state) == updated ]] && head_is $v2'
$U >/dev/null || true
check "then reports up to date" '[[ $(state) == up-to-date ]]'

echo "== history rewritten on GitHub"
git -C "$W/origin" checkout -q --orphan other; echo x > "$W/origin/X"; g add X; g commit -qm other; other=$(g rev-parse HEAD)
git -C "$W/remote.git" fetch -q "$W/origin" other:other; set_main "$other"; ci "$other" all
$U >/dev/null || true
check "refuses a non-fast-forward" '[[ $(state) == refused ]] && head_is $v2'

echo "== update breaks the Mac"
set_main "$v3"; ci "$v3" all
mv /usr/local/sbin/macserver /usr/local/sbin/macserver.off     # health check will fail
$U >/dev/null || true
check "rolls back" '[[ $(state) == rolled-back ]] && head_is $v2'
mv /usr/local/sbin/macserver.off /usr/local/sbin/macserver
$U >/dev/null || true
check "does not retry the broken version by itself" '[[ $(state) == skipped ]] && head_is $v2'
$U --now >/dev/null || true
check "installs it when asked (upgrade)" '[[ $(state) == updated ]] && head_is $v3'

echo "== turned off"
echo AUTO_UPDATE=off > /etc/macserver/macserver.conf
set_main "$v3"
$U >/dev/null || true
check "does nothing when off" '[[ $(state) == off ]]'
check "status file readable by the dashboard" '[[ $(stat -c %a /run/macserver/self-update.json) == 644 ]]'

rm -rf "$W"
(( fails == 0 )) || { echo "self-update test: $fails failure(s)"; exit 1; }
echo "self-update test passed"
