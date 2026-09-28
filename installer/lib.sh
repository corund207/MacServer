# shellcheck shell=bash
# Shared helpers for install.sh and the macserver command. Sourced, not executed.

# shellcheck disable=SC2034  # constants are used by the scripts that source this file
MACSERVER_ETC=/etc/macserver
MACSERVER_CONF=$MACSERVER_ETC/macserver.conf
MACSERVER_STATE_DIR=/var/lib/macserver
MACSERVER_STATE=$MACSERVER_STATE_DIR/install.state
MACSERVER_OPT=/opt/macserver
SUPABASE_DIR=$MACSERVER_OPT/supabase
GATEWAY_DIR=$MACSERVER_OPT/gateway
ADMIN_DIR=$MACSERVER_OPT/admin
ADMIN_PORT=8090
STUDIO_TAILNET_PORT=8443

# Pinned upstream sources. Update deliberately, with a review of the diff.
SUPABASE_REF=self-hosted/v0.8.2
SUPABASE_COMMIT=47111f95a43ffcc20ab288e29c48ce0b80174bd6
T2_KEY_FPR=9F9873A566A73E27CFF0294FE2E496114ACDBFD4
DOCKER_KEY_FPR=9DC858229FC7DD38854AE2D88D81803C0EBFCD88
TAILSCALE_KEY_FPR=2596A99EAAB33821893C0A79458CA832957F5868
# Claude Code native binary for linux-x64 from the npm registry, pinned by version
# and the SHA-512 of the package tarball (its npm "integrity").
CLAUDE_CODE_VERSION=2.1.284
CLAUDE_CODE_SHA512=8638cf80de2ef03be7cd9a960d89d4e71c5093292e52b914a1579ebad54b5e3ae1c78a9e44f9b0819ebd045007995e3614145d891c1b2db88313d9e441d004f2
FALLBACK_DNS=(9.9.9.9 149.112.112.112)

if [[ -t 1 ]]; then
  C_B=$'\e[1m' C_G=$'\e[32m' C_Y=$'\e[33m' C_R=$'\e[31m' C_C=$'\e[36m' C_0=$'\e[0m'
else
  C_B='' C_G='' C_Y='' C_R='' C_C='' C_0=''
fi

say()  { printf '%s\n' "$*"; }
head1() { printf '\n%s==> %s%s\n' "$C_B$C_C" "$*" "$C_0"; }
ok()   { printf '%s  ok%s %s\n' "$C_G" "$C_0" "$*"; }
warn() { printf '%s  !!%s %s\n' "$C_Y" "$C_0" "$*" >&2; }
# check GOOD BAD CMD...: print ok GOOD if CMD succeeds, else warn BAD.
check() { local good=$1 bad=$2; shift 2; if "$@"; then ok "$good"; else warn "$bad"; fi; }
die()  { printf '%s  error:%s %s\n' "$C_R" "$C_0" "$*" >&2; exit 1; }


# Unattended mode (first boot after the disk-image installer): every prompt takes
# its default, and secrets are never asked for.
MACSERVER_UNATTENDED=${MACSERVER_UNATTENDED:-0}
unattended() { [[ $MACSERVER_UNATTENDED == 1 ]]; }

ask() {  # ask "Question" "default" -> echoes answer
  local reply
  if unattended; then printf '%s' "$2"; return; fi
  printf '%s [%s]: ' "$1" "$2" > /dev/tty
  IFS= read -r reply < /dev/tty || reply=''
  printf '%s' "${reply:-$2}"
}

ask_secret() {  # ask_secret "Question" -> echoes answer (not shown)
  local reply
  unattended && return 0
  printf '%s: ' "$1" > /dev/tty
  IFS= read -rs reply < /dev/tty || reply=''
  printf '\n' > /dev/tty
  printf '%s' "$reply"
}

confirm() {  # confirm "Question" [default y|n]; returns 0 for yes
  local def=${2:-n} hint reply
  if unattended; then [[ $def == y ]]; return; fi
  [[ $def == y ]] && hint='Y/n' || hint='y/N'
  printf '%s [%s]: ' "$1" "$hint" > /dev/tty
  IFS= read -r reply < /dev/tty || reply=''
  reply=${reply:-$def}
  [[ ${reply,,} == y || ${reply,,} == yes ]]
}

# --- persistent install state -------------------------------------------------

state_has() { [[ -f $MACSERVER_STATE ]] && grep -qx "$1" "$MACSERVER_STATE"; }
state_add() { install -d -m 0700 "$MACSERVER_STATE_DIR"; state_has "$1" || echo "$1" >> "$MACSERVER_STATE"; }
state_del() { [[ -f $MACSERVER_STATE ]] && sed -i "/^$1\$/d" "$MACSERVER_STATE"; return 0; }

# --- key=value configuration (no secrets) -------------------------------------

