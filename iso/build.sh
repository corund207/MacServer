#!/usr/bin/env bash
# Assemble the MacServer USB installer image (UEFI, hybrid ISO for USB sticks).
#
#   sudo iso/build.sh [VERSION]   -> build/macserver-VERSION.iso (+ .sha256)
#
# MACSERVER_WIFI_FIRMWARE=/path/firmware.tar (from iso/fetch-wifi-firmware.sh) makes
# a PERSONAL image, macserver-VERSION-personal.iso, with Wi-Fi working from the first
# screen. It contains Apple firmware: keep it to yourself, never publish it.
#
# The live base system (Debian 13 + t2linux kernel) comes from build/base; it is
# built by iso/base/build-base.sh when missing (CI restores it from cache). The
# MacServer sources are placed next to it on the image in /macserver, so changing
# them only takes this quick assembly step.
set -Eeuo pipefail

ROOT=$(cd "$(dirname "$0")/.." && pwd)
VERSION=${1:-dev}
OUT=$ROOT/build
BASE=$OUT/base
STAGE=$OUT/stage
FIRMWARE=${MACSERVER_WIFI_FIRMWARE:-}
if [[ -n $FIRMWARE ]]; then ISO=$OUT/macserver-$VERSION-personal.iso; else ISO=$OUT/macserver-$VERSION.iso; fi
# shellcheck source=installer/lib.sh
source "$ROOT/installer/lib.sh"
[[ $EUID -eq 0 ]] || die "run as root"

# Rebuild the base when its inputs changed (the same inputs as CI's cache key).
base_sum=$({ find "$ROOT/iso/base" -type f | sort | xargs cat; cat "$ROOT/host/t2.sources" "$ROOT/host/t2.pref"
             grep "^T2_KEY_FPR=" "$ROOT/installer/lib.sh"; } | sha256sum | cut -c1-16)
if [[ ! -f $BASE/filesystem.squashfs || $(cat "$BASE/inputs.sum" 2>/dev/null) != "$base_sum" ]]; then
  rm -rf "$BASE"
  bash "$ROOT/iso/base/build-base.sh" "$BASE"
  echo "$base_sum" > "$BASE/inputs.sum"
fi

head1 "Image tools"
if ! command -v grub-mkrescue >/dev/null || ! command -v xorriso >/dev/null || ! command -v mformat >/dev/null; then
  apt-get update -q
  DEBIAN_FRONTEND=noninteractive apt-get install -y -q --no-install-recommends \
    xorriso grub-efi-amd64-bin grub-common mtools dosfstools
fi

head1 "Stage"
rm -rf "$STAGE"
mkdir -p "$STAGE/live" "$STAGE/boot/grub" "$STAGE/macserver"
cp "$BASE/filesystem.squashfs" "$BASE/vmlinuz" "$BASE/initrd.img" "$BASE/kernel-version" "$STAGE/live/"
tar -C "$ROOT" --exclude=./build --exclude=./.git --exclude='__pycache__' -cf - . | tar -C "$STAGE/macserver" -xf -
install -m 0644 "$ROOT/iso/grub.cfg" "$STAGE/boot/grub/grub.cfg"
if [[ -n $FIRMWARE ]]; then
  [[ -s $FIRMWARE ]] || die "MACSERVER_WIFI_FIRMWARE: $FIRMWARE not found"
  (( $(tar -tf "$FIRMWARE" | grep -c 'brcmfmac.*apple') > 0 )) || die "$FIRMWARE does not look like renamed Mac Wi-Fi firmware"
  install -D -m 0644 "$FIRMWARE" "$STAGE/firmware/wifi.tar"
  warn "PERSONAL image: it contains Apple Wi-Fi firmware. Do not share or publish it."
fi
echo "MacServer installer $VERSION ($(cat "$BASE/kernel-version"))" > "$STAGE/README.txt"

head1 "ISO image"
rm -f "$ISO"
grub-mkrescue -o "$ISO" "$STAGE" -- -volid MACSERVER
(cd "$OUT" && sha256sum "$(basename "$ISO")" > "$(basename "$ISO").sha256")
ok "built $ISO ($(du -h "$ISO" | cut -f1))"
