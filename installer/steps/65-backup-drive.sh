# shellcheck shell=bash
# Backups on a USB drive. Not an install step: `macserver backup` and `macserver backup-drive`
# use these functions.
#
# `macserver backup-drive setup` erases a USB disk (only after ERASE is typed), encrypts it
# with LUKS and records its UUID as BACKUP_DRIVE_UUID. The key sits in /etc/macserver on the
# Mac's own encrypted disk, so the drive opens by itself on this Mac and nowhere else; a
# recovery passphrase, shown once, is the only other way in. The drive is opened only while a
# backup runs and is closed again afterwards, so it is never mounted when the Mac is idle.
#
# Which disk may be erased is decided by admin/backupdrive.py (tested): a whole USB disk,
# big enough, with nothing mounted. This file only does the work.

BACKUP_MAP=macserver-backup                 # device-mapper name while the drive is open
BACKUP_MNT=/mnt/macserver-backup
BACKUP_KEY=$MACSERVER_ETC/backup-drive.key
BACKUP_STATE=$MACSERVER_STATE_DIR/backup.json
BACKUP_LOCK=/run/macserver-backup.lock
BACKUPDRIVE_PY=/usr/local/lib/macserver/backupdrive.py
# How many copies to keep on the drive. Storage copies are whole folders, so fewer.
BACKUP_KEEP_DB=30
BACKUP_KEEP_STORAGE=7
BACKUP_KEEP_CONFIG=30
BACKUP_LUKS_OPTS=(--type luks2 --pbkdf argon2id --pbkdf-memory 262144)   # 256 MiB: this Mac has 4 GB

backup_drive_uuid() { conf_get BACKUP_DRIVE_UUID; }

# The disk node of the configured drive's LUKS partition, when it is plugged in.
backup_drive_dev() {
  local uuid; uuid=$(backup_drive_uuid)
  [[ -n $uuid && -e /dev/disk/by-uuid/$uuid ]] || return 1
  readlink -f "/dev/disk/by-uuid/$uuid"
}

backup_drive_open() {
  local dev
  dev=$(backup_drive_dev) || return 1
  [[ -r $BACKUP_KEY ]] || return 1
  if [[ ! -e /dev/mapper/$BACKUP_MAP ]]; then
    cryptsetup open --key-file "$BACKUP_KEY" "$dev" "$BACKUP_MAP" || return 1
  fi
  install -d -m 0700 "$BACKUP_MNT"
  if ! mountpoint -q "$BACKUP_MNT"; then
    mount -o noatime,nodev,nosuid,noexec "/dev/mapper/$BACKUP_MAP" "$BACKUP_MNT" || return 1
  fi
}

backup_drive_close() {
  if mountpoint -q "$BACKUP_MNT"; then sync; umount "$BACKUP_MNT" || return 1; fi
  if [[ -e /dev/mapper/$BACKUP_MAP ]]; then cryptsetup close "$BACKUP_MAP" || return 1; fi
  return 0
}

# --- what the dashboards read ------------------------------------------------------

backup_record_ok() {  # backup_record_ok NEWEST_DUMP
  local count free
  count=$(find "$BACKUP_MNT/database" -maxdepth 1 -name 'supabase-*.sql.gz' -type f | wc -l)
  free=$(df --output=avail -B1 "$BACKUP_MNT" | tail -n 1 | tr -d ' ')
  install -d -m 0700 "$MACSERVER_STATE_DIR"
  jq -n --argjson at "$(date +%s)" --argjson count "$count" --argjson size "$(stat -c %s "$1")" \
    --argjson free "${free:-0}" \
    '{ok_at: $at, tried_at: $at, count: $count, newest_size: $size, free: $free, error: ""}' \
    > "$BACKUP_STATE.tmp" && mv -f "$BACKUP_STATE.tmp" "$BACKUP_STATE"
}

