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

Verified in CI: all of the above passed (run 36353592396). Verified locally in WSL2
(tests/local.sh): disk, image and vm stages, and FULL_UEFI=1 (boot through the
image's own GRUB menu), in about 5 and 3 minutes.

Found by the tests and fixed: missing initrd when the kernel installs before
initramfs-tools; answer keys with digits ignored; OVMF not connecting drives without
a boot index; **the T2 driver renamed from apple-bce to t2bce_* in kernel 6.18** (the
built-in keyboard would not have worked at the passphrase prompt).

Still needs the real MacBook Air: USB boot with Startup Security set, t2bce keyboard at
the LUKS prompt, Wi-Fi firmware download via get-apple-firmware, iPhone tethering in
the live system, t2fanrd, battery threshold, first-boot Tailscale QR flow.

## 2026-09-27: wrong clock on the Mac breaks the installer's Internet check

Found on the real MacBook Air (personal Wi-Fi image): the Wi-Fi firmware loads and
`nmtui-connect` connects, but the installer said "not connected" because the clock
was wrong (flat battery), so every HTTPS certificate looked invalid. Setting it by
hand from a `curl -sI` Date header failed, most likely because the header ends in a
carriage return that `date -s` rejects.

Fixed:
- `installer/lib.sh`: `sync_clock` gets the time over SNTP from Cloudflare/Google
  NTP by IP (no DNS, no TLS), then from plain-HTTP Date headers; it also writes the
  Mac's hardware clock (`hwclock --systohc --utc`). `https_works` checks Debian over HTTPS.
- `macserver-setup`: sets the clock as soon as there is any route and again before
  debootstrap; says on screen when it corrected the clock; the "not connected"
  menu shows the clock and names certificate errors; new menu item "Set the date
  and time by hand" (local time + time zone). Live image gains `util-linux-extra`
  (hwclock) and `tzdata`.
- Installed system: `util-linux-extra` installed, `systemd-timesyncd` enabled
  explicitly; first-boot `step_network` sets the clock and retries when DNS works
  but HTTPS does not.

Verified locally in WSL2: `tests/run.sh` + ShellCheck 0.11 clean; disk stage (24
checks, now including timesyncd enabled and hwclock present); personal image
rebuilt with a fresh base; FULL_UEFI=1 VM test with the VM clock at 2019-06-01.
Observed: systemd moves a pre-build clock up to its build date (2026-04-13), so the
live system woke about five months behind; the installer printed "Clock was wrong
... set from the network" and the install finished. The VM test now requires that line.

Not verified yet: the new image on the real Mac (does its Wi-Fi network pass NTP on
UDP 123 or plain HTTP?), and the manual clock menu (only exercised by hand-reading).

## 2026-09-28: always-on console dashboard

Found on the real Mac: after install and first login the owner got a bare shell
prompt, and the screen went black after 2 minutes (`consoleblank=120`).

Now: tty1 autologins the unprivileged `macserver-console` account, whose login
shell is `admin/dashboard.sh --kiosk` (read-only, reads `status.json`, ignores keys,
Ctrl+C trapped). It shows setup state, health, addresses, the docs link and how to
log in (Ctrl + Option + F2). `consoleblank=0` plus `setterm --blank 0 --powerdown 0`
keep the screen on. Logging in prints the dashboard once (`/etc/profile.d/macserver.sh`);
`macserver dashboard` is the live view. The status collector adds `setup_done`.
Installed by `install_console` at the end of the `host` step.

Verified: `tests/run.sh` (dashboard renders fixtures, hides keys, shows "Setup has
not finished" without data), ShellCheck; VM test phase 2 now runs first boot through
the host step on a real systemd boot; in WSL, the exact agetty line autologs a test
account into the kiosk dashboard on a pseudo-terminal.

Not verified: the dashboard on the Mac's physical tty1 (font, layout at 16x32), and
VT switching with the Mac keyboard's Ctrl + Option + F2.

## 2026-09-28: Claude Code on the server, with a MacServer skill

New install step `claude` (after `admin`): downloads the native Claude Code binary
(`@anthropic-ai/claude-code-linux-x64` 2.1.284 from the npm registry), refuses it
unless the tarball's SHA-512 matches the pin in `installer/lib.sh`, installs it to
`/usr/local/lib/claude-code/<version>` with `/usr/local/bin/claude`, and turns the
self-updater off (`DISABLE_AUTOUPDATER=1` in `/etc/environment`). It installs the
`macserver` skill (`host/claude/SKILL.md`) to the owner's `~/.claude/skills/` and a
`~/macserver-workspace` with a CLAUDE.md. The USB installer now records
`ADMIN_USER` in macserver.conf; `admin_user` falls back to SUDO_USER or the sudo group.

Verified in WSL (isolated mount namespace): download + checksum + install,
idempotent re-run, skill owned by the owner, and a wrong checksum refuses to install;
the binary reports 2.1.284. Repo test checks the pin format and the checksum call.

Not verified: signing in on the Mac (needs the owner's claude.ai account), and
that Claude Code picks up the skill there.

## 2026-09-28: "Start Claude session" on the admin page

The admin page gains one action (see docs/SECURITY.md): an allowlisted, same-origin
JSON POST to `/api/claude` writes `start`/`stop` to `/run/macserver-admin/claude-request`
(the admin unit's RuntimeDirectory). Root-owned `macserver-claude-control.path` runs
`claude-control`, which starts/stops `macserver-claude.service`: `claude remote-control
--name "<host> MacServer"` as the owner in `~/macserver-workspace`, inside `script` for
a 400-column pty, log at `/run/macserver-claude/session.log` (0600, kept after stop).
The collector reports `claude` (installed, state, link, problem: login/consent); the
page shows Start / Open session / Stop and what to do when sign-in or the one-time
Remote Control consent is missing. The console dashboard shows the session state.

Verified: 8 admin server tests (accepted start/stop; refused without login, wrong
login, bad action, foreign Origin, cross-site, non-JSON), collector parsing test,
dashboard fixture; end to end on WSL's systemd with a stand-in `claude`: step_claude
installs the units, the real admin server's POST starts the service as the owner in
the workspace on a 60x400 pty, the collector reports the link, stop works, and a
symlinked request file is ignored (17 checks, all cleaned up afterwards).

Not verified: the real `claude remote-control` output format under `script` (the
collector looks for a claude.ai/code link; the page otherwise says to find the
session at claude.ai/code), the page in a browser over `tailscale serve`, and whether
`tailscale serve` passes an `Origin` header matching `https://<TAILNET_NAME>`.
