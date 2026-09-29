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
- Pins: Supabase `self-hosted/v0.8.2` = commit `564eab8a…` (was wrongly the tag object `47111f95…` until 2026-09-28); APT key fingerprints for
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

## 2026-09-28: designed dashboard TUI (replaces the text dashboard)

`admin/tui.py` (Python stdlib curses, installed as `/usr/local/lib/macserver/dashboard`)
replaces `admin/dashboard.sh`: header with an overall status badge and clock; SYSTEM
bars (CPU, memory, disk, temperature + fan, battery); CONNECTIONS with OK/WARN/DOWN/OFF
badges (Internet + Wi-Fi signal, Tailscale + devices online, public API reachability,
Claude session, clock sync, updates); SUPABASE container dots; six area charts
(CPU, memory, temperature, network in/out, battery; 10 s per column, up to 2 h kept
in memory); addresses and how to log in. Adapts from 160x50 down to 40x12. Live
numbers come from /proc and /sys every 2 s; the rest from status.json, which gains
`tailscale.peers_online`, `public_ok` and `clock_synced`.

The Mac's console font limits the characters: none of Debian's console fonts have
the ▁▂▃ sparkline blocks, ○ or ✓. The dashboard draws only `tui.GLYPHS`, which
`tests/test_dashboard.py` checks against Lat15-Terminus32x16 (CI installs
console-setup-linux for it). `CODESET="Lat15"` is now explicit.

Verified: 8 dashboard tests (3 scenarios x 5 sizes, only font characters, no keys on
screen, setup-running is not an error, chart and scale maths, `--once`), collector
tests; `tests/tui_preview.py` renders the screen to PNG with the real font and
console palette (docs/images/dashboard.png) and the layout was reviewed from those
renders; the kiosk ran through the real agetty autologin with TERM=linux, kept
redrawing and ignored Ctrl+C, Ctrl+Z, q and Ctrl+D.

Not verified: on the Mac's physical console (real colours/brightness of the Retina
panel), and hwmon names/Wi-Fi signal on the MacBook Air itself.

## 2026-09-28: one simple guide; one-command personal USB image

`docs/GUIDE.md` is now the single step-by-step path, in plain language: Tailscale,
make the USB stick, Startup Security, install (Wi-Fi picker, clock, questions,
ERASE), first start (passphrase, QR code, the dashboard explained), admin page and
Studio, connecting an app (URL + publishable key, public route, RLS), Claude,
everyday commands, updating an installed Mac, and a problem table. README is a
short front page with the dashboard screenshot. TROUBLESHOOTING gains the
"not connected"/clock, black screen, setup-not-finished, missing dashboard and
Claude cases. Note: no release is published, so the guide builds the personal image.

`iso\make-personal-usb.ps1` builds the personal image from Windows in one command
(WSL check, rsync, Apple Wi-Fi firmware once, image, copy to Downloads with a
SHA-256 check). `iso/build.sh` now rebuilds the cached base when its inputs change.

Verified: the script ran end to end on this PC (after fixing a helper that shadowed
`wsl` and stderr handling in Windows PowerShell); it rebuilt the base with
CODESET=Lat15. Guide anchors checked against headings.

## 2026-09-28: Supabase pin fixed (found on the real Mac)

On the Mac, `sudo bash install.sh` stopped at the supabase step: "does not match the
pinned commit". `SUPABASE_COMMIT` held the annotated tag object (`47111f95…`), but
the installer compares the clone's HEAD, which is the tagged commit (`564eab8a…`).
The step could never pass anywhere, which is also why first boot on the Mac ended at
a bare login instead of a finished server. No test reached the supabase step (the VM
test stops after host). Now pinned to the commit, and CI checks with `git ls-remote`
that the pin equals `refs/tags/$SUPABASE_REF^{}`. Checked at the tag: docker/.env.example,
utils/generate-keys.sh and add-new-auth-keys.sh (`--update-env`), run.sh start.

Not verified: the rest of the supabase step and the steps after it (admin, claude,
public) on real hardware; they have not run end to end anywhere yet.

## 2026-09-28: btop-style dashboard in a kiosk terminal

