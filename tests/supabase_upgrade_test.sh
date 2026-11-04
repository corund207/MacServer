#!/usr/bin/env bash
# Tests `macserver supabase-upgrade` (installer/steps/66-supabase-upgrade.sh) against a fake upstream
# repository with two tags and a fake installed Supabase (secrets, data, MacServer's own override file),
# with a stub `docker`. Needs git; no root, no network.
#
#   bash tests/supabase_upgrade_test.sh
# shellcheck disable=SC2016,SC2317,SC2119,SC2120,SC2034  # check() evaluates its single-quoted condition later; fresh's argument is optional
set -uo pipefail
ROOT=$(cd "$(dirname "$0")/.." && pwd)
command -v git >/dev/null || { echo "supabase upgrade test skipped: git is not installed"; exit 0; }
W=$(mktemp -d); trap 'rm -rf "$W"' EXIT
fails=0
check() { if eval "$2"; then echo "  ok $1"; else echo "  FAIL $1"; fails=$((fails + 1)); fi; }

# --- a fake upstream: v0.8.2, v0.8.3 (images bumped, a file added, one made executable) and a "risky" tag
g() { git -C "$W/work" -c user.name=t -c user.email=t@t "$@"; }
git init -q -b main "$W/work"
mkdir -p "$W/work/docker/volumes/api" "$W/work/docker/volumes/db"
printf 'services:\n  auth:\n    image: supabase/gotrue:v1\n  rest:\n    image: postgrest/postgrest:v1\n' > "$W/work/docker/docker-compose.yml"
printf 'POSTGRES_PASSWORD=change-me\n' > "$W/work/docker/.env.example"
printf '#!/bin/sh\necho run\n' > "$W/work/docker/run.sh"; chmod +x "$W/work/docker/run.sh"
printf '#!/bin/sh\necho reset v1\n' > "$W/work/docker/reset.sh"
printf '# 1\n' > "$W/work/docker/CHANGELOG.md"
printf 'api: v1\n' > "$W/work/docker/volumes/api/config.yml"
g add -A >/dev/null; g commit -qm v082; g tag -a self-hosted/v0.8.2 -m v082
printf 'services:\n  auth:\n    image: supabase/gotrue:v2\n    command: ["--new-flag"]\n  rest:\n    image: postgrest/postgrest:v1\n' > "$W/work/docker/docker-compose.yml"
printf '#!/bin/sh\necho reset v2\n' > "$W/work/docker/reset.sh"; chmod +x "$W/work/docker/reset.sh"
printf '# 2\n' > "$W/work/docker/CHANGELOG.md"
printf 'services: {}\n' > "$W/work/docker/docker-compose.extra.yml"
g add -A >/dev/null; g update-index --chmod=+x docker/reset.sh; g commit -qm v083; g tag -a self-hosted/v0.8.3 -m v083
printf 'POSTGRES_PASSWORD=change-me\nNEW_SETTING=1\n' > "$W/work/docker/.env.example"
printf 'api: v2\n' > "$W/work/docker/volumes/api/config.yml"
g add -A >/dev/null; g commit -qm risky; g tag -a self-hosted/v0.9.0 -m risky
git clone -q --bare "$W/work" "$W/upstream.git"
git -C "$W/upstream.git" config uploadpack.allowFilter true
git -C "$W/upstream.git" config uploadpack.allowAnySHA1InWant true
export MACSERVER_SUPABASE_REPO=file://$W/upstream.git
commit_of() { git -C "$W/upstream.git" rev-parse "$1^{commit}"; }

# --- the code under test, pointed at a scratch Mac
# shellcheck source=installer/lib.sh
source "$ROOT/installer/lib.sh"
SUPABASE_DIR=$W/mac/supabase
SUPABASE_REF=self-hosted/v0.8.3 SUPABASE_COMMIT=$(commit_of self-hosted/v0.8.3)
# shellcheck source=installer/steps/66-supabase-upgrade.sh
source "$ROOT/installer/steps/66-supabase-upgrade.sh"
export SUPABASE_UPGRADE_MIN_GB=0

