#!/usr/bin/env bash
# Tests the USB backup drive (installer/steps/65-backup-drive.sh) for real: a loop device stands
# in for the USB stick, and the code erases, partitions, LUKS-encrypts, formats, mounts and backs
# up to it, with a stub `docker` in place of the database container.
#
#   sudo bash tests/backup_drive_test.sh
# Skips (exit 0) on a machine without loop devices or dm-crypt. Which disk is allowed to be
# erased is tested separately, without root, in tests/test_backupdrive.py.
# shellcheck disable=SC2016,SC2317  # check() evaluates its single-quoted condition later
set -uo pipefail
ROOT=$(cd "$(dirname "$0")/.." && pwd)
[[ $EUID -eq 0 ]] || exec sudo bash "$0" "$@"
skip() { echo "backup drive test skipped: $*"; exit 0; }
for tool in losetup cryptsetup mkfs.ext4 sfdisk wipefs jq base32 udevadm; do
  command -v "$tool" >/dev/null || skip "$tool is not installed"
done
modprobe dm-crypt 2>/dev/null || true
[[ -e /dev/mapper/control ]] || skip "no device-mapper"

W=$(mktemp -d)
LOOP=''
cleanup() {
  umount "$W/mnt" 2>/dev/null || true
  cryptsetup close macserver-backup 2>/dev/null || true
  [[ -z $LOOP ]] || losetup -d "$LOOP" 2>/dev/null || true
  rm -rf "$W"
}
trap cleanup EXIT
fails=0
check() { if eval "$2"; then echo "  ok $1"; else echo "  FAIL $1"; fails=$((fails + 1)); fi; }

# Attach a 1 GiB sparse file as a "USB disk" whose partitions show up (-P). Old kernels without
# /dev/loop-control need an explicit device.
truncate -s 1G "$W/usb.img"
mkfs.ext4 -q -F -L old-data "$W/usb.img"          # something on it that must be erased
LOOP=$(losetup -fP --show "$W/usb.img" 2>/dev/null) || LOOP=''
if [[ -z $LOOP ]]; then
  for n in 0 1 2 3 4 5 6 7; do
    if losetup -P "/dev/loop$n" "$W/usb.img" 2>/dev/null; then LOOP=/dev/loop$n; break; fi
  done
fi
[[ -n $LOOP ]] || skip "no free loop device"

# The code under test, pointed at a scratch Mac.
# shellcheck source=installer/lib.sh
source "$ROOT/installer/lib.sh"
MACSERVER_ETC=$W/etc MACSERVER_CONF=$W/etc/macserver.conf MACSERVER_STATE_DIR=$W/state
SUPABASE_DIR=$W/supabase
mkdir -p "$W/etc" "$W/state" "$SUPABASE_DIR/volumes/storage/bucket" "$W/mnt"
echo "SECRET_KEY=abc" > "$SUPABASE_DIR/.env"
echo "photo" > "$SUPABASE_DIR/volumes/storage/bucket/a.jpg"
echo "TAILNET_NAME=x.ts.net" > "$MACSERVER_CONF"
# shellcheck source=installer/steps/65-backup-drive.sh
source "$ROOT/installer/steps/65-backup-drive.sh"
BACKUP_MNT=$W/mnt BACKUP_LOCK=$W/lock
BACKUP_LUKS_OPTS=(--type luks2 --pbkdf pbkdf2 --pbkdf-force-iterations 1000)   # fast: the real ones use argon2id
# No udev here, so there is no /dev/disk/by-uuid: hand the code the partition it would find.
PART=
backup_drive_dev() { [[ -n $PART ]] && echo "$PART"; }

# A stub for the database container: `docker ps` lists it, `docker exec` dumps (or fails).
mkdir -p "$W/bin"
cat > "$W/bin/docker" <<'EOF'
#!/bin/sh
case "$1" in
  ps) echo supabase-db ;;
  exec) [ -n "$FAKE_DUMP_FAIL" ] && exit 1; head -c 6000 /dev/urandom | base64 ;;
esac
EOF
chmod +x "$W/bin/docker"
export PATH="$W/bin:$PATH"

echo "== it refuses to erase something that is not a disk"
check "a plain file is refused" '! bd_format "$W/usb.img" 2>/dev/null'
check "the old data is still there" '[[ $(blkid -s LABEL -o value "$W/usb.img") == old-data ]]'