The owner asked for the screen to look like btop, high resolution. The bare Linux
console cannot (16 colours, no braille in any console font), so tty1 now runs a
kiosk: `host/macserver-console` starts `cage -s` (Wayland kiosk, pixman software
rendering, VT switching kept) with `foot` (`host/foot.ini`: JetBrains Mono at
`DASHBOARD_FONT_PX`, default 18 px = about 232x64 cells on 2560x1600; key bindings
for new terminals, URL launching and pasting disabled) running `dashboard --kiosk`.
If cage fails at once, the same account falls back to the text-console dashboard.
The host step installs `cage foot fonts-jetbrains-mono` (Debian main).

`admin/tui.py` rewritten without curses: its own truecolour/16-colour renderer,
btop layout (top bar with status and battery; CPU panel with braille graph and
per-core meters, temperatures and graphs; memory with meters, disk I/O and graph;
network with mirrored download/upload graphs; connections with pills and addresses;
a services table with per-container CPU and memory from `docker stats`, stopped
ones first; setup panel while setup runs). Samples every second, 20 minutes kept.

Verified: 38 unit tests (both looks x 3 scenarios x 6 sizes, only allowed
characters, braille and console graph maths, colours, `--once`, console glyphs vs
the Lat15 font; collector stats parsing); previews reviewed at the Mac's real grid;
the real kiosk (cage + foot + dashboard) ran on WSLg for 75 s without error and was
captured with grim from cage's own socket; the text-console fallback ran through the
real agetty autologin and ignored Ctrl+C/Ctrl+Z/q/Ctrl+D; `foot --check-config` passes.

Not verified: cage on the Mac's i915 + DRM through logind (the fallback covers a
failure), the exact grid at 18 px on the Retina panel, hwmon labels on the Mac.

## 2026-09-28: screen switching on the number row

On the Mac the owner could not use Ctrl + Option + F2: the top row sends brightness
and volume unless fn works. Now: key 2 (or 3-6) on the dashboard asks the root
`macserver-vt.path` (request file in `/run/macserver-console`, made by tmpfiles for
the console account) to `chvt` there; `vt-switch` accepts only a digit 1-6 and ignores
symlinks. `macserver-vtkeys.service` loads `host/vt-keys.map` so Ctrl + Option + 1..6
switch screens on the text consoles (back from the login screen: Ctrl + Option + 1).
The dashboard, guide and troubleshooting say "press 2" instead of the F-keys.

Verified: `loadkeys --parse` accepts the map; on WSL's systemd the dashboard (run as
macserver-console) -> request -> path unit -> vt-switch -> chvt (stand-in) chain
works, junk and symlinked requests are refused; key parsing unit test.
Not verified: on the Mac's keyboard (does the T2 keyboard deliver Ctrl + Option + 1
to the kernel keymap as expected; is `2` delivered to the dashboard inside foot).

## 2026-09-28: MacServer updates itself from GitHub

Owner chose: install new commits on `main` automatically once CI passes, checking
every 15 minutes. New step `autoupdate` (asks; on in unattended mode) enables
`macserver-self-update.timer` -> `installer/self-update.sh` (root): newest `main` via
`git ls-remote`; requires `checks`, `disk-install`, `image`, `vm-test` all successful
for that commit (GitHub check-runs API); fast-forward of the running commit only;
database backup; `/opt/macserver-src` becomes a git checkout (old tree kept as
`.prev`); re-runs host/admin/claude when done before; health check (admin page,
collector, dashboard, CLI); rollback on failure and no automatic retry of that
commit. Status in `/run/macserver/self-update.json` -> status.json `macserver_update`
-> dashboard "MacServer" row and admin page. CLI: `macserver upgrade`,
`macserver autoupdate on|off|status`.

Found while building it: the `checks` job had failed on two pushes (a timing-flaky
dashboard test; fixed), and installer-image only ran for some paths, so admin/ and
CLI changes never got the VM test. installer-image now runs for every commit on main.

Verified: `tests/self_update_test.sh` (private mount namespace, local repository,
saved CI answers; runs in CI): waits for CI, skips failed CI, installs, reports up to
date, refuses rewritten history, rolls back a broken update, no automatic retry,
`upgrade` installs on request, off switch, status file readable. Live read-only
`--check` against GitHub correctly waited on a commit without the full CI.
Not verified: a real update on the Mac (the first will come from this push).

## 2026-09-28: apt waits for the package lock

On the Mac, `--redo host` could not install cage/foot: another apt-get (Debian's
automatic security updates) held the dpkg lock, so the screen fell back to the text
dashboard. `apt_quiet` now waits up to 10 minutes (`DPkg::Lock::Timeout=600`).
Verified in WSL: with the lock held for 5 s, apt waited and then succeeded.
Also confirmed on the Mac: all setup steps done, automatic updates on.