# stubs: docker records what it was asked and fails on request; the backup and health probe are switches
mkdir -p "$W/bin"
cat > "$W/bin/docker" <<'EOF'
#!/bin/sh
echo "$*" >> "$DOCKER_LOG"
case "$*" in
  *pull*) [ -n "$FAKE_PULL_FAIL" ] && exit 1 ;;
  *"up -d"*) n=$(grep -c "up -d" "$DOCKER_LOG"); [ -n "$FAKE_UP_FAIL" ] && [ "$n" = 1 ] && exit 1 ;;
esac
exit 0
EOF
chmod +x "$W/bin/docker"; export PATH="$W/bin:$PATH" DOCKER_LOG=$W/docker.log
cmd_backup() { echo called >> "$W/backup.log"; [[ -z ${BACKUP_FAIL:-} ]]; }
supabase_healthy() { [[ -z ${HEALTH_FAIL:-} ]]; }
export MACSERVER_UNATTENDED=1

# an installed Supabase at v0.8.2 with the owner's things around it
fresh() {  # fresh [recorded ref | none]
  rm -rf "$W/mac" "$DOCKER_LOG" "$W/backup.log"; mkdir -p "$W/mac"
  git -C "$W/upstream.git" archive --prefix=supabase/ self-hosted/v0.8.2:docker | tar -x -C "$W/mac"
  printf 'SUPABASE_PUBLISHABLE_KEY=pk\nPOSTGRES_PASSWORD=secret-that-must-survive\nCOMPOSE_FILE=docker-compose.yml:docker-compose.macserver.yml\n' > "$SUPABASE_DIR/.env"
  printf '# Written by MacServer\nservices: {}\n' > "$SUPABASE_DIR/docker-compose.macserver.yml"
  mkdir -p "$SUPABASE_DIR/volumes/db/data" "$SUPABASE_DIR/volumes/storage"
  echo 17 > "$SUPABASE_DIR/volumes/db/data/PG_VERSION"; echo photo > "$SUPABASE_DIR/volumes/storage/obj"
  [[ ${1:-} == none ]] || printf '# Managed by MacServer\nref=%s\n' "${1:-self-hosted/v0.8.2}" > "$SUPABASE_DIR/.supabase-version"
  unset BACKUP_FAIL HEALTH_FAIL FAKE_PULL_FAIL FAKE_UP_FAIL
  : > "$DOCKER_LOG"
}
untouched() {  # the owner's things are exactly as they were
  [[ $(grep -c secret-that-must-survive "$SUPABASE_DIR/.env") == 1 && $(cat "$SUPABASE_DIR/volumes/db/data/PG_VERSION") == 17 &&
     $(cat "$SUPABASE_DIR/volumes/storage/obj") == photo && $(grep -c "Written by MacServer" "$SUPABASE_DIR/docker-compose.macserver.yml") == 1 ]]
}
is_old() { grep -q "gotrue:v1" "$SUPABASE_DIR/docker-compose.yml" && [[ $(sed -n 's/^ref=//p' "$SUPABASE_DIR/.supabase-version") == self-hosted/v0.8.2 ]]; }
is_new() { grep -q "gotrue:v2" "$SUPABASE_DIR/docker-compose.yml" && [[ $(sed -n 's/^ref=//p' "$SUPABASE_DIR/.supabase-version") == self-hosted/v0.8.3 ]]; }
run() { local out; out=$( ( supabase_upgrade "$@" ) 2>&1 ); RC=$?; OUT=$out; }

echo "== a dry run changes nothing"
fresh; run --check
check "succeeds and shows the plan" '[[ $RC == 0 && $OUT == *"docker-compose.yml"* && $OUT == *"now supabase/gotrue:v2"* ]]'
check "nothing changed, no backup, no docker" 'is_old && untouched && [[ ! -s $DOCKER_LOG && ! -e $W/backup.log ]]'

