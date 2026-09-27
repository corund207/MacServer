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
apt_key() {
  local name=$1 url=$2 fpr=$3 tmp got
  tmp=$(mktemp -d)
  curl -fsSL --compressed --proto '=https' --tlsv1.2 "$url" -o "$tmp/key" || { rm -rf "$tmp"; die "could not download the $name signing key"; }
  got=$(gpg --show-keys --with-colons "$tmp/key" 2>/dev/null | awk -F: '/^fpr:/ { print $10; exit }')
  if [[ $got != "$fpr" ]]; then
    rm -rf "$tmp"
    die "$name signing key fingerprint is '$got', expected '$fpr'. Refusing to trust it."
  fi
  install -d -m 0755 /etc/apt/keyrings
  gpg --dearmor < "$tmp/key" > "$tmp/key.gpg" 2>/dev/null || cp "$tmp/key" "$tmp/key.gpg"
  install -m 0644 "$tmp/key.gpg" "/etc/apt/keyrings/$name.gpg"
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