## 2026-09-28: dashboard samples every 500 ms, busier panels

SAMPLE_S 0.5 (history 2400 samples = 20 min). Added: CPU tasks/context switches/
interrupts per second and frequency; memory panel splits into a memory graph and a
mirrored disk read/write graph; network panel shows tailscale0 traffic; services
table gains a per-service CPU TREND sparkline (from the collector's 30 s samples);
an EVENTS panel beside the services on screens 170+ columns wide, fed by status diffs
(services, Tailscale, devices online, public API, MacServer updates, Claude, Debian
updates) and live edges (CPU busy, hot, download burst, offline). "→" joins GLYPHS
(checked present in Lat15). A frame takes about 25 ms to build here.
Verified: 42 tests (event diffing and edge logic, busy panels); previews reviewed.
This change is meant to reach the Mac by the self-updater, not by hand.

## 2026-09-28: fan control, alert glow, red incident view

Fans: `admin/fan_control.py` (root, `macserver-fans.service`, installed by the host
step; replaces t2fanrd, which it disables and `Conflicts=` with). Every 2 s: target =
FAN_MIN_PCT (default 60, never below 30) + the rest scaled by the higher of CPU load
and temperature (50-90 C); 100% at 95 C; rises at once, falls 3 points per step;
writes applesmc fanN_manual/fanN_output; FAN_MODE=auto or no temperature reading
hands the fans back; ExecStopPost resets fanN_manual=0. Reports to
/run/macserver/fans.json; the CPU panel shows rpm, target, floor and a trend.

Alerts: `incidents()` gives each problem a level, reason, fixes and logs. Warning =
the screen edge pulses orange (edge-only frames about 8 per second between full
redraws); critical (any "bad") = the edge flashes red, the whole theme turns red, and
the incident view shows (block-letter CRITICAL banner, problems with why/fix/logs,
system state, all services, live vitals, events). Space toggles incident/overview.
The collector adds the last 8 log lines of stopped or unhealthy containers, with
JWTs, bearer tokens, password/secret/token values, long keys and database URLs
replaced by [hidden] (a test found and fixed a Bearer-token leak in the first version).

Verified: 48 tests (fan curve/step/fallbacks against a fake SMC; redaction; incident
view, warning glow, space toggle, edge-only frames; all scenarios x sizes x looks);
previews reviewed; the kiosk ran a critical incident through real agetty login.
Not verified on the Mac: the applesmc fan attributes under the T2 kernel (the
controller exits cleanly if none exist; install_fans only enables it when they do).

## 2026-09-28: fan fix on the real Mac (T2 fans are not in hwmon)

On the MacBookAir8,2 the CPU hit 97 C and the dashboard's fan reading was blank.
Over Tailscale SSH: the SMC fan controls are on the ACPI device
(`/sys/devices/pci0000:00/.../APP0001:00/fan1_{input,min,max,manual,output}`, min
2700, max 8000 rpm), not in /sys/class/hwmon, so the MacServer controller had found
no fans, stayed off, and left t2fanrd in charge, which held the fan at its 2700 rpm
minimum (manual=1, output=2700). Emergency action on the Mac: stopped t2fanrd, set
fan1_manual=1 and fan1_output=fan1_max; the fan went to 7614 rpm. (A first attempt
from PowerShell had its `$` expanded locally, created two empty files /fan1_manual
and /fan1_output on the Mac; both were removed.)

Fix: `fan_control.smc()` looks on APP0001:00 first (the paths t2fanrd uses), then
applesmc hwmon; the dashboard reads the fan there too; FAN_MIN_PCT defaults to 100
(owner: always full speed); without a temperature reading the fans run at full speed
instead of being handed back; `fan-control --check` tells the installer whether fans
exist. Tests: a fake T2 layout copied from the real Mac, default full speed, no-temp
full speed (50 tests total).

## 2026-09-28: smoothed readings

The dashboard smooths CPU (and each core), temperature, frequency, network, Tailscale,
disk and interrupt/context-switch rates with an exponential moving average
(`SMOOTH`, about a third of each new 500 ms sample), so numbers stay readable and
graphs lose the sample-to-sample spikes; the graphs and the CPU-busy/hot events use
the smoothed values, and the raw ones are kept in `Sampler.raw`. Test: a reading
jumping 10/90% shows a swing under 40 points around 50%, and a real jump to 100% is
shown above 95% within 12 samples (6 s).
