#!/usr/bin/env bash
# Run the installer test stages locally (WSL2 Debian or any Debian 13 box with KVM).
# The checkout is copied to a Linux file system first; the cached base image in
# $WORK/repo/build/base survives between runs.
#
#   sudo bash tests/local.sh disk    disk installer onto a loop device (~3-8 min)
#   sudo bash tests/local.sh image   assemble the USB image (base built once, ~10 min)
#   sudo bash tests/local.sh vm      install the image in QEMU and boot the result (~5 min)
#   sudo bash tests/local.sh all     all three
#
# MACSERVER_WIFI_FIRMWARE=/path/firmware.tar makes the image stage build a personal
# image with Wi-Fi firmware (see iso/fetch-wifi-firmware.sh); vm tests the newest image.
#
# From Windows:  wsl -d Debian -u root -- bash tests/local.sh all   (in the checkout)
set -euo pipefail
SRC=$(cd "$(dirname "$0")/.." && pwd)
WORK=${MACSERVER_WORK:-/var/tmp/macserver-local}
[[ $EUID -eq 0 ]] || exec sudo -E bash "$0" "$@"

mkdir -p "$WORK/repo"
rsync -a --delete --exclude .git --exclude build --exclude __pycache__ "$SRC/" "$WORK/repo/"
cd "$WORK/repo"

stage() {
  case $1 in
    disk) bash tests/setup_disk_test.sh ;;
    image) bash iso/build.sh local ;;
    vm) bash tests/iso_install_test.sh "$(find build -maxdepth 1 -name 'macserver-local*.iso' -printf '%T@ %p\n' | sort -rn | head -1 | cut -d' ' -f2)" ;;
    *) echo "unknown stage '$1' (disk, image, vm, all)"; exit 2 ;;
  esac
}

if [[ ${1:-all} == all ]]; then
  for s in disk image vm; do
    echo "===== $s"
    start=$SECONDS
    stage "$s"
    echo "===== $s passed in $((SECONDS - start))s"
  done
else
  stage "$1"
fi
