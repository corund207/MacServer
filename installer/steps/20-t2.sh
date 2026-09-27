# shellcheck shell=bash
# Steps: t2, host, reboot, wifi.

step_t2() {
  if ! is_t2; then ok "not a T2 Mac; skipping"; return 0; fi
  if dpkg -s linux-t2-lts >/dev/null 2>&1; then ok "T2 kernel already installed"; return 0; fi
  cat <<'EOF'
Stock Debian cannot drive the T2 chip: the internal keyboard, trackpad, Wi-Fi and
fan control stay dead. This step adds the t2linux community kernel repository
(key fingerprint checked), and pins it so it can only supply T2 packages. It then
installs:
  - linux-t2-lts         the Debian kernel plus the T2 drivers (long-term branch)
  - apple-firmware-script  get-apple-firmware, which fetches the Wi-Fi firmware
  - t2fanrd              fan control, so the fans ramp up under load
It also adds the kernel options t2linux requires, and puts the keyboard driver in
the boot image so you can type the disk passphrase on the built-in keyboard.
The current Debian kernel stays installed as a fallback in the GRUB "Advanced" menu.
EOF
  confirm "Install the T2 kernel?" y || { warn "skipped; the internal keyboard and Wi-Fi will not work"; return 0; }

  apt_key t2 https://adityagarg8.github.io/t2-ubuntu-repo/KEY.gpg "$T2_KEY_FPR"
  put_file "$SRC/host/t2.sources" /etc/apt/sources.list.d/t2.sources 0644
  put_file "$SRC/host/t2.pref" /etc/apt/preferences.d/t2.pref 0644
  apt_quiet update
  apt_quiet install linux-t2-lts apple-firmware-script
  if apt-cache show t2fanrd >/dev/null 2>&1; then
    apt_quiet install t2fanrd
    systemctl enable t2fanrd >/dev/null 2>&1 || true
  fi

  put_file "$SRC/host/grub-t2.cfg" /etc/default/grub.d/60-macserver-t2.cfg 0644
  add_initrd_modules /
  update-initramfs -u -k all
  update-grub

  # Stop Debian's own kernel updates from overtaking the T2 kernel at boot.
  if dpkg -s linux-image-amd64 >/dev/null 2>&1; then
    apt-mark hold linux-image-amd64 >/dev/null
    ok "held linux-image-amd64 so GRUB keeps booting the T2 kernel by default"
  fi
  ok "T2 kernel installed; it takes effect after the reboot step"
}

step_host() {
  put_file "$SRC/host/logind.conf" /etc/systemd/logind.conf.d/60-macserver.conf 0644
  systemctl mask sleep.target suspend.target hibernate.target hybrid-sleep.target >/dev/null 2>&1
  ok "closing the lid no longer suspends; sleep is disabled"

  put_file "$SRC/host/journald.conf" /etc/systemd/journald.conf.d/60-macserver.conf 0644
  put_file "$SRC/host/sysctl.conf" /etc/sysctl.d/60-macserver.conf 0644
  sysctl --quiet --system >/dev/null || true
  systemctl enable --now systemd-timesyncd >/dev/null 2>&1 || true

  put_file "$SRC/host/macserver-battery.service" /etc/systemd/system/macserver-battery.service 0644
  systemctl daemon-reload
  systemctl enable macserver-battery.service >/dev/null
  systemctl restart systemd-journald
  ok "logs capped at 500 MB, clock sync on, battery limit service installed"
}

step_reboot() {
  if ! is_t2 || ! dpkg -s linux-t2-lts >/dev/null 2>&1; then ok "no kernel change to activate"; return 0; fi
  if ! running_t2_kernel; then
    say "The T2 kernel is installed but not running yet."
    if unattended; then say "Rebooting into it; setup continues automatically."; systemctl reboot; return 10; fi
    if confirm "Reboot now? After the reboot, log in and run: sudo ./install.sh" y; then
      systemctl reboot
    fi
    say "Reboot when ready, then run: sudo ./install.sh"
    return 10
  fi
  ok "running $(uname -r)"
  if t2_keyboard_loaded; then
    ok "T2 keyboard driver loaded: the built-in keyboard and trackpad work"
  else
    warn "the T2 keyboard driver is not loaded; the built-in keyboard may not work (try: sudo modprobe t2bce_vhci)"
  fi
}

wifi_firmware_present() { compgen -G '/lib/firmware/brcm/brcmfmac*apple*' >/dev/null; }

step_wifi() {
  if ! is_t2; then ok "not a T2 Mac; skipping"; return 0; fi
  if unattended; then ok "set up by the USB installer (change it later: sudo macserver wifi)"; return 0; fi
  say "A wired connection (USB-C Ethernet) is the most reliable for a server, but Wi-Fi works too."
  confirm "Set up the built-in Wi-Fi?" y || { ok "skipped (run later: sudo macserver wifi)"; return 0; }
  wifi_setup
}

wifi_setup() {
  if ! wifi_firmware_present; then
    cat <<'EOF'
Apple's Wi-Fi firmware may not be redistributed, so it must be fetched from Apple.
get-apple-firmware will download a macOS Recovery image from Apple's servers
(about 1 GB) and extract only the Wi-Fi and Bluetooth firmware. When it asks,
pick the newest macOS offered.
EOF
    confirm "Download the firmware now?" y || return 0
    apt_quiet install dmg2img curl python3
    get-apple-firmware get_from_online
    modprobe -r brcmfmac_wcc 2>/dev/null || true
    modprobe -r brcmfmac 2>/dev/null || true
    modprobe brcmfmac
    sleep 3
  fi
  wifi_firmware_present || die "Wi-Fi firmware is still missing. See docs/NETWORK.md."
  ok "Wi-Fi firmware installed"

  apt_quiet install network-manager
  systemctl enable --now NetworkManager >/dev/null
  sleep 3
  nmcli device wifi list || true
  local ssid
  ssid=$(ask "Wi-Fi network name (leave empty to skip)" "")
  [[ -n $ssid ]] || return 0
  nmcli --ask device wifi connect "$ssid" < /dev/tty > /dev/tty
  ok "connected to $ssid (reconnects automatically after reboots)"
}
