#!/usr/bin/env bash
# Fast test of the disk installer (~10 minutes): run macserver-setup hands-off
# against a 32 GB loop device on this Linux machine (no image build, no VM), then
# open the encrypted result and check what a Mac would boot.
# Run as root on a disposable CI machine: it installs packages and uses loop devices.
set -Eeuo pipefail
ROOT=$(cd "$(dirname "$0")/.." && pwd)
[[ $EUID -eq 0 ]] || exec sudo -E bash "$0" "$@"
# shellcheck source=installer/lib.sh
source "$ROOT/installer/lib.sh"
PASS=ci-passphrase-123
W=$(mktemp -d /var/tmp/macserver-disk.XXXXXX)
LOOP=''

cleanup() {
  umount -R "$W/root" 2>/dev/null || true
  umount -R /mnt/target 2>/dev/null || true
  cryptsetup close check 2>/dev/null || true
  cryptsetup close cryptroot 2>/dev/null || true
  [[ -n $LOOP ]] && losetup -d "$LOOP" 2>/dev/null
  rm -rf "$W"
}
trap cleanup EXIT

head1 "Tools"
apt-get update -q
DEBIAN_FRONTEND=noninteractive apt-get install -y -q --no-install-recommends \
  debootstrap debian-archive-keyring gdisk parted dosfstools e2fsprogs cryptsetup \
  whiptail curl jq gpg initramfs-tools-core >/dev/null
apt_key t2 https://adityagarg8.github.io/t2-ubuntu-repo/KEY.gpg "$T2_KEY_FPR"

head1 "Fake 32 GB disk"
truncate -s 32G "$W/disk.img"
LOOP=$(losetup -fP --show "$W/disk.img")
say "using $LOOP"
cat > "$W/answers.txt" <<EOF
DISK=$LOOP
CONFIRM_ERASE=$LOOP
HOSTNAME=ci-macserver
USERNAME=ci
PASSWORD=ci-password
ENCRYPT=yes
LUKS_PASSPHRASE=$PASS
TIMEZONE=Europe/London
GET_WIFI_FIRMWARE=no
ALLOW_NON_T2=yes
EOF

head1 "Run the installer"
if ! MACSERVER_SRC=$ROOT MACSERVER_ANSWERS=$W/answers.txt bash "$ROOT/iso/setup/macserver-setup"; then
  mkdir -p "$ROOT/build"; cp /tmp/macserver-setup.log "$ROOT/build/" 2>/dev/null || true
  die "installer failed (log: build/macserver-setup.log)"
fi
mkdir -p "$ROOT/build"; cp /tmp/macserver-setup.log "$ROOT/build/" 2>/dev/null || true

head1 "Inspect the installed disk"
fails=0
check_that() {  # check_that "description" command...
  local d=$1; shift
  if "$@"; then ok "$d"; else warn "FAILED: $d"; fails=$((fails + 1)); fi
}

partprobe "$LOOP" 2>/dev/null || true; udevadm settle
check_that "root partition is LUKS2" cryptsetup isLuks --type luks2 "${LOOP}p3"
printf '%s' "$PASS" | cryptsetup open --key-file - "${LOOP}p3" check
mkdir -p "$W/root"
mount /dev/mapper/check "$W/root"
mount "${LOOP}p2" "$W/root/boot"
mount "${LOOP}p1" "$W/root/boot/efi"
R=$W/root

kver=$(basename "$(find "$R/boot" -maxdepth 1 -name 'vmlinuz-*t2*' | sort -V | tail -1)")
kver=${kver#vmlinuz-}
check_that "T2 kernel installed ($kver)" test -n "$kver"
check_that "initrd for the T2 kernel" test -s "$R/boot/initrd.img-$kver"
lsinitramfs "$R/boot/initrd.img-$kver" > "$W/initrd.list" 2>/dev/null || true
check_that "initrd has the T2 keyboard driver (built-in keyboard at the passphrase prompt)" grep -q 't2bce_vhci' "$W/initrd.list"
check_that "initrd has the T2 driver core" grep -q 't2bce_core' "$W/initrd.list"
check_that "initrd has cryptsetup" grep -q 'sbin/cryptsetup' "$W/initrd.list"
check_that "UEFI loader at the removable path the Mac boots" test -s "$R/boot/efi/EFI/BOOT/BOOTX64.EFI"
check_that "GRUB passes the T2 kernel options" grep -q 'intel_iommu=on iommu=pt pcie_ports=compat' "$R/boot/grub/grub.cfg"
check_that "GRUB boots the T2 kernel" grep -q "vmlinuz-$kver" "$R/boot/grub/grub.cfg"
check_that "crypttab names the LUKS partition" grep -q "^cryptroot UUID=$(blkid -s UUID -o value "${LOOP}p3") none luks" "$R/etc/crypttab"
check_that "fstab mounts /, /boot and /boot/efi" test "$(grep -c '^UUID=' "$R/etc/fstab")" = 3
check_that "user ci exists and can sudo" grep -q '^sudo:.*\bci\b' "$R/etc/group"
check_that "root login is locked" grep -q '^root:!' "$R/etc/shadow"
check_that "hostname set" grep -qx ci-macserver "$R/etc/hostname"
check_that "time zone set" test "$(readlink "$R/etc/localtime")" = /usr/share/zoneinfo/Europe/London
check_that "first-boot setup enabled" test -L "$R/etc/systemd/system/multi-user.target.wants/macserver-firstboot.service"
check_that "NetworkManager enabled" test -L "$R/etc/systemd/system/multi-user.target.wants/NetworkManager.service"
check_that "network time sync enabled" test -L "$R/etc/systemd/system/sysinit.target.wants/systemd-timesyncd.service"
check_that "hwclock installed" test -x "$R/usr/sbin/hwclock"
check_that "MacServer sources copied" test -x "$R/opt/macserver-src/install.sh"
check_that "MacServer config written" grep -q '^HOSTNAME=ci-macserver' "$R/etc/macserver/macserver.conf"
check_that "t2linux repo pinned to T2 packages" test -f "$R/etc/apt/preferences.d/t2.pref"
check_that "no Tailscale key left behind" test ! -e "$R/etc/macserver/tailscale-authkey"
check_that "installer log saved privately" test "$(stat -c %a "$R/var/log/macserver-setup.log")" = 600

(( fails == 0 )) || die "$fails check(s) failed"
ok "disk installer test passed"
