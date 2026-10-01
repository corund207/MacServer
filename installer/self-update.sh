#!/usr/bin/env bash
# MacServer self-update: installs the newest commit on GitHub `main`, but only once
# every CI check for that exact commit has passed (the tests and the full VM
# install), and only as a fast-forward of what runs now. It backs up the database
# first, re-applies MacServer's own settings (UPDATE_STEPS: screen, fans, DNS, kernel
# network settings, firewall, admin page, Claude Code, this updater), checks health,
# and rolls back on failure. It never changes Supabase's version or data, or Docker;
# `sudo macserver update` handles packages and containers.
#
#   self-update           what the timer runs (every 2 minutes; obeys AUTO_UPDATE)
#   self-update --now     check and install now, even with AUTO_UPDATE=off
#   self-update --check   only report what would happen
#   self-update --force   force apply latest version, bypassing rolled-back cache (use with caution)
#   self-update --verbose show detailed step-by-step progress
set -Eeuo pipefail

REPO=corund207/MacServer
# (Tests point these at a local repository and saved CI answers.)
REPO_URL=${MACSERVER_UPDATE_REPO:-https://github.com/$REPO.git}
API=${MACSERVER_UPDATE_API:-https://api.github.com/repos/$REPO}
SRC_DIR=/opt/macserver-src
# CI jobs that must have passed for a commit (ci.yml and installer-image.yml).
REQUIRED_CHECKS=(checks disk-install image vm-test)
# Changes to these need the full install test; anything else (dashboard, admin page,
# docs, tests) only the quick checks. Health check and rollback protect either way.
INSTALL_PATHS='^(iso/|installer/|host/|install\.sh$|\.github/workflows/)'
# Install steps re-applied after an update, when they were done before.
UPDATE_STEPS=(host firewall admin claude autoupdate)
STATUS_RUN=/run/macserver/self-update.json
STATUS_KEEP=/var/lib/macserver/self-update.json
LOCK=/run/macserver-self-update.lock

# shellcheck source=installer/lib.sh
source /usr/local/lib/macserver/lib.sh

mode=${1:-timer}
verbose=false
force=false
case $mode in
  --verbose) verbose=true; mode=${2:-timer} ;;
  --force) force=true; mode=${2:-timer} ;;
esac
if [[ $mode == --verbose ]]; then verbose=true; mode=${2:-timer}; fi
if [[ $mode == --force ]]; then force=true; mode=${2:-timer}; fi
[[ $EUID -eq 0 ]] || die "run as root"
exec 9>"$LOCK"
flock -n 9 || { say "another update is running"; exit 0; }

current=$(git -C "$SRC_DIR" rev-parse HEAD 2>/dev/null || true)

report() {  # report STATE "message" [latest]
  local json
  json=$(jq -n --arg state "$1" --arg message "$2" --arg current "${current:-}" --arg latest "${3:-}" \
    --arg auto "$(conf_get AUTO_UPDATE on)" --argjson at "$(date +%s)" \
    '{state: $state, message: $message, current: $current, latest: $latest, auto: $auto, checked_at: $at}')
  install -d -m 0755 /run/macserver
  printf '%s\n' "$json" > "$STATUS_RUN.tmp" && chmod 0644 "$STATUS_RUN.tmp" && mv "$STATUS_RUN.tmp" "$STATUS_RUN"
  install -d -m 0700 "$(dirname "$STATUS_KEEP")"
  cp "$STATUS_RUN" "$STATUS_KEEP"
  say "$1: $2"
}

vlog() { [[ $verbose == true ]] && say "$*" || true; }

if [[ $mode == timer && $(conf_get AUTO_UPDATE on) != on && $force != true ]]; then
  report off "automatic updates are off (turn on: sudo macserver autoupdate on)"
  exit 0
fi
if [[ ! -f $MACSERVER_STATE_DIR/firstboot.done ]]; then
  report waiting "setup has not finished; updates start after it"
  exit 0
fi

latest=$(git ls-remote "$REPO_URL" refs/heads/main 2>/dev/null | cut -f1)
[[ $latest =~ ^[0-9a-f]{40}$ ]] || { report error "could not reach GitHub"; exit 0; }
if [[ $latest == "$current" ]]; then
  report up-to-date "running the newest version" "$latest"
  exit 0
fi

bad=$(jq -r 'select(.state == "rolled-back") | .latest' "$STATUS_KEEP" 2>/dev/null || true)
if [[ $mode == timer && $bad == "$latest" && $force != true ]]; then
  report skipped "${latest:0:7} failed its health check before; waiting for a newer version (use --force to retry)" "$latest"
  exit 0
fi

# A local copy of the repository, refreshed with a cheap fetch on every check.
cache=$SRC_DIR.git
if [[ -d $cache ]]; then
  git -C "$cache" fetch --quiet "$REPO_URL" "+refs/heads/main:refs/heads/main" 2>/dev/null ||
    { report error "could not download from GitHub" "$latest"; exit 0; }