conf_get() {  # conf_get KEY [default]
  local v=''
  [[ -f $MACSERVER_CONF ]] && v=$(grep -m1 "^$1=" "$MACSERVER_CONF" | cut -d= -f2-)
  printf '%s' "${v:-${2:-}}"
}

conf_set() {  # conf_set KEY VALUE
  install -d -m 0755 "$MACSERVER_ETC"
  touch "$MACSERVER_CONF"; chmod 0644 "$MACSERVER_CONF"
  if grep -q "^$1=" "$MACSERVER_CONF"; then
    local tmp; tmp=$(mktemp)
    awk -v k="$1" -v v="$2" 'index($0, k "=") == 1 { print k "=" v; next } { print }' "$MACSERVER_CONF" > "$tmp"
    cat "$tmp" > "$MACSERVER_CONF"; rm -f "$tmp"
  else
    printf '%s=%s\n' "$1" "$2" >> "$MACSERVER_CONF"
  fi
}

# env_set FILE KEY VALUE: replace or append KEY=VALUE in a dotenv file.
env_set() {
  local file=$1 key=$2 value=$3 tmp
  tmp=$(mktemp)
  if grep -q "^$key=" "$file"; then
    awk -v k="$key" -v v="$value" 'index($0, k "=") == 1 { print k "=" v; next } { print }' "$file" > "$tmp"
  else
    cat "$file" > "$tmp"; printf '%s=%s\n' "$key" "$value" >> "$tmp"
  fi
  cat "$tmp" > "$file"; rm -f "$tmp"
}

env_get() { grep -m1 "^$2=" "$1" 2>/dev/null | cut -d= -f2-; }

# --- files ----------------------------------------------------------------------

# put_file SRC DEST MODE: install SRC at DEST; keep one backup of a differing original.
put_file() {
  local src=$1 dest=$2 mode=$3
  if [[ -f $dest ]] && ! cmp -s "$src" "$dest"; then
    [[ -e $dest.macserver-orig ]] || cp -p "$dest" "$dest.macserver-orig"
  fi
  install -D -m "$mode" "$src" "$dest"
}

# --- apt sources pinned by key fingerprint ---------------------------------------

# apt_key NAME URL FINGERPRINT: fetch a signing key, refuse it unless its primary
# fingerprint matches, and store it as /etc/apt/keyrings/NAME.gpg.
apt_key() {  # apt_key NAME URL FINGERPRINT [KEYRING_DIR]
  local name=$1 url=$2 fpr=$3 dir=${4:-/etc/apt/keyrings} tmp got
  tmp=$(mktemp -d)
  curl -fsSL --compressed --proto '=https' --tlsv1.2 "$url" -o "$tmp/key" || { rm -rf "$tmp"; die "could not download the $name signing key"; }
  got=$(gpg --show-keys --with-colons "$tmp/key" 2>/dev/null | awk -F: '/^fpr:/ { print $10; exit }')
  if [[ $got != "$fpr" ]]; then
    rm -rf "$tmp"
    die "$name signing key fingerprint is '$got', expected '$fpr'. Refusing to trust it."
  fi
  install -d -m 0755 "$dir"
  gpg --dearmor < "$tmp/key" > "$tmp/key.gpg" 2>/dev/null || cp "$tmp/key" "$tmp/key.gpg"
  install -m 0644 "$tmp/key.gpg" "$dir/$name.gpg"
  rm -rf "$tmp"
  ok "$name signing key verified ($fpr)"
}

apt_quiet() { DEBIAN_FRONTEND=noninteractive apt-get -y -q -o Dpkg::Options::=--force-confold "$@"; }

# --- hardware and network facts ---------------------------------------------------

is_t2() {  # Apple T2 bridge (106b:1801) or T2 SEP (106b:1802)
  local d
  for d in /sys/bus/pci/devices/*; do
    [[ $(cat "$d/vendor" 2>/dev/null) == 0x106b ]] || continue
    case $(cat "$d/device" 2>/dev/null) in 0x1801|0x1802) return 0 ;; esac
  done
  return 1
}

tailscale_running() { [[ $(tailscale status --json 2>/dev/null | jq -r .BackendState) == Running ]]; }
running_t2_kernel() { [[ $(uname -r) == *t2* ]]; }

can_reach_ip() { timeout 5 bash -c "exec 3<>/dev/tcp/${FALLBACK_DNS[0]}/53" 2>/dev/null; }
can_resolve() { timeout 8 getent hosts deb.debian.org >/dev/null 2>&1; }

tailnet_name() {  # MagicDNS name without trailing dot, or empty
  tailscale status --json 2>/dev/null | jq -r '.Self.DNSName // empty' | sed 's/\.$//'
}

supabase_compose() { (cd "$SUPABASE_DIR" && docker compose "$@"); }
gateway_compose() { docker compose -p macserver-gateway --project-directory "$GATEWAY_DIR" -f "$GATEWAY_DIR/compose.yml" "$@"; }

# make_initrds ROOT: create or refresh the initramfs of every kernel under ROOT.
# (update-initramfs -u only refreshes existing images; a kernel installed before
# initramfs-tools has none yet.)
make_initrds() {
  local root=$1 kver
  for kver in "$root"/lib/modules/*; do
    kver=${kver##*/}
    [[ -f $root/boot/vmlinuz-$kver ]] || continue
    if [[ -f $root/boot/initrd.img-$kver ]]; then
      chroot "$root" update-initramfs -u -k "$kver"
    else
      chroot "$root" update-initramfs -c -k "$kver"
    fi
  done
}

