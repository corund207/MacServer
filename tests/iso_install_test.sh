#!/usr/bin/env bash
# End-to-end test of the USB installer image in QEMU (UEFI, NVMe disk, KVM):
#  1. boot the ISO with a hands-off answers drive; it erases the virtual NVMe disk
#     and installs an encrypted Debian 13 + T2 kernel + MacServer, then powers off;
#  2. boot the installed disk, type the passphrase on the serial console, and wait
#     for MacServer's first-boot setup to pass its preflight and network steps.
# Needs: qemu-system-x86, ovmf, mtools, dosfstools, python3, /dev/kvm.
set -euo pipefail
ISO=$(realpath "$1")
ROOT=$(cd "$(dirname "$0")/.." && pwd)
W=$(mktemp -d)
PASS=ci-passphrase-123
CODE=/usr/share/OVMF/OVMF_CODE_4M.fd
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

qemu() {
  qemu-system-x86_64 -enable-kvm -machine q35 -cpu host -m 6144 -smp 2 \
    -drive if=pflash,format=raw,readonly=on,file="$CODE" \
    -drive if=pflash,format=raw,file="$W/vars.fd" \
    -drive file="$W/disk.qcow2",if=none,id=nvm -device nvme,serial=ci0,drive=nvm,bootindex=1 \
    -nic user,model=virtio-net-pci -display none -no-reboot "$@"
}

echo "== Phase 1: hands-off install from the ISO"
qemu -drive file="$ISO",media=cdrom,if=none,id=cd -device ide-cd,drive=cd,bootindex=0 \
  -drive file="$W/answers.img",format=raw,if=virtio \
  -serial file:"$W/install.log" &
qpid=$!
sleep 2
tail -n +1 -F --pid="$qpid" "$W/install.log" 2>/dev/null | tr -d "\r" | sed -u "s/^/[vm] /" &
for ((waited = 0; waited < 4500; waited += 10)); do
  kill -0 "$qpid" 2>/dev/null || break
  sleep 10
done
kill "$qpid" 2>/dev/null || true
wait "$qpid" 2>/dev/null || true
mkdir -p "$ROOT/build"; cp "$W/install.log" "$ROOT/build/" 2>/dev/null || true
grep -q MACSERVER-SETUP-DONE "$W/install.log" || { echo "install did not finish"; exit 1; }

echo "== Phase 2: boot the installed system and unlock the disk"
qemu -chardev socket,id=s0,path="$W/serial.sock",server=on,wait=off -serial chardev:s0 &
qpid=$!
set +e
python3 "$ROOT/tests/qemu_serial.py" "$W/serial.sock" "$W/boot.log" 1500 \
  "Please unlock disk=>$PASS" \
  "MacServer installer" \
  "Debian 13 x86_64, UEFI" \
  "Internet and DNS work"
rc=$?
set -e
kill "$qpid" 2>/dev/null || true
tail -n 40 "$W/boot.log" | tr -d '\r'
mkdir -p "$ROOT/build"; cp "$W/install.log" "$W/boot.log" "$ROOT/build/" 2>/dev/null || true
[[ $rc == 0 ]] && echo "ISO install test passed"
exit "$rc"