backup_record_failure() {  # backup_record_failure "why": keeps the last good numbers
  local old='{}'
  [[ -s $BACKUP_STATE ]] && old=$(cat "$BACKUP_STATE")
  install -d -m 0700 "$MACSERVER_STATE_DIR"
  jq --arg e "$1" --argjson t "$(date +%s)" '. + {error: $e, tried_at: $t}' <<<"$old" \
    > "$BACKUP_STATE.tmp" && mv -f "$BACKUP_STATE.tmp" "$BACKUP_STATE"
}

# --- the backup itself -------------------------------------------------------------

backup_prune() {  # backup_prune DIR PATTERN KEEP: delete all but the newest KEEP files
  find "$1" -maxdepth 1 -name "$2" -type f | sort | head -n "-$3" | xargs -r rm -f --
}

# Writes into the open drive. Every command is checked by hand: this runs inside `||`, where
# `set -e` does nothing.
backup_write() {
  local stamp dump
  stamp=$(date +%Y%m%d-%H%M%S)
  install -d -m 0700 "$BACKUP_MNT/database" "$BACKUP_MNT/storage" "$BACKUP_MNT/config" || return 1
  if ! docker ps --format '{{.Names}}' 2>/dev/null | grep -qx supabase-db; then
    warn "the database is not running, so there is nothing to dump"
    backup_record_failure "the database container is not running"
    return 1
  fi
  dump=$BACKUP_MNT/database/supabase-$stamp.sql.gz
  if ! (umask 077; docker exec supabase-db pg_dumpall -h 127.0.0.1 -U supabase_admin | gzip > "$dump.partial") ||
     ! gzip -t "$dump.partial" || [[ $(stat -c %s "$dump.partial") -lt 1024 ]]; then
    rm -f "$dump.partial"
    warn "the database dump failed or is incomplete; nothing was kept"
    backup_record_failure "the database dump failed"
    return 1
  fi
  mv -f "$dump.partial" "$dump" || return 1
  ok "database  $(du -h "$dump" | cut -f1)  $(basename "$dump")"

  # Files uploaded to Supabase Storage, and the settings needed to bring everything back.
  if [[ -d $SUPABASE_DIR/volumes/storage ]]; then
    if (umask 077; tar -C "$SUPABASE_DIR/volumes" -czf "$BACKUP_MNT/storage/storage-$stamp.tar.gz.partial" storage); then
      mv -f "$BACKUP_MNT/storage/storage-$stamp.tar.gz.partial" "$BACKUP_MNT/storage/storage-$stamp.tar.gz"
      ok "storage   $(du -h "$BACKUP_MNT/storage/storage-$stamp.tar.gz" | cut -f1)  storage-$stamp.tar.gz"
    else
      rm -f "$BACKUP_MNT/storage/storage-$stamp.tar.gz.partial"
      warn "the storage files were not copied (the database dump is safe)"
    fi
  fi
  local keep=() f
  for f in "$SUPABASE_DIR/.env" "$MACSERVER_CONF"; do [[ -e $f ]] && keep+=("${f#/}"); done
  if [[ ${#keep[@]} -gt 0 ]] && (umask 077; tar -C / -czf "$BACKUP_MNT/config/config-$stamp.tar.gz" "${keep[@]}"); then
    ok "settings  config-$stamp.tar.gz"
  fi

  backup_prune "$BACKUP_MNT/database" 'supabase-*.sql.gz' "$BACKUP_KEEP_DB"
  backup_prune "$BACKUP_MNT/storage" 'storage-*.tar.gz' "$BACKUP_KEEP_STORAGE"
  backup_prune "$BACKUP_MNT/config" 'config-*.tar.gz' "$BACKUP_KEEP_CONFIG"
  sync
  backup_record_ok "$dump"
}

backup_to_drive() {
  local rc=0
  exec 8>"$BACKUP_LOCK"
  flock -n 8 || { warn "another backup is running"; return 1; }
  if ! backup_drive_open; then
    backup_drive_close || true
    warn "the backup drive is not plugged in, or will not open (sudo macserver backup-drive status)"
    backup_record_failure "the backup drive is not plugged in, or will not open"
    return 1
  fi
  backup_write || rc=$?
  backup_drive_close || warn "could not close the backup drive; it stays mounted at $BACKUP_MNT"
  [[ $rc -eq 0 ]] && ok "backed up to the USB drive"
  return $rc
}

# --- setting the drive up ----------------------------------------------------------

backup_recovery_phrase() {  # 160 random bits as XXXX-XXXX-...: easy to copy into a password manager
  head -c 20 /dev/urandom | base32 | fold -w4 | paste -sd- -
}

# bd_format DISK: erase DISK and turn it into the encrypted backup volume. DESTRUCTIVE: callers
# check with backupdrive.py first. Prints the recovery passphrase on the last line.
bd_format() {
  local disk=$1 part uuid recovery p
  [[ -b $disk ]] || { warn "$disk is not a disk"; return 1; }
  for p in $(lsblk -nrpo PATH,TYPE "$disk" | awk '$2 == "part" {print $1}'); do
    wipefs -a -f "$p" >/dev/null 2>&1 || true
  done
  wipefs -a -f "$disk" >/dev/null || return 1
  printf 'label: gpt\n,,L\n' | sfdisk --wipe always --wipe-partitions always -q "$disk" || return 1
  udevadm settle --timeout=30 || true
  part=$(lsblk -nrpo PATH,TYPE "$disk" | awk '$2 == "part" {print $1; exit}')
  [[ -b $part ]] || { warn "the new partition on $disk did not appear"; return 1; }

  install -d -m 0755 "$MACSERVER_ETC"
  (umask 077; head -c 64 /dev/urandom > "$BACKUP_KEY.new") || return 1
  chmod 0400 "$BACKUP_KEY.new"
  cryptsetup luksFormat --batch-mode "${BACKUP_LUKS_OPTS[@]}" --key-file "$BACKUP_KEY.new" "$part" || return 1
  recovery=$(backup_recovery_phrase)
  printf '%s' "$recovery" |
    cryptsetup luksAddKey --batch-mode "${BACKUP_LUKS_OPTS[@]:2}" --key-file "$BACKUP_KEY.new" "$part" - || return 1
  mv -f "$BACKUP_KEY.new" "$BACKUP_KEY" || return 1

  cryptsetup open --key-file "$BACKUP_KEY" "$part" "$BACKUP_MAP" || return 1
  mkfs.ext4 -q -L MACSERVER-BACKUP -m 0 "/dev/mapper/$BACKUP_MAP" || { cryptsetup close "$BACKUP_MAP" || true; return 1; }
  uuid=$(cryptsetup luksUUID "$part")
  conf_set BACKUP_DRIVE_UUID "$uuid"
  rm -f "$BACKUP_STATE"
  # Prove it works the way a backup will use it: mount, write, read back, close.
  install -d -m 0700 "$BACKUP_MNT"
  if mount -o noatime,nodev,nosuid,noexec "/dev/mapper/$BACKUP_MAP" "$BACKUP_MNT" &&
     printf 'macserver backup drive\n' > "$BACKUP_MNT/.macserver-backup" &&
     [[ $(cat "$BACKUP_MNT/.macserver-backup") == 'macserver backup drive' ]]; then
    backup_drive_close || return 1
  else
    backup_drive_close || true
    warn "the new drive did not pass its write test"
    return 1
  fi
  printf '%s\n' "$recovery"
}

backup_drive_setup() {  # backup_drive_setup [DISK]
  local disk=${1:-} lines n pick typed recovery
  command -v cryptsetup >/dev/null || apt_quiet install cryptsetup
  if [[ -z $disk ]]; then
    lines=$(python3 "$BACKUPDRIVE_PY" list --eligible) || die "could not list the disks"
    n=$(grep -c . <<<"$lines" || true)
    if [[ $n -eq 0 ]]; then
      say "No USB disk can be used. What the Mac sees:"
      python3 "$BACKUPDRIVE_PY" list >&2 || true
      die "plug in the USB drive (and unmount it if it is mounted), then run this again"
    elif [[ $n -eq 1 ]]; then
      disk=$(cut -f1 <<<"$lines")
    else
      say "More than one USB disk is plugged in:"
      nl -w2 -s'  ' <<<"$lines" | sed 's/\t/  /'
      pick=$(ask "Which one is the backup drive (number)")
      [[ $pick =~ ^[0-9]+$ && $pick -ge 1 && $pick -le $n ]] || die "not a choice"
      disk=$(sed -n "${pick}p" <<<"$lines" | cut -f1)
    fi
  fi
  python3 "$BACKUPDRIVE_PY" check "$disk" >&2 || die "$disk cannot be used for backups"
  say ""
  say "This drive will be used for backups:"
  python3 "$BACKUPDRIVE_PY" describe "$disk"
  say ""
  warn "EVERYTHING on it will be erased: every partition and file shown above. This cannot be undone."
  [[ -z $(backup_drive_uuid) ]] || warn "A backup drive is already set up. This replaces it; its old backups can no longer be opened."
  typed=$(ask "Type ERASE to erase $disk and use it for backups")
  [[ $typed == ERASE ]] || die "nothing was changed: ERASE was not typed"
  python3 "$BACKUPDRIVE_PY" check "$disk" >&2 || die "$disk changed while you were deciding; nothing was erased"
  say "Erasing and encrypting $disk (this takes about a minute)..."
  recovery=$(bd_format "$disk") || die "setting up the drive failed; see the messages above (the drive may be half-set-up: run this again)"
  recovery=$(tail -n 1 <<<"$recovery")
  ok "backup drive ready: encrypted, and it opens only on this Mac"
  say ""
  say "  RECOVERY PASSPHRASE (shown once, never stored on this Mac):"
  say ""
  say "      $recovery"
  say ""
  say "  Put it in your password manager now. If this Mac's disk dies, it is the only way to read the backups."
  say ""
  if confirm "Run the first backup now?" y; then backup_to_drive || true; fi
}

backup_drive_status() {
  local uuid state present="no" at ok_at age=""
  uuid=$(backup_drive_uuid)
  if [[ -z $uuid ]]; then
    say "No backup drive is set up. Backups go to /var/backups/macserver on this Mac's own disk."
    say "Plug in a USB drive and run: sudo macserver backup-drive setup"
    return 0
  fi
  [[ -e /dev/disk/by-uuid/$uuid ]] && present="yes"
  say "Backup drive:  set up (encrypted, UUID $uuid)"
  say "Plugged in:    $present"
  state=$(cat "$BACKUP_STATE" 2>/dev/null || echo '{}')
  ok_at=$(jq -r '.ok_at // 0' <<<"$state")
  if [[ $ok_at -gt 0 ]]; then
    at=$(( $(date +%s) - ok_at ))
    age="$(( at / 3600 )) h $(( at % 3600 / 60 )) min ago"
    say "Last backup:   $age, $(jq -r '.count' <<<"$state") kept, newest $(numfmt --to=iec "$(jq -r '.newest_size' <<<"$state")"), $(numfmt --to=iec "$(jq -r '.free // 0' <<<"$state")") free"
  else
    say "Last backup:   none yet (sudo macserver backup)"
  fi
  [[ -z $(jq -r '.error // ""' <<<"$state") ]] || warn "last attempt failed: $(jq -r '.error' <<<"$state")"
}

backup_drive_files() {  # list what is on the drive (opens it for the moment)
  backup_drive_open || die "the backup drive is not plugged in, or will not open"
  find "$BACKUP_MNT" -type f ! -path '*/lost+found/*' -printf '%TY-%Tm-%Td %TH:%TM  %8s bytes  %P
' | sort -k4 || true
  backup_drive_close || true
}
