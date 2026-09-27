#!/usr/bin/env bash
# Fetch the Mac Wi-Fi/Bluetooth firmware from Apple and pack it for Linux, so a
# personal MacServer image can use Wi-Fi from the first screen.
#
#   sudo iso/fetch-wifi-firmware.sh OUT.tar
#
# Downloads a macOS Sonoma Recovery image from Apple (~750 MB, with kholia/OSX-KVM's
# fetch-macOS-v2.py), extracts /usr/share/firmware with 7-Zip (no HFS+ driver needed),
# and renames the files for brcmfmac with the renamer from t2linux's firmware.sh.
# The resulting archive holds Apple firmware, which may not be redistributed: use it
# only for your own Mac and never publish an image built with it.
set -Eeuo pipefail
OUT=$(realpath -m "${1:?usage: fetch-wifi-firmware.sh OUT.tar}")
W=$(mktemp -d /var/tmp/macserver-fw.XXXXXX)
trap 'rm -rf "$W"' EXIT
[[ $EUID -eq 0 ]] || exec sudo -E bash "$0" "$@"

echo "==> Tools"
DEBIAN_FRONTEND=noninteractive apt-get install -y -q --no-install-recommends 7zip python3 curl ca-certificates >/dev/null

echo "==> Downloading macOS Sonoma Recovery from Apple (about 750 MB)"
cd "$W"
curl -fsSL -o fetch-macOS-v2.py https://raw.githubusercontent.com/kholia/OSX-KVM/master/fetch-macOS-v2.py
curl -fsSL -o firmware.sh https://wiki.t2linux.org/tools/firmware.sh
python3 fetch-macOS-v2.py -s sonoma > download.log 2>&1 || { tail -5 download.log; exit 1; }
[[ -s BaseSystem.dmg ]] || { echo "download failed"; exit 1; }

echo "==> Extracting the firmware"
7z x BaseSystem.dmg -oextract 'macOS Base System/usr/share/firmware/*' -r -y > /dev/null
fw="$W/extract/macOS Base System/usr/share/firmware"
[[ -d $fw/wifi ]] || { echo "no firmware found in the recovery image"; exit 1; }

echo "==> Renaming for Linux (t2linux renamer)"
# firmware.sh only offers its renamer on macOS; it is a self-contained Python
# program embedded in the script, so run it directly.
awk 'f && /^EOF$/ { exit } f { print } /^python3 - "\$@" <<.EOF.$/ { f = 1 }' firmware.sh > rename.py
[[ -s rename.py ]] || { echo "could not find the renamer in firmware.sh"; exit 1; }
python3 rename.py "$fw" "$OUT"
[[ -s $OUT ]] || { echo "renaming produced no archive"; exit 1; }
chmod 0600 "$OUT"
echo "==> $OUT: $(tar -tf "$OUT" | wc -l) files ($(du -h "$OUT" | cut -f1)). Personal use only; do not publish."
