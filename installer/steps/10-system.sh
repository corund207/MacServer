# shellcheck shell=bash
# Steps: preflight, network, base.

step_preflight() {
  # shellcheck source=/dev/null
  . /etc/os-release
  [[ ${ID:-} == debian && ${VERSION_ID:-} == 13 ]] || die "Debian 13 (trixie) is required; found ${PRETTY_NAME:-unknown}."
  [[ $(uname -m) == x86_64 ]] || die "an x86_64 (Intel) Mac is required."
  [[ -d /sys/firmware/efi ]] || die "Debian must be installed in UEFI mode."
  ok "Debian 13 x86_64, UEFI"

  local model; model=$(cat /sys/class/dmi/id/product_name 2>/dev/null || echo unknown)
  if is_t2; then
    ok "Apple T2 Mac detected ($model)"
  else
    warn "no Apple T2 chip found ($model). The T2 kernel step will be skipped."
    confirm "Continue anyway?" n || return 1
  fi

  local mem_kb free_gb
  mem_kb=$(awk '/^MemTotal:/ { print $2 }' /proc/meminfo)
  (( mem_kb >= 3800000 )) || die "at least 4 GB of RAM is required."
  free_gb=$(df -BG --output=avail / | tail -1 | tr -dc 0-9)
  (( free_gb >= 30 )) || die "at least 30 GB must be free on / (found ${free_gb} GB)."
  ok "$((mem_kb / 1024 / 1024)) GB RAM, ${free_gb} GB free"

  if lsblk -rno TYPE | grep -qx crypt; then
    ok "disk encryption is on (you type the passphrase at boot)"
  else
    warn "the disk is not encrypted. Anyone with the laptop can read the database."
  fi
}

# Many phones hand out a DNS server that does not answer Linux. Replace it.
fix_dns() {
  local conf list
  list=$(IFS=,; echo "${FALLBACK_DNS[*]}")
  say "Using fixed DNS servers: ${FALLBACK_DNS[*]}"
  [[ -L /etc/resolv.conf ]] && { warn "/etc/resolv.conf is managed by another service; set DNS there."; return 1; }
  [[ -e /etc/resolv.conf.macserver-orig ]] || cp -p /etc/resolv.conf /etc/resolv.conf.macserver-orig 2>/dev/null || true
  printf 'nameserver %s\n' "${FALLBACK_DNS[@]}" > /etc/resolv.conf
  # Keep the override when DHCP renews the lease.
  if [[ -x /usr/sbin/dhclient || -x /sbin/dhclient ]]; then
    conf=/etc/dhcp/dhclient.conf
    grep -q 'macserver dns' "$conf" 2>/dev/null ||
      printf '\n# macserver dns\nsupersede domain-name-servers %s;\n' "${list//,/, }" >> "$conf"
  fi
  if [[ -x /usr/sbin/dhcpcd ]]; then
    conf=/etc/dhcpcd.conf
    grep -q 'macserver dns' "$conf" 2>/dev/null ||
      printf '\n# macserver dns\nstatic domain_name_servers=%s\n' "${FALLBACK_DNS[*]}" >> "$conf"
  fi
  if [[ -d /etc/NetworkManager/conf.d ]]; then
    printf '[global-dns-domain-*]\nservers=%s\n' "$list" > /etc/NetworkManager/conf.d/60-macserver-dns.conf
    systemctl reload NetworkManager 2>/dev/null || true
  fi
}

step_network() {
  if can_resolve; then ok "Internet and DNS work"; return 0; fi
  if ! can_reach_ip; then
    warn "no Internet connection."
    say "Connect a USB-C Ethernet adapter, an Android phone (USB tethering) or an iPhone"
    say "(Personal Hotspot over USB), then run this installer again. See docs/NETWORK.md."
    return 1
  fi
  warn "the Internet works but DNS does not (typical for iPhone tethering)."
  confirm "Switch this Mac to fixed public DNS servers (${FALLBACK_DNS[*]})?" y || return 1
  fix_dns || return 1
  can_resolve || die "DNS still fails after the fix. See docs/NETWORK.md."
  ok "DNS fixed"
}

step_base() {
  apt_quiet update
  apt_quiet install ca-certificates curl gnupg jq git openssl python3 sudo \
    nftables unattended-upgrades apt-listchanges pciutils usbutils lm-sensors \
    smartmontools
  put_file "$SRC/host/20auto-upgrades" /etc/apt/apt.conf.d/20auto-upgrades 0644
  ok "base packages installed; Debian security updates install automatically (no automatic reboot)"
}