else
  git clone --quiet --bare "$REPO_URL" "$cache" 2>/dev/null ||
    { rm -rf "$cache"; report error "could not download from GitHub" "$latest"; exit 0; }
fi
git -C "$cache" cat-file -e "$latest^{commit}" 2>/dev/null ||
  { report error "GitHub did not provide ${latest:0:7}" "$latest"; exit 0; }
# Only fast-forwards: refuse a rewritten history.
if [[ -n $current ]] && ! git -C "$cache" merge-base --is-ancestor "$current" "$latest" 2>/dev/null; then
  report refused "GitHub history was rewritten (${current:0:7} is not in ${latest:0:7}); update by hand" "$latest"
  exit 0
fi

# Which CI jobs must pass: all of them when the change touches how the Mac is
# installed (or when the running version is unknown); otherwise the quick checks.
required=(checks)
if [[ -z $current ]] || git -C "$cache" diff --name-only "$current" "$latest" | grep -qE "$INSTALL_PATHS"; then
  required=("${REQUIRED_CHECKS[@]}")
fi

# Every required CI job for this exact commit must have completed successfully.
runs=$(curl -fsS --max-time 20 -H "Accept: application/vnd.github+json" "$API/commits/$latest/check-runs?per_page=100" 2>/dev/null) ||
  { report error "could not ask GitHub for the CI results" "$latest"; exit 0; }
pending='' failed=''
for check in "${required[@]}"; do
  result=$(jq -r --arg n "$check" '[.check_runs[] | select(.name == $n)] | max_by(.started_at) // {} |
    if .status == null then "missing" elif .status != "completed" then "pending" else .conclusion end' <<<"$runs")
  case $result in
    success) ;;
    missing|pending) pending+=" $check" ;;
    *) failed+=" $check" ;;
  esac
done
if [[ -n $failed ]]; then
  report skipped "the newest commit failed CI ($failed ); staying on this version" "$latest"
  exit 0
fi
if [[ -n $pending ]]; then
  report waiting-ci "a new version is being tested ($pending ); it installs when CI passes" "$latest"
  exit 0
fi
if [[ $mode == --check ]]; then
  report available "version ${latest:0:7} passed CI (${required[*]}) and will install" "$latest"
  exit 0
fi

# The exact commit CI tested, from the local copy.
next=$SRC_DIR.next
rm -rf "$next"
if ! git clone --quiet "$cache" "$next" || ! git -C "$next" checkout --quiet --detach "$latest"; then
  rm -rf "$next"
  report error "could not prepare ${latest:0:7}" "$latest"
  exit 0
fi

report updating "installing ${latest:0:7}" "$latest"
if docker ps --format '{{.Names}}' 2>/dev/null | grep -qx supabase-db; then
  vlog "Backing up database..."
  /usr/local/sbin/macserver backup >/dev/null 2>&1 || warn "database backup failed; continuing"
fi

vlog "Switching source tree to ${latest:0:7}..."
apply() {  # apply DIR: re-run the update steps from that source tree
  local dir=$1 step
  for step in "${UPDATE_STEPS[@]}"; do
    if state_has "$step"; then
      vlog "Re-applying step: $step"
      MACSERVER_UNATTENDED=1 "$dir/install.sh" --redo "$step" >> /var/log/macserver-self-update.log 2>&1 || return 1
    else
      vlog "Skipping step (not previously done): $step"
    fi
  done
}

healthy() {  # only what this Mac has installed
  vlog "Running health checks..."
  if state_has admin; then
    vlog "Checking admin page services..."
    systemctl start macserver-status.service || return 1
    systemctl is-active --quiet macserver-admin.service || return 1
    systemctl is-active --quiet macserver-status.timer || return 1
  fi
  if id macserver-console >/dev/null 2>&1; then
    vlog "Checking console dashboard..."
    runuser -u macserver-console -- /usr/local/lib/macserver/dashboard --once >/dev/null 2>&1 || return 1
  fi
  vlog "Checking macserver CLI..."
  /usr/local/sbin/macserver help >/dev/null 2>&1
}

rm -rf "$SRC_DIR.prev"
[[ -d $SRC_DIR ]] && mv "$SRC_DIR" "$SRC_DIR.prev"
mv "$next" "$SRC_DIR"
{ echo; echo "== $(date -Is) ${current:0:7} -> ${latest:0:7}"; } >> /var/log/macserver-self-update.log
if apply "$SRC_DIR" && healthy; then
  current=$latest
  vlog "Health checks passed, update successful"
  report updated "now running ${latest:0:7} ($(git -C "$SRC_DIR" log -1 --format=%s | cut -c1-60))" "$latest"
  exit 0
fi

# Roll back to what ran before.
failed_sha=$latest
vlog "Health check failed, rolling back..."
rm -rf "$SRC_DIR"
mv "$SRC_DIR.prev" "$SRC_DIR"
apply "$SRC_DIR" || true
before=${current:0:7}
report rolled-back "${failed_sha:0:7} failed its health check; back on ${before:-the previous version} (log: /var/log/macserver-self-update.log)" "$failed_sha"
exit 1
