# shellcheck shell=bash
# Moving an installed Supabase to the version this MacServer pins (SUPABASE_REF).
# Not an install step: `macserver supabase-upgrade` uses it.
#
# step_supabase installs Supabase once and never touches an existing install, so nothing else
# changes the version. This does it without disturbing what the owner has:
#
#   - It compares the two upstream tags (the version recorded in .supabase-version and the pinned one)
#     and only touches the files that differ between them. Data (volumes/db/data, volumes/storage),
#     .env (the secrets and settings) and MacServer's own docker-compose.macserver.yml are never
#     in that list.
#   - It stops, changing nothing, if the tags differ in volumes/ or .env.example (settings or
#     database setup changed: a person has to read that), or if a file it would replace is not the
#     stock one from the old tag (the owner edited it).
#   - It backs up first (cmd_backup) and refuses to go on without one.
#   - It writes the new files, pulls the images, restarts and probes the API; if any step fails it puts
#     the old files back and restarts the old version. The images of the old version stay on disk. The
#     services run their own forward-only database migrations on start; the backup is the way back if a
#     migration ever has to be undone.

SUPABASE_REPO=${MACSERVER_SUPABASE_REPO:-https://github.com/supabase/supabase.git}
SUPABASE_PREVIOUS_PIN=self-hosted/v0.8.2   # what installs before the version file existed were made from

supabase_recorded_ref() {
  local ref
  ref=$(sed -n 's/^ref=//p' "$SUPABASE_DIR/.supabase-version" 2>/dev/null | head -n 1)
  echo "${ref:-$SUPABASE_PREVIOUS_PIN}"
}

# Is the API answering through its gateway? (The compose --wait already checked each container.)
supabase_healthy() {
  local key
  key=$(env_get "$SUPABASE_DIR/.env" SUPABASE_PUBLISHABLE_KEY)
  for _ in 1 2 3 4 5 6; do
    curl -fsS --max-time 10 -H "apikey: $key" "http://127.0.0.1:8000/auth/v1/health" >/dev/null 2>&1 && return 0
    sleep 10
  done
  return 1
}

# supabase_upgrade [--check] [--yes]
supabase_upgrade() {
  local check=false yes=false arg
  for arg in "$@"; do
    case $arg in
      --check) check=true ;;
      --yes) yes=true ;;
      *) die "unknown option '$arg' (use --check or --yes)" ;;
    esac
  done
  [[ -f $SUPABASE_DIR/.env && -f $SUPABASE_DIR/docker-compose.yml ]] || die "Supabase is not installed here"
  local from to
  # shellcheck disable=SC2153  # SUPABASE_REF is the pin in installer/lib.sh
  to=$SUPABASE_REF
  from=$(supabase_recorded_ref)
  if [[ $from == "$to" ]]; then ok "Supabase is already on $to"; return 0; fi

  local tmp; tmp=$(mktemp -d)
  say "Comparing Supabase $from with $to ..."
  git -C "$tmp" init -q src
  git -C "$tmp/src" remote add origin "$SUPABASE_REPO"
  git -C "$tmp/src" fetch -q --depth=1 --filter=blob:none origin \
    "+refs/tags/$from:refs/tags/$from" "+refs/tags/$to:refs/tags/$to" ||
    { rm -rf "$tmp"; die "could not download $from and $to from $SUPABASE_REPO"; }
  local from_commit to_commit
  from_commit=$(git -C "$tmp/src" rev-parse "$from^{commit}") || { rm -rf "$tmp"; die "the Supabase tag $from is missing"; }
  to_commit=$(git -C "$tmp/src" rev-parse "$to^{commit}") || { rm -rf "$tmp"; die "the Supabase tag $to is missing"; }
  [[ $to_commit == "$SUPABASE_COMMIT" ]] ||
    { rm -rf "$tmp"; die "Supabase $to does not match the pinned commit. Refusing to upgrade."; }

  # Everything that differs, and whether it is safe to apply on its own.
  local status path blocked=() conflicts=() changes=()
  while IFS=$'\t' read -r status path; do
    path=${path#docker/}
    [[ -n $path ]] || continue
    case $path in
      volumes/*|.env.example|.env)
        blocked+=("$path"); continue ;;
    esac
    changes+=("$status $path")
    if [[ $status == A ]]; then
      if [[ -e $SUPABASE_DIR/$path ]] && ! git -C "$tmp/src" show "$to_commit:docker/$path" | cmp -s - "$SUPABASE_DIR/$path"; then
        conflicts+=("$path (exists here and differs)")
      fi
    elif ! git -C "$tmp/src" show "$from_commit:docker/$path" | cmp -s - "$SUPABASE_DIR/$path" 2>/dev/null; then
      conflicts+=("$path (not the stock $from file: it was edited here, or is missing)")
    fi
  done < <(git -C "$tmp/src" diff --no-renames --name-status "$from_commit" "$to_commit" -- docker)

  if [[ ${#blocked[@]} -gt 0 ]]; then
    rm -rf "$tmp"
    warn "$from -> $to changes files that hold settings or database setup:"
    printf '       %s\n' "${blocked[@]}" >&2
    die "not upgraded: read Supabase's changes first (nothing was changed)"
  fi
  if [[ ${#conflicts[@]} -gt 0 ]]; then
    rm -rf "$tmp"
    warn "these files would be replaced, but they are not the stock ones:"
    printf '       %s\n' "${conflicts[@]}" >&2
    die "not upgraded: merge by hand (nothing was changed)"
  fi

  say "Files that change (${#changes[@]}):"
  printf '       %s\n' "${changes[@]}"
  if printf '%s\n' "${changes[@]}" | grep -q ' docker-compose.yml$'; then
    say "Image changes:"
    diff <(git -C "$tmp/src" show "$from_commit:docker/docker-compose.yml" | grep -E '^\s+image:' | sed 's/^ *image: //') \
         <(git -C "$tmp/src" show "$to_commit:docker/docker-compose.yml" | grep -E '^\s+image:' | sed 's/^ *image: //') |
      grep -E '^[<>]' | sed 's/^</       was /; s/^>/       now /'
  fi
  if $check; then rm -rf "$tmp"; ok "dry run: nothing was changed"; return 0; fi

  local free_gb; free_gb=$(df --output=avail -BG "$SUPABASE_DIR" | tail -n 1 | tr -dc '0-9')
  [[ ${free_gb:-0} -ge ${SUPABASE_UPGRADE_MIN_GB:-6} ]] ||
    { rm -rf "$tmp"; die "only ${free_gb:-0} GB free; the new images need about 6 GB (nothing was changed)"; }
  say "Backing up first ..."
  cmd_backup || { rm -rf "$tmp"; die "the backup failed, so Supabase was not upgraded (nothing was changed)"; }
  if ! $yes; then
    confirm "Upgrade Supabase from $from to $to now? Its services restart for a minute or two." n ||
      { rm -rf "$tmp"; say "not upgraded"; return 0; }
  fi

  # Keep what is replaced, so a failure can put it back.
  local keep item status2
  keep=$SUPABASE_DIR/.upgrade-backup-$(date +%Y%m%d-%H%M%S)
  install -d -m 0700 "$keep"
  cp -a "$SUPABASE_DIR/.supabase-version" "$keep/" 2>/dev/null || true
  for item in "${changes[@]}"; do
    status2=${item%% *}; path=${item#* }
    if [[ $status2 != A && -e $SUPABASE_DIR/$path ]]; then
      install -d -m 0700 "$keep/$(dirname "$path")"
      cp -a "$SUPABASE_DIR/$path" "$keep/$path"
    fi
  done
  supabase_upgrade_restore() {  # put the old files back and start the old version
    warn "going back to $from"
    local entry st p
    for entry in "${changes[@]}"; do
      st=${entry%% *}; p=${entry#* }
      if [[ $st == A ]]; then
        rm -f "$SUPABASE_DIR/$p"
      elif [[ -e $keep/$p ]]; then
        install -d -m 0755 "$(dirname "$SUPABASE_DIR/$p")"
        cp -a "$keep/$p" "$SUPABASE_DIR/$p"
      fi
    done
    [[ -e $keep/.supabase-version ]] && cp -a "$keep/.supabase-version" "$SUPABASE_DIR/.supabase-version"
    supabase_compose up -d --wait >/dev/null 2>&1 || warn "the old version did not start cleanly either: sudo macserver logs"
  }
  for item in "${changes[@]}"; do
    status2=${item%% *}; path=${item#* }
    if [[ $status2 == D ]]; then
      rm -f "$SUPABASE_DIR/$path"
    else
      install -d -m 0755 "$(dirname "$SUPABASE_DIR/$path")"
      git -C "$tmp/src" show "$to_commit:docker/$path" > "$SUPABASE_DIR/$path"
      if [[ $(git -C "$tmp/src" ls-tree "$to_commit" "docker/$path" | cut -d' ' -f1) == 100755 ]]; then
        chmod 0755 "$SUPABASE_DIR/$path"
      else
        chmod 0644 "$SUPABASE_DIR/$path"
      fi
    fi
  done
  printf '# Managed by MacServer\nref=%s\n' "$to" > "$SUPABASE_DIR/.supabase-version"
  rm -rf "$tmp"

  say "Downloading the new images (a few GB) ..."
  if ! supabase_compose pull --quiet; then
    supabase_upgrade_restore
    die "could not download the new images; Supabase is unchanged ($from)"
  fi
  say "Restarting Supabase on $to ..."
  if ! supabase_compose up -d --wait; then
    supabase_upgrade_restore
    die "Supabase did not start healthy on $to; back on $from (the backup is on the USB drive)"
  fi
  if ! supabase_healthy; then
    supabase_upgrade_restore
    die "the API did not answer on $to; back on $from (the backup is on the USB drive)"
  fi
  ok "Supabase is now on $to (the replaced files are in $keep)"
}