echo "== erase, encrypt and format the drive"
recovery=$(bd_format "$LOOP" 2>"$W/format.err" | tail -n 1) || { cat "$W/format.err"; fails=$((fails + 1)); }
PART=$(lsblk -nrpo PATH,TYPE "$LOOP" | awk '$2 == "part" {print $1; exit}')
check "one partition was made" '[[ -b $PART ]]'
check "the old data is gone" '[[ -z $(blkid -s LABEL -o value "$LOOP") && $(blkid -s TYPE -o value "$PART") == crypto_LUKS ]]'
check "a recovery passphrase is printed" '[[ $recovery =~ ^([A-Z2-7]{4}-){7}[A-Z2-7]{4}$ ]]'
check "the recovery passphrase opens the drive" 'printf "%s" "$recovery" | cryptsetup open --test-passphrase --key-file - "$PART"'
check "so does the key on the Mac" 'cryptsetup open --test-passphrase --key-file "$BACKUP_KEY" "$PART"'
check "the key is private" '[[ $(stat -c %a "$BACKUP_KEY") == 400 && $(stat -c %s "$BACKUP_KEY") == 64 ]]'
check "a wrong passphrase does not" '! printf "wrong" | cryptsetup open --test-passphrase --key-file - "$PART" 2>/dev/null'
check "the drive is recorded" '[[ $(backup_drive_uuid) == $(cryptsetup luksUUID "$PART") ]]'
check "it is closed afterwards" '[[ ! -e /dev/mapper/macserver-backup ]] && ! mountpoint -q "$BACKUP_MNT"'
check "the recovery passphrase is not stored" '! grep -rq "$recovery" "$W/etc" "$W/state" 2>/dev/null'

echo "== a backup"
backup_to_drive >"$W/out.txt" 2>&1; rc=$?
check "succeeds" '[[ $rc == 0 ]]'
backup_drive_open
check "the database dump is there and complete" 'f=$(ls "$BACKUP_MNT"/database/supabase-*.sql.gz) && gzip -t "$f"'
check "the Storage files are there" 'tar -tzf "$BACKUP_MNT"/storage/storage-*.tar.gz | grep -q bucket/a.jpg'
check "the settings are there, nothing else from /etc" 'tar -tzf "$BACKUP_MNT"/config/config-*.tar.gz | grep -q "macserver.conf" && ! tar -tzf "$BACKUP_MNT"/config/config-*.tar.gz | grep -qE "totp|gh-token|backup-drive.key"'
check "backups are private" '[[ $(stat -c %a "$(ls "$BACKUP_MNT"/database/supabase-*.sql.gz)") == 600 ]]'
check "no half-written files" '! find "$BACKUP_MNT" -name "*.partial" | grep -q .'
backup_drive_close
check "the drive is closed again" '[[ ! -e /dev/mapper/macserver-backup ]] && ! mountpoint -q "$BACKUP_MNT"'
check "the dashboards' record says so" '[[ $(jq -r ".count" "$BACKUP_STATE") == 1 && $(jq -r ".error" "$BACKUP_STATE") == "" && $(jq -r ".ok_at" "$BACKUP_STATE") -gt 0 ]]'

echo "== old backups are pruned, the newest are kept"
backup_drive_open
for i in $(seq 1 35); do : > "$BACKUP_MNT/database/supabase-2020010$((i % 10))-0000$(printf %02d "$i").sql.gz"; done
backup_drive_close
sleep 1
backup_to_drive >/dev/null 2>&1
backup_drive_open
check "30 database dumps are kept" '[[ $(ls "$BACKUP_MNT"/database | wc -l) == 30 ]]'
check "the new one survived" 'ls "$BACKUP_MNT"/database | grep -q "^supabase-$(date +%Y)"'
backup_drive_close

echo "== a failed dump is never kept, and says so"
# shellcheck disable=SC2034  # read inside check()'s single-quoted condition
before=$(jq -r .ok_at "$BACKUP_STATE")
FAKE_DUMP_FAIL=1 backup_to_drive >/dev/null 2>&1; rc=$?
check "the backup reports failure" '[[ $rc != 0 ]]'
backup_drive_open
check "no partial dump is left" '! find "$BACKUP_MNT" -name "*.partial" | grep -q .'
backup_drive_close
check "the error is recorded, the last good backup kept" '[[ $(jq -r .error "$BACKUP_STATE") == *dump* && $(jq -r .ok_at "$BACKUP_STATE") == $before ]]'
check "the drive is closed" '[[ ! -e /dev/mapper/macserver-backup ]]'

echo "== the drive is not plugged in"
PART_SAVED=$PART
PART=
backup_to_drive >/dev/null 2>&1; rc=$?
check "the backup reports failure" '[[ $rc != 0 ]]'
check "and says why" '[[ $(jq -r .error "$BACKUP_STATE") == *"not plugged in"* ]]'
PART=$PART_SAVED

echo "== a key that does not fit"
cp "$BACKUP_KEY" "$W/key.good"; head -c 64 /dev/urandom > "$BACKUP_KEY"
backup_to_drive >/dev/null 2>&1; rc=$?
check "the backup reports failure and closes everything" '[[ $rc != 0 && ! -e /dev/mapper/macserver-backup ]] && ! mountpoint -q "$BACKUP_MNT"'
cp "$W/key.good" "$BACKUP_KEY"

echo "== the dashboard record for a set-up drive"
check "a failed run keeps the numbers" '[[ $(jq -r .count "$BACKUP_STATE") -ge 1 ]]'

(( fails == 0 )) || { echo "backup drive test: $fails failure(s)"; exit 1; }
echo "backup drive test passed"