echo "== the upgrade"
fresh; run --yes
check "succeeds" '[[ $RC == 0 ]]'
check "the new files are in place, the new one added" 'is_new && [[ -f $SUPABASE_DIR/docker-compose.extra.yml && $(cat $SUPABASE_DIR/CHANGELOG.md) == "# 2" ]]'
check "executable files stay executable" '[[ -x $SUPABASE_DIR/reset.sh && -x $SUPABASE_DIR/run.sh && ! -x $SUPABASE_DIR/CHANGELOG.md ]]'
check "secrets, data and MacServer's own override are untouched" 'untouched'
check "it backed up first, once" '[[ $(wc -l < $W/backup.log) == 1 ]]'
check "it pulled, then restarted and waited" 'grep -q "compose pull" "$DOCKER_LOG" && grep -q "compose up -d --wait" "$DOCKER_LOG"'
check "the replaced files were kept" 'grep -rq "gotrue:v1" "$SUPABASE_DIR"/.upgrade-backup-*/docker-compose.yml'

echo "== already up to date"
run --yes
check "nothing to do" '[[ $RC == 0 && $OUT == *"already on"* ]]'

echo "== no version file (installs from before it existed)"
fresh none; run --yes
check "is treated as the previous pin and upgraded" '[[ $RC == 0 ]] && is_new && untouched'

echo "== a file the owner edited"
fresh; echo "# my edit" >> "$SUPABASE_DIR/docker-compose.yml"; run --yes
check "refuses and names the file" '[[ $RC != 0 && $OUT == *"docker-compose.yml"* && $OUT == *"not the stock"* ]]'
check "nothing changed, no backup, no docker" 'grep -q "my edit" "$SUPABASE_DIR/docker-compose.yml" && untouched && [[ ! -s $DOCKER_LOG && ! -e $W/backup.log ]]'

echo "== upstream changed settings or database setup"
fresh; SUPABASE_REF=self-hosted/v0.9.0 SUPABASE_COMMIT=$(commit_of self-hosted/v0.9.0) run --yes
check "refuses and says what to read" '[[ $RC != 0 && $OUT == *".env.example"* && $OUT == *"volumes/api/config.yml"* && $OUT == *"read Supabase"* ]]'
check "nothing changed" 'is_old && untouched && [[ ! -s $DOCKER_LOG ]]'

echo "== the pinned commit does not match the tag"
fresh; SUPABASE_COMMIT=0000000000000000000000000000000000000000 run --yes
check "refuses" '[[ $RC != 0 && $OUT == *"pinned commit"* ]] && is_old && [[ ! -s $DOCKER_LOG ]]'

echo "== the backup fails"
fresh; BACKUP_FAIL=1 run --yes
check "it does not upgrade" '[[ $RC != 0 && $OUT == *"backup failed"* ]] && is_old && untouched && [[ ! -s $DOCKER_LOG ]]'

echo "== the images cannot be downloaded"
fresh; FAKE_PULL_FAIL=1 run --yes
check "the old files are put back" '[[ $RC != 0 ]] && is_old && untouched && [[ ! -e $SUPABASE_DIR/docker-compose.extra.yml ]]'

echo "== the new version will not start"
fresh; FAKE_UP_FAIL=1 run --yes
check "the old version is put back and started again" '[[ $RC != 0 && $OUT == *"going back"* ]] && is_old && untouched && [[ $(grep -c "up -d" "$DOCKER_LOG") == 2 ]]'

echo "== the API does not answer"
fresh; HEALTH_FAIL=1 run --yes
check "the old version is put back" '[[ $RC != 0 && $OUT == *"did not answer"* ]] && is_old && untouched && [[ ! -e $SUPABASE_DIR/docker-compose.extra.yml ]]'
check "and its executable bits too" '[[ -x $SUPABASE_DIR/run.sh ]]'

echo "== bad use"
fresh; run --bogus
check "unknown options are refused" '[[ $RC != 0 && $OUT == *"unknown option"* ]] && is_old'
rm -rf "$W/mac"; run --check
check "no Supabase installed is a clear error" '[[ $RC != 0 && $OUT == *"not installed"* ]]'

(( fails == 0 )) || { echo "supabase upgrade test: $fails failure(s)"; exit 1; }
echo "supabase upgrade test passed"
