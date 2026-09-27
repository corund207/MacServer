#!/usr/bin/env bash
# End-to-end test of the USB installer image in QEMU, failing fast with the console
# streamed into the log:
#  1. boot the image with a hands-off answers drive; it installs an encrypted
#     Debian 13 + T2 kernel + MacServer on a virtual NVMe disk and powers off;
#  2. boot the installed disk under UEFI, type the passphrase on the serial console
#     and wait for MacServer's first-boot setup to pass preflight and network.
#
#   tests/iso_install_test.sh IMAGE.iso            quick: phase 1 boots the kernel directly
#   FULL_UEFI=1 tests/iso_install_test.sh IMAGE.iso phase 1 boots the image's own UEFI menu
#
# Needs: qemu-system-x86, qemu-utils, ovmf, mtools, dosfstools, xorriso, python3, /dev/kvm.
set -euo pipefail
ISO=$(realpath "$1")
ROOT=$(cd "$(dirname "$0")/.." && pwd)
W=$(mktemp -d)
PASS=ci-passphrase-123
CODE=/usr/share/OVMF/OVMF_CODE_4M.fd
T2OPTS="intel_iommu=on iommu=pt pcie_ports=compat"
mkdir -p "$ROOT/build"
cp /usr/share/OVMF/OVMF_VARS_4M.fd "$W/vars.fd"
qemu-img create -q -f qcow2 "$W/disk.qcow2" 40G

cat > "$W/answers.txt" <<ANS
DISK=/dev/nvme0n1
CONFIRM_ERASE=/dev/nvme0n1
HOSTNAME=ci-macserver
USERNAME=ci
PASSWORD=ci-password
ENCRYPT=yes
LUKS_PASSPHRASE=$PASS
TIMEZONE=UTC
GET_WIFI_FIRMWARE=no
ALLOW_NON_T2=yes
EXTRA_CMDLINE=console=tty0 console=ttyS0,115200
POWEROFF=yes
ANS
mkfs.fat -C -n MSANSWERS "$W/answers.img" 8192 >/dev/null
mcopy -i "$W/answers.img" "$W/answers.txt" ::answers.txt

vm() {  # vm LOG_NAME QEMU_ARGS...: run QEMU in the background with its serial on a socket
  local name=$1; shift
  rm -f "$W/serial.sock"
  qemu-system-x86_64 -enable-kvm -machine q35 -cpu host -m 6144 -smp 2 \
    -drive file="$W/disk.qcow2",if=none,id=nvm -device nvme,serial=ci0,drive=nvm,bootindex=1 \
    -nic user,model=virtio-net-pci -display none -no-reboot \
    -chardev socket,id=s0,path="$W/serial.sock",server=on,wait=off -serial chardev:s0 \
    "$@" > "$W/$name.qemu.log" 2>&1 &
  echo $!
}

watch_console() {  # watch_console LOG STEP...: on failure also show QEMU's own messages
  local log=$1 rc=0; shift
  python3 "$ROOT/tests/qemu_serial.py" --sock "$W/serial.sock" --log "$ROOT/build/$log" \
    --fail MACSERVER-SETUP-FAILED --fail "Kernel panic" --fail "failed (exit" "$@" || rc=$?
  if (( rc )); then
    echo "--- QEMU messages"
    cat "$W"/*.qemu.log 2>/dev/null || true
    cp "$W"/*.qemu.log "$ROOT/build/" 2>/dev/null || true
  fi
  return "$rc"
}

echo "== Phase 1: hands-off install from the image"
# shellcheck disable=SC2054  # commas belong to the QEMU options
cd_args=(-drive file="$ISO",media=cdrom,if=none,id=cd -device ide-cd,drive=cd,bootindex=0
         -drive file="$W/answers.img",format=raw,if=none,id=ans -device virtio-blk-pci,drive=ans,bootindex=2)
if [[ ${FULL_UEFI:-0} == 1 ]]; then
  qpid=$(vm install -drive if=pflash,format=raw,readonly=on,file="$CODE" -drive if=pflash,format=raw,file="$W/vars.fd" "${cd_args[@]}")
else
  xorriso -osirrox on -indev "$ISO" -extract /live/vmlinuz "$W/vmlinuz" -extract /live/initrd.img "$W/initrd.img" 2>&1 | tail -3
  [[ -s $W/vmlinuz && -s $W/initrd.img ]] || { echo "could not extract the kernel from the image"; exit 1; }
  qpid=$(vm install -kernel "$W/vmlinuz" -initrd "$W/initrd.img" \
    -append "boot=live $T2OPTS console=ttyS0,115200 macserver.auto=1" "${cd_args[@]}")
fi
watch_console install.log \
  "Hands-off install@300" \
  "[ 10%]@600" \
  "[ 55%]@1200" \
  "MACSERVER-SETUP-DONE@1200"
for _ in $(seq 60); do kill -0 "$qpid" 2>/dev/null || break; sleep 1; done
kill "$qpid" 2>/dev/null || true

echo "== Phase 2: boot the installed system (UEFI) and unlock the disk"
qpid=$(vm boot -drive if=pflash,format=raw,readonly=on,file="$CODE" -drive if=pflash,format=raw,file="$W/vars.fd")
set +e
watch_console boot.log \
  "Please unlock disk=>$PASS@300" \
  "MacServer installer@300" \
  "Debian 13 x86_64, UEFI@120" \
  "Internet and DNS work@180"
rc=$?
set -e
kill "$qpid" 2>/dev/null || true
[[ $rc == 0 ]] && echo "ISO install test passed"
exit "$rc"
