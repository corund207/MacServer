#!/usr/bin/env bash
# Build the MacServer USB installer image (UEFI, hybrid ISO for USB sticks).
#
#   sudo iso/build.sh [VERSION]      -> build/macserver-VERSION.iso (+ .sha256)
#
# Runs as root on Debian 13 (CI uses a privileged debian:trixie container). The
# image is a live Debian 13 with the t2linux kernel, so the Mac's keyboard,
# trackpad and (with firmware) Wi-Fi work while installing. It boots straight
# into macserver-setup.
set -Eeuo pipefail

ROOT=$(cd "$(dirname "$0")/.." && pwd)
VERSION=${1:-dev}
OUT=$ROOT/build
WORK=$OUT/work
CHROOT=$WORK/chroot
STAGE=$WORK/iso
ISO=$OUT/macserver-$VERSION.iso
# shellcheck source=installer/lib.sh
source "$ROOT/installer/lib.sh"

[[ $EUID -eq 0 ]] || die "run as root"

LIVE_PACKAGES=(
  linux-t2-lts live-boot systemd-sysv udev dbus kmod sudo
  network-manager wpasupplicant iw usbmuxd libimobiledevice-utils
  debootstrap debian-archive-keyring gdisk parted dosfstools e2fsprogs cryptsetup
  whiptail curl ca-certificates gnupg jq python3 apple-firmware-script dmg2img
  kbd console-setup locales less nano pciutils usbutils iproute2 iputils-ping
  procps util-linux tar xz-utils zstd firmware-realtek firmware-linux-free
)

unmount_chroot() {
  local m
  for m in dev/pts dev proc sys run; do umount -l "$CHROOT/$m" 2>/dev/null || true; done
}
trap unmount_chroot EXIT

head1 "Build tools"
apt-get update -q
DEBIAN_FRONTEND=noninteractive apt-get install -y -q --no-install-recommends \
  debootstrap squashfs-tools xorriso grub-efi-amd64-bin grub-common mtools dosfstools \
  curl ca-certificates gpg

head1 "Base system"
unmount_chroot
rm -rf "$WORK"
mkdir -p "$CHROOT" "$STAGE/live" "$STAGE/boot/grub"
debootstrap --arch=amd64 --variant=minbase --include=ca-certificates,gnupg trixie "$CHROOT" https://deb.debian.org/debian
for m in dev dev/pts proc sys run; do mount --bind "/$m" "$CHROOT/$m"; done
cp /etc/resolv.conf "$CHROOT/etc/resolv.conf"

cat > "$CHROOT/etc/apt/sources.list.d/debian.sources" <<'EOF'
Types: deb
URIs: https://deb.debian.org/debian
Suites: trixie trixie-updates
Components: main contrib non-free-firmware
Signed-By: /usr/share/keyrings/debian-archive-keyring.gpg

Types: deb
URIs: https://security.debian.org/debian-security
Suites: trixie-security
Components: main contrib non-free-firmware
Signed-By: /usr/share/keyrings/debian-archive-keyring.gpg
EOF
rm -f "$CHROOT/etc/apt/sources.list"

head1 "t2linux repository (key fingerprint $T2_KEY_FPR)"
tmp=$(mktemp -d)
curl -fsSL --compressed https://adityagarg8.github.io/t2-ubuntu-repo/KEY.gpg -o "$tmp/key"
got=$(gpg --show-keys --with-colons "$tmp/key" 2>/dev/null | awk -F: '/^fpr:/ { print $10; exit }')
[[ $got == "$T2_KEY_FPR" ]] || die "t2linux key fingerprint mismatch: $got"
install -d "$CHROOT/etc/apt/keyrings"
gpg --dearmor < "$tmp/key" > "$CHROOT/etc/apt/keyrings/t2.gpg" 2>/dev/null || cp "$tmp/key" "$CHROOT/etc/apt/keyrings/t2.gpg"
rm -rf "$tmp"
install -m 0644 "$ROOT/host/t2.sources" "$CHROOT/etc/apt/sources.list.d/t2.sources"
install -m 0644 "$ROOT/host/t2.pref" "$CHROOT/etc/apt/preferences.d/t2.pref"

head1 "Live packages"
chroot "$CHROOT" apt-get update -q
chroot "$CHROOT" env DEBIAN_FRONTEND=noninteractive apt-get install -y -q --no-install-recommends "${LIVE_PACKAGES[@]}"

head1 "Installer and live settings"
echo macserver-installer > "$CHROOT/etc/hostname"
printf '127.0.0.1\tlocalhost\n127.0.1.1\tmacserver-installer\n' > "$CHROOT/etc/hosts"
install -m 0644 "$ROOT/iso/live/console-setup" "$CHROOT/etc/default/console-setup"
install -m 0644 "$ROOT/iso/live/issue" "$CHROOT/etc/issue"
install -D -m 0644 "$ROOT/iso/live/autologin.conf" "$CHROOT/etc/systemd/system/getty@tty1.service.d/autologin.conf"
install -D -m 0644 "$ROOT/iso/live/serial-autologin.conf" "$CHROOT/etc/systemd/system/serial-getty@ttyS0.service.d/autologin.conf"
install -m 0644 "$ROOT/iso/live/bash_profile" "$CHROOT/root/.bash_profile"
install -m 0755 "$ROOT/iso/setup/macserver-setup" "$CHROOT/usr/local/sbin/macserver-setup"
install -d "$CHROOT/opt/macserver-src"
tar -C "$ROOT" --exclude=./build --exclude=./.git --exclude='__pycache__' -cf - . | tar -C "$CHROOT/opt/macserver-src" -xf -
chroot "$CHROOT" systemctl enable NetworkManager >/dev/null
chroot "$CHROOT" update-initramfs -u -k all

head1 "Clean up"
chroot "$CHROOT" apt-get clean
rm -rf "$CHROOT"/var/lib/apt/lists/* "$CHROOT"/tmp/* "$CHROOT"/var/log/*.log
: > "$CHROOT/etc/machine-id"
rm -f "$CHROOT/etc/resolv.conf"
unmount_chroot

head1 "Squash file system"
kernel=$(find "$CHROOT/boot" -maxdepth 1 -name 'vmlinuz-*' | sort -V | tail -1)
initrd=$(find "$CHROOT/boot" -maxdepth 1 -name 'initrd.img-*' | sort -V | tail -1)
[[ -n $kernel && -n $initrd && $kernel == *t2* ]] || die "T2 kernel or initrd missing in the image"
cp "$kernel" "$STAGE/live/vmlinuz"
cp "$initrd" "$STAGE/live/initrd.img"
mksquashfs "$CHROOT" "$STAGE/live/filesystem.squashfs" -comp xz -Xbcj x86 -noappend -quiet
basename "$kernel" > "$STAGE/live/kernel-version"

head1 "ISO image"
install -m 0644 "$ROOT/iso/grub.cfg" "$STAGE/boot/grub/grub.cfg"
echo "MacServer installer $VERSION" > "$STAGE/README.txt"
grub-mkrescue -o "$ISO" "$STAGE" -- -volid MACSERVER
(cd "$OUT" && sha256sum "$(basename "$ISO")" > "$(basename "$ISO").sha256")
ok "built $ISO ($(du -h "$ISO" | cut -f1)) with $(basename "$kernel")"
