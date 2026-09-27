# Status

## 2026-09-27: rebuilt from scratch for T2

The previous project (a staged, documentation-heavy Debian design without T2 support)
was removed from `main`. Its history remains in git. It was replaced by one guided
installer built around the MacBook Air's T2 chip.

### Contents

- `install.sh` + `installer/`: 14 resumable steps (preflight, network, base, t2, host,
  reboot, wifi, tailscale, firewall, docker, supabase, admin, public, finish).
- `macserver`: status, keys, logs, restart, update, backup, public on/off/setup, wifi, doctor.
- `host/`: firewall, Docker daemon, APT sources and pins, GRUB T2 options, logind,
  journald, sysctl, battery limit unit.
- `admin/`: root status collector plus a read-only admin page behind `tailscale serve`.
- `gateway/`: Caddy path filter plus cloudflared (pinned by digest).
- Pins: Supabase `self-hosted/v0.8.2` = `47111f95…`; APT key fingerprints for
  t2linux, Docker and Tailscale; Caddy 2.11.4, cloudflared 2026.9.3.

### Verified

- On the Windows development PC: `bash -n` on all scripts, ShellCheck 0.11 (clean),
  `tests/test_lib.sh` (with Windows-only mode changes stubbed), and 22 Python tests
  (admin authorization and live HTTP, collector parsing, repository invariants).
- Upstream facts checked on 2026-09-27: t2linux trixie repo layout and packages
  (`linux-t2-lts`, `apple-firmware-script`, `t2fanrd`), `get-apple-firmware`
  subcommands, Supabase v0.8.2 compose (`name: supabase`, `api-gw` Envoy on 8000,
  `supabase-db`), and key fingerprints.

### Not verified yet (needs the real Mac and CI)

- The whole install on the MacBook Air: T2 kernel boot, the T2 keyboard driver in the initramfs,
  Wi-Fi firmware download, the battery threshold file, t2fanrd.
- `tests/gateway_test.sh` (needs Docker; runs in CI) and Compose validation.
- `pg_dumpall` as `supabase_admin` over TCP inside `supabase-db`.
- `tailscale serve` HTTPS on the user's tailnet.

### Next

Run the installer on the Mac, then record what was confirmed or corrected here.

## 2026-09-27: USB installer image (Omarchy-style)

`iso/` builds a bootable UEFI image: live Debian 13 with the t2linux kernel that
starts `macserver-setup` (whiptail questions, types-ERASE confirmation, LUKS2 +
ext4, debootstrap, T2 kernel, GRUB at the removable EFI path without NVRAM writes,
NetworkManager/Wi-Fi carry-over, first-boot `install.sh --unattended`).

Test pipeline (`installer-image` workflow, ~7 minutes per change):
- `disk-install` (~3 min): runs `macserver-setup` hands-off onto a 32 GB loop device
  and checks 22 properties of the result (LUKS2, T2 kernel + initrd with
  t2bce_vhci/t2bce_core, BOOTX64.EFI, GRUB T2 options, crypttab/fstab, user, locked
  root, first-boot unit, NetworkManager, MacServer files).
- `image` (~30 s with cache): base squashfs cached weekly/by inputs; the MacServer
  sources sit beside it on the image (`/macserver`), copied in by the launcher.
- `vm-test` (~4 min): direct kernel boot of the image in QEMU/KVM with an MSANSWERS
  drive, hands-off install to NVMe, then UEFI boot of the result, passphrase over
  serial, first-boot preflight + network. Release tags also boot the image's own
  UEFI GRUB menu (FULL_UEFI=1).

Verified in CI: all of the above passed (run 36353592396).

Found by the tests and fixed: missing initrd when the kernel installs before
initramfs-tools; answer keys with digits ignored; OVMF not connecting drives without
a boot index; **the T2 driver renamed from apple-bce to t2bce_* in kernel 6.18** (the
built-in keyboard would not have worked at the passphrase prompt).

Still needs the real MacBook Air: USB boot with Startup Security set, t2bce keyboard at
the LUKS prompt, Wi-Fi firmware download via get-apple-firmware, iPhone tethering in
the live system, t2fanrd, battery threshold, first-boot Tailscale QR flow.