# Kernel modules the T2 keyboard and trackpad need at the disk passphrase prompt.
# (Kernels before 6.18 called the driver apple-bce; it is now t2bce_*.)
T2_INITRD_MODULES=(t2bce_dma t2bce_core t2bce_vhci usbhid hid_apple hid_generic)

t2_keyboard_loaded() { [[ -d /sys/module/t2bce_vhci || -d /sys/module/apple_bce ]]; }

# add_initrd_modules ROOT: list T2_INITRD_MODULES in ROOT/etc/initramfs-tools/modules.
add_initrd_modules() {
  local file=$1/etc/initramfs-tools/modules m
  install -d "${file%/*}"
  touch "$file"
  for m in "${T2_INITRD_MODULES[@]}"; do grep -qx "$m" "$file" || echo "$m" >> "$file"; done
}

# --- clock --------------------------------------------------------------------------
# A Mac whose battery ran flat can wake with its clock years off, and then every
# HTTPS certificate looks invalid. These helpers get the time without HTTPS and,
# for the first sources, without DNS. (Package signatures are still verified by
# apt/debootstrap; the clock only has to be close enough for TLS.)

# sntp_time IP: Unix time from an NTP server over UDP 123 (no DNS needed).
sntp_time() {
  timeout 8 python3 - "$1" <<'PY'
import socket, struct, sys
s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
s.settimeout(4)
s.sendto(b"\x1b" + 47 * b"\0", (sys.argv[1], 123))
data = s.recv(48)
if len(data) < 48 or data[0] & 0x7 != 4 or data[1] == 0:   # server mode, synchronised
    sys.exit(1)
print(struct.unpack("!I", data[40:44])[0] - 2208988800)
PY
}

# http_time URL: Unix time from a web server's Date header (plain HTTP).
http_time() {
  local stamp
  stamp=$(curl -sI --max-time 8 "$1" 2>/dev/null | tr -d '\r' | sed -n 's/^[Dd]ate: //p' | head -1)
  [[ -n $stamp ]] && date -d "$stamp" +%s 2>/dev/null
}

# network_time: Unix time from the first source that answers.
NTP_IPS=(162.159.200.1 162.159.200.123 216.239.35.0 216.239.35.4)   # time.cloudflare.com, time.google.com
TIME_URLS=(http://deb.debian.org/ http://www.google.com/ http://www.cloudflare.com/)
network_time() {
  local src t
  for src in "${NTP_IPS[@]}"; do
    t=$(sntp_time "$src" 2>/dev/null) && (( t > 1735689600 )) && { echo "$t"; return 0; }
  done
  for src in "${TIME_URLS[@]}"; do
    t=$(http_time "$src") && (( t > 1735689600 )) && { echo "$t"; return 0; }
  done
  return 1
}

# set_clock UNIX_TIME: set the system clock and the Mac's hardware clock (UTC).
set_clock() {
  date -s "@$1" >/dev/null
  if command -v hwclock >/dev/null; then hwclock --systohc --utc 2>/dev/null || true; fi
}

# sync_clock: fix the clock from the network if it is more than 2 minutes off.
sync_clock() {
  local t now
  t=$(network_time) || return 1
  now=$(date +%s)
  if (( t - now > 120 || now - t > 120 )); then set_clock "$t"; fi
  return 0
}

# https_works: can this machine fetch from Debian over HTTPS (DNS + route + clock)?
https_works() { timeout 15 curl -fsI https://deb.debian.org/debian/dists/trixie/Release >/dev/null 2>&1; }

# admin_user: the owner's login account (set by the USB installer, else the sudo user,
# else the first member of the sudo group).
admin_user() {
  local u
  u=$(conf_get ADMIN_USER "")
  [[ -z $u && -n ${SUDO_USER:-} && $SUDO_USER != root ]] && u=$SUDO_USER
  [[ -z $u ]] && u=$(getent group sudo | cut -d: -f4 | cut -d, -f1)
  [[ -n $u ]] && id "$u" >/dev/null 2>&1 && echo "$u"
}
