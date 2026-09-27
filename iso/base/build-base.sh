#!/usr/bin/env bash
# Build the live base system of the MacServer USB image: Debian 13 + the t2linux
# kernel + installer tools, squashed. This is the slow part (~10 minutes), so CI
# caches its output and rebuilds it only when iso/base/, the t2 APT files or the
# t2linux key fingerprint change (or weekly, for security updates).
#
#   sudo iso/base/build-base.sh OUTDIR  -> OUTDIR/{filesystem.squashfs,vmlinuz,initrd.img,kernel-version}
set -Eeuo pipefail

ROOT=$(cd "$(dirname "$0")/../.." && pwd)
OUTDIR=${1:?usage: build-base.sh OUTDIR}
WORK=$(mktemp -d /var/tmp/macserver-base.XXXXXX)
CHROOT=$WORK/chroot
# shellcheck source=installer/lib.sh
source "$ROOT/installer/lib.sh"
[[ $EUID -eq 0 ]] || die "run as root"
mapfile -t PACKAGES < <(grep -v '^\s*\(#\|$\)' "$ROOT/iso/base/packages.txt")

unmount_chroot() {
  local m
  for m in dev/pts dev proc sys run; do umount -l "$CHROOT/$m" 2>/dev/null || true; done
}
cleanup() { unmount_chroot; rm -rf "$WORK"; }
trap cleanup EXIT

head1 "Build tools"
apt-get update -q
DEBIAN_FRONTEND=noninteractive apt-get install -y -q --no-install-recommends \
  debootstrap squashfs-tools curl ca-certificates gpg

head1 "Debian 13 base"
debootstrap --arch=amd64 --variant=minbase --include=ca-certificates,gnupg trixie "$CHROOT" https://deb.debian.org/debian
for m in dev dev/pts proc sys run; do mount --bind "/$m" "$CHROOT/$m"; done
cp /etc/resolv.conf "$CHROOT/etc/resolv.conf"
rm -f "$CHROOT/etc/apt/sources.list"
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

head1 "t2linux repository"
apt_key t2 https://adityagarg8.github.io/t2-ubuntu-repo/KEY.gpg "$T2_KEY_FPR" "$CHROOT/etc/apt/keyrings"
install -m 0644 "$ROOT/host/t2.sources" "$CHROOT/etc/apt/sources.list.d/t2.sources"
install -m 0644 "$ROOT/host/t2.pref" "$CHROOT/etc/apt/preferences.d/t2.pref"

head1 "Live packages"
chroot "$CHROOT" apt-get update -q
chroot "$CHROOT" env DEBIAN_FRONTEND=noninteractive apt-get install -y -q --no-install-recommends "${PACKAGES[@]}"

head1 "Live settings"
L=$ROOT/iso/base/live
echo macserver-installer > "$CHROOT/etc/hostname"
printf '127.0.0.1\tlocalhost\n127.0.1.1\tmacserver-installer\n' > "$CHROOT/etc/hosts"
install -m 0644 "$L/console-setup" "$CHROOT/etc/default/console-setup"
install -m 0644 "$L/issue" "$CHROOT/etc/issue"
install -D -m 0644 "$L/autologin.conf" "$CHROOT/etc/systemd/system/getty@tty1.service.d/autologin.conf"
install -D -m 0644 "$L/serial-autologin.conf" "$CHROOT/etc/systemd/system/serial-getty@ttyS0.service.d/autologin.conf"
install -m 0644 "$L/bash_profile" "$CHROOT/root/.bash_profile"
install -m 0755 "$ROOT/iso/base/macserver-setup-launcher" "$CHROOT/usr/local/sbin/macserver-setup"
chroot "$CHROOT" systemctl enable NetworkManager >/dev/null
make_initrds "$CHROOT"

head1 "Clean up and squash"
chroot "$CHROOT" apt-get clean
rm -rf "$CHROOT"/var/lib/apt/lists/* "$CHROOT"/tmp/* "$CHROOT"/var/log/*.log
: > "$CHROOT/etc/machine-id"
rm -f "$CHROOT/etc/resolv.conf"
unmount_chroot
kernel=$(find "$CHROOT/boot" -maxdepth 1 -name 'vmlinuz-*' | sort -V | tail -1)
initrd=$(find "$CHROOT/boot" -maxdepth 1 -name 'initrd.img-*' | sort -V | tail -1)
[[ -n $kernel && -n $initrd && $kernel == *t2* ]] || die "T2 kernel or initrd missing in the base image"
mkdir -p "$OUTDIR"
cp "$kernel" "$OUTDIR/vmlinuz"
cp "$initrd" "$OUTDIR/initrd.img"
basename "$kernel" > "$OUTDIR/kernel-version"
mksquashfs "$CHROOT" "$OUTDIR/filesystem.squashfs" -comp xz -Xbcj x86 -noappend -quiet
ok "base image ready in $OUTDIR ($(du -sh "$OUTDIR" | cut -f1), $(cat "$OUTDIR/kernel-version"))"
