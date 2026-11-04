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
  t2linux, Docker and Tailscale; Caddy 2.11.4, cloudflared 2026.10.3.

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

## 2026-09-28: faster updates

- The Mac checks every 2 minutes (was 15; `git ls-remote` is not rate limited and
  the check-runs API is only asked when a new commit exists). `autoupdate` joined the
  steps an update re-applies, so timer changes reach installed Macs; in unattended
  mode that step keeps the owner's AUTO_UPDATE choice.
- The updater keeps a bare copy of the repository (`/opt/macserver-src.git`, fetched
  each check), checks fast-forward there, and requires only `checks` unless the
  change since the running commit touches iso/, installer/, host/, install.sh or the
  workflows (then also disk-install, image, vm-test).
- Both workflows cancel superseded runs on the same branch.
- Found: every installer-image run rebuilt the base (about 2 min) because cached bases
  predated `build/base/inputs.sum` and an exact cache hit is never re-saved. The
  cache key gained `v2`, so the next run saves a base with the file.
Expected: dashboard/docs changes on the Mac about 2-3 minutes after a push;
installer changes about 5-7. Verified: 13 updater checks (quick-only install while
the VM test runs; installer change waits for it). Timings to confirm on the next runs.

## 2026-09-28: Supabase ports were reachable from the LAN (found on the real Mac)

While preparing a PlaneSight deploy: on the Mac, supabase-db 5432, the pooler 6543
and the API 8000 listened on 0.0.0.0, and a connection test from another machine on
the same Wi-Fi reached all three (the admin page 8090 correctly did not).
/etc/docker/daemon.json had "ip": "127.0.0.1" but was modified at 20:43 that evening,
after the Supabase containers were created; a daemon setting does not change existing
containers' bindings. And the MacServer firewall only had an input chain, while Docker
DNATs published ports through the forward path.

Fix in the repository: host/nftables.conf gains a forward chain (priority filter - 1,
before Docker's) that drops new connections arriving on wl*, en*, eth*, usb*, ww*,
whatever a container binds to; established traffic and tailscale0 pass. The
self-updater re-applies the firewall step. Verified with a network-namespace test
(fake Wi-Fi, fake container, Docker-style DNAT): without the chain the "database"
answered from the LAN, with it the connection is blocked, and the container's
outgoing traffic still works. On the Mac: ~/fix-supabase-ports.sh recreates the
Supabase containers so they bind to 127.0.0.1 (needs the owner's sudo).
Follow-up: recreating the containers did not help: Docker's daemon "ip" setting only
covers the default bridge, not Compose networks, so Supabase's ports were never on
127.0.0.1 (the repo test only checked daemon.json). `supabase_local_ports` now writes
/opt/macserver/supabase/docker-compose.macserver.yml (api-gw 8000, supavisor 5432 and
6543 on 127.0.0.1, `!override`) and adds it to COMPOSE_FILE; called by the supabase
step and by the firewall step, which updates re-apply. Verified with `docker compose
config` on Supabase v0.8.2's real files: all three ports host_ip 127.0.0.1.

## 2026-09-28: the incident view is a debugging console; status every 2 seconds

Status file: `macserver-status.timer` now runs every 2 s (was 30 s). A run takes about
0.3 s: container CPU and memory come from cgroup v2 (the same numbers as `docker stats`,
which alone took 2.2 s), and the slow parts are cached in `/run/macserver/collector-state.json`
(apt 5 min, public route 20 s). The service has `LogLevelMax=warning` so the journal is not
flooded. The admin page polls every 2 s, the dashboard reads the file every 2 s, and the
admin page calls the data stale after 20 s (was 120 s).

The collector adds a `debug` section to status.json (never fatal: an error there leaves the
basic health data intact). Envoy access log parsed into requests (query strings stripped,
token-like path segments hidden), `/proc/net/nf_conntrack` grouped into inbound and outbound
connections per container (labelled by container, tailnet device or LAN host), `ss` listeners
with how far they reach, router / Internet / DNS checks (an outage needs two misses in a
row), `docker inspect` facts for services that are not fine, the last error lines of each
service log, failed units, journal and kernel errors, OOM kills and heat-throttle counters,
and the files worth opening with their age.

Dashboard: five incident pages that rotate (n / b / p keys). New incidents from the debug
data: ports reachable from the LAN, failed units, API 5xx, OOM kill, heat throttling,
Internet unreachable (critical), DNS failing. The T+ clock counts from the start of the
incident.

Verified: 71 Python tests (parsers on real Envoy / conntrack / ss lines, every page at six
sizes in rich and console mode, glyph set), renders of every page from sample and from the
real Mac's status.json, and on the Mac: collector 0.27 s per run, status file 2 s old, no
secrets in the debug section (the public anon key is in status.json by design).

Found on the way (also in the entry above): Supabase's published ports were reachable from
the LAN; `docker-compose.macserver.yml` is written by the supabase step and the firewall step
re-applies it. On the Mac the fix was still not applied after the update to fe68309 (no
override file, no forward chain, 5432 / 6543 / 8000 open from the LAN), because the updater
that installed it does not run the firewall step itself: it takes effect on the next
update, or with `sudo ./install.sh --redo firewall`. The dashboard now shows this as a
notice ("port(s) reachable from the local network") until it is fixed.

## 2026-09-29: no HTTPS certificate for the admin page; DNS through systemd-resolved

Found on the real Mac: `https://macserver.<tailnet>.ts.net/` never loaded (Firefox
`SSL_ERROR_INTERNAL_ERROR_ALERT`). `tailscale serve` and the admin page were fine, but
tailscaled could not get its Let's Encrypt certificate: `lookup
acme-v02.api.letsencrypt.org ... i/o timeout`. Two causes, both measured on the Mac:
1. With Tailscale DNS on and no systemd-resolved, Tailscale writes 100.100.100.100 and
   fd7a:115c:a1e0::53 into /etc/resolv.conf. tailscaled's own sockets carry fwmark
   0x80000, which skips Tailscale's routing table, so they cannot reach that resolver
   (a marked query timed out / "network unreachable"; an unmarked one was answered).
2. The Wi-Fi router drops every UDP DNS query that carries EDNS0 (to itself, 1.1.1.1
   and 9.9.9.9 alike) and does not answer DNS over TCP. Go's resolver always sends
   EDNS0; glibc does not, so curl, apt and getent worked. Public resolvers answer over
   TCP and TLS.

Done by hand on the Mac (owner's go-ahead): restarted tailscaled (no effect);
`tailscale set --accept-dns=false`; installed systemd-resolved. It then had no
upstream servers until `systemctl reload dbus`, a restart and `nmcli general reload
dns-full` (the Mac's own DNS was down about two minutes, 09:24-09:26); a hand-made
drop-in `/etc/systemd/resolved.conf.d/macserver.conf` adds FallbackDNS. tailscaled got
its certificate at 09:27 and the admin page answers 200.
Side effect: Docker forwards Supabase's container lookups to what /etc/resolv.conf
listed when they started (`ExtServers: [host(100.100.100.100) host(fd7a:...::53)]`);
with Tailscale DNS off, 100.100.100.100 answers SERVFAIL for public names, so those
containers cannot resolve public names until they restart. A new container gets
`host(127.0.0.53)` and resolves (glibc and c-ares/EDNS0 lookups tested).

In the repository:
- `host/resolved.conf` + `install_dns` (host step; asks; the updater re-applies it):
  systemd-resolved with Quad9 and Cloudflare next to the network's server, DNS over
  TLS opportunistic, LLMNR and mDNS off; after installing the package it reloads D-Bus
  and NetworkManager's DNS. `fix_dns` uses resolved once it runs.
- Tailscale joins and re-runs with `--accept-dns=false`.
- `host/sysctl.conf`: no ICMP redirects or source routing (all + default), secure
  redirects off, broadcast pings and bogus ICMP errors ignored, SYN cookies.
- Firewall: pings from the LAN rate-limited (10/s, burst 20).
- `macserver doctor` checks DNS goes through resolved; `tailscale up` hints carry the
  flags. The server's Claude skill: publish on 127.0.0.1 explicitly, no `dns:` in
  compose files.
- Docs: SECURITY (DNS, sysctl, the corrected Docker claim, what the updater
  re-applies), NETWORK (DNS section), TROUBLESHOOTING and GUIDE (certificate,
  container DNS).
- Tests: repo invariants (resolved settings, `--accept-dns=false` on every `tailscale
  up`/`set`, DNS switched before the dashboard); the VM test waits for the DNS line.

Upgrades checked on 2026-09-29: Supabase self-hosted v0.8.2, Caddy 2.11.4, cloudflared
2026.9.3 and Claude Code 2.1.284 are the newest releases; Docker 29.8.1 and Debian had
nothing pending. Nothing to bump.

Verified: `tests/run.sh` (72 tests), ShellCheck 0.11, `nft -c`; the local VM test
(`tests/local.sh image` + `vm`): hands-off install, then first boot on real systemd with
NetworkManager installed systemd-resolved and resolved names within 2 s ("ok DNS:
systemd-resolved on 127.0.0.53"), then the dashboard. CI passed all four jobs.

On the real Mac (owner approved; 10:12-10:16): the self-updater installed 2286525 and
passed its health check. It applied the forward chain and moved Supabase's API
gateway and pooler to 127.0.0.1 (both recreated, about 30 s); resolved runs with the
new drop-in (LLMNR and mDNS off, DoT opportunistic); the sysctls are live; Tailscale
DNS is off. Then `sh run.sh restart --except api-gw supavisor`: all 11 Supabase
containers healthy after 46 s, every one with `ExtServers: [host(127.0.0.53)]`, and
lookups from supabase-storage work (getaddrinfo and c-ares/EDNS0). The hand-made
drop-in was removed. From another PC on the same Wi-Fi, 192.168.18.33 answers on
none of 5432, 6543, 8000, 8090, 8081, 5355, 22, 443; the admin page answers 200 over
the tailnet. The only listener beyond loopback and the tailnet is Tailscale's UDP 41641.

For the owner: in the Tailscale admin console, turn off key expiry for `macserver`
(it expires about 2027-03-28; re-authenticating then needs someone at the Mac).
PlaneSight's compose file hard-codes Quad9 (`dns:`) and builds with `network: host`
because Tailscale's resolver used to be in the way; both can go now.

## 2026-09-29: admin page as a btop-style console, with a web terminal

- The admin page is now three views: **overview** (btop layout: CPU history graph and
  per-core meters, memory/swap/disk meters and disk I/O, network graphs, sortable process
  table, containers with a click-through detail), **network** (latency, requests per 2 s,
  listeners, connections, top paths and clients, everything wrong in one list) and
  **terminal**. Keys 1/2/3 switch views; the tab is kept in the URL hash.
- `collect_status.py` reports a `live` section: CPU % (total and per core), network and
  disk-I/O rates, memory and swap, tasks and the busiest processes (by CPU plus by memory).
  Rates come from the previous run's counters in the collector state file; the page keeps
  the 4-minute history. Only process *names* are reported, never command lines.
- `admin/terminal.py` + `macserver-terminal.service`: a WebSocket-to-pty bridge (standard
  library only), a login shell as the owner, mounted at `/term` by `tailscale serve`.
  Security model in `docs/SECURITY.md`. Installed by `step_admin` (which self-update reruns).
- Vendored xterm.js 5.5.0 and addon-fit 0.10.0 (MIT), pinned by hash in `tests/test_admin.py`.

### Verified

- `tests/run.sh` in WSL Debian (Python 3.13): 88 tests, including a real shell over a
  real WebSocket, hangup on close, resize, the origin/login gate, and the collector's rates
  against a fake `/proc`.
- `node --check` on both scripts.

### Not verified

- The page has not been looked at in a browser, and `tailscale serve --set-path /term`
  has not been run on the Mac (whether the mount prefix is stripped is handled either way:
  the service accepts `/ws` and `/term/ws`). ShellCheck was not available locally; CI runs it.

## 2026-09-30: idle mode (low power when nobody is connected)

After `IDLE_AFTER_S` (default 900 s) with no connections, the Mac uses less
power until activity returns. Detection is in the collector (`status.json`
`idle`: API traffic in the last minute, live inbound connections, meaningful
outbound work, an active Claude session, or containers over 20% CPU; Tailscale's
control plane, the tunnel keepalive and DNS/NTP never count). `admin/idle.py`
(`macserver-idle.timer`, every 30 s) then reversibly applies: CPU governors to
`IDLE_GOVERNOR` (default powersave), screen to `IDLE_BRIGHTNESS_PCT` (default
30%), and `docker pause` for `IDLE_PAUSE_CONTAINERS` (default empty: nothing).
It never touches the network, firewall, Tailscale or the public route, and a
stale collector fails safe (stay awake). `sudo macserver idle [status|auto|on|off]`
(default auto); shown in `macserver status`, the dashboard (Idle row, IDLE badge)
and the admin page.

Verified: `tests/run.sh` (shell syntax, ShellCheck 0.11, 91 Python tests: collector
hysteresis/activity, controller apply/restore with fake sysfs, dashboard Idle row,
repo safety invariants) plus end-to-end `--status`/`--check` with a fake status file.
Not verified: real governor/backlight paths and wake latency on the MacBook Air.

## 2026-10-01: dev tab on the admin page (mirror of macserver CLI)

The admin page gains a fourth view (**dev**) that mirrors every `macserver`
command: status, keys, logs, restart, update, backup, public on/off/setup,
idle auto/on/off/status, wifi, doctor, upgrade, autoupdate. Each command has
a "run" button; output appears in a details panel below the list. The page runs
commands via a new `/api/dev` endpoint that validates the subcommand against an
allowlist and executes `macserver` (with `sudo` when the command needs root).
The web terminal is still view 3; key 4 switches to dev.

Verified: full `tests/run.sh` green (109 tests), including the new dev endpoint
authorization, same-origin gate, and allowlist check.

## 2026-10-01: `macserver` command reachable over SSH

Over SSH as the owner, `macserver dashboard` answered "command not found":
the command lives in `/usr/local/sbin`, which is not on unprivileged users'
PATH (only the login summary, which uses the absolute path, worked).
`install_tools` now links it into `/usr/local/bin`; read-only commands
(`status`, `dashboard`, `doctor`, `idle status`) work without sudo while
everything privileged still refuses via `need_root`. Existing Macs get the
link from the next self-update (it re-runs the admin step). The live
dashboard itself needed no change: `macserver dashboard` (q quits) already
draws in the SSH terminal, complementing the dev tab, which runs the same
commands from the admin page over Tailscale.

Verified: `tests/run.sh` green (new repo invariant: the symlink plus
rootless `dashboard`/`status`/`doctor` dispatch); confirmed on the Mac that
`/usr/local/sbin/macserver` exists and `/usr/local/bin/macserver` is missing.

## 2026-10-01: doomsday lockdown protocol

Intrusion-like behaviour (unusual inbound/outbound connections, unexpected
listeners, API 5xx spikes, unknown root processes) isolates the Mac: an email
goes out through the `doomsday-alert` GitHub Action BEFORE the network is cut,
then Tailscale, the public tunnel, Supabase and the admin page stop, the
lockdown firewall drops input/forward/output except loopback, DHCP and LAN SSH,
swap is dropped, extra encrypted volumes lock, and the screen turns into a
red lockdown incident. Tailscale SSH, loopback, infra traffic and allowlisted
AI-agent/owner processes never score; LAN SSH is noted but cannot fire alone;
firing needs repeated over-threshold checks. Recovery is a Google
Authenticator (TOTP, stdlib-only) code: `unlock` enters an isolated DEBUG mode,
`restore` returns to normal with everything auto-restored from a snapshot.
New `doomsday` install step (on by default), `macserver doomsday ...` commands,
`doomsday` in status.json, the dashboard and `macserver status`.

Verified: `tests/run.sh` (new `tests/test_doomsday.py`: RFC 6238 TOTP vectors,
scoring, hysteresis, alert redaction, repo wiring); ShellCheck clean.
Not verified: a real isolation/restore cycle on the Mac (needs the owner at
the console), the alert email delivery, and TOTP enrolment.

## 2026-10-01: CI workflows rejected for steps combining `run` and `uses`
GitHub rejects a workflow file outright (zero jobs run) when a step has both
`run:` and `uses:`. Every email notify job in `ci.yml` and
`installer-image.yml` built the body with `run:` inside the send-mail step, so
both workflows failed on every recent commit — which also froze the
self-updater (it only installs commits where those jobs passed). Each notify
is now two steps (create the body file, then send it), and the new
`doomsday-alert.yml` had the same bug and is fixed the same way.
`tests/test_repo.py` now fails the suite if any step combines the two keys.

## 2026-10-01: 1.0.0 install commit — doomsday with pre-emptive exclusions

The false-positive lockdown (Tailscale direct + RustDesk) drove a systematic
exclusion overhaul so **no config is needed** for normal MacServer operation:

- **Auto-trusted, zero-config**: Tailscale direct peer traffic (UDP 41641 inbound,
  ephemeral-port hole-punching outbound), mDNS (UDP 5353), kernel threads
  (`[kworker/0:1]`, `[rcu_sched]`), container runtime processes
  (`containerd-shim-runc-v2`, `docker-proxy`, `runc`), and host-network
  services (RustDesk `hbbs`/`hbbr`, PlaneSight).
- **Configurable**: `DOOMSDAY_TRUST_CONTAINERS=false` disables container
  auto-trust; `DOOMSDAY_TRUSTED_PROCS` / `DOOMSDAY_TRUSTED_USERS` for custom
  services; `DOOMSDAY_SCORE` / `DOOMSDAY_CONSECUTIVE` thresholds.
- **Verified**: `tests/run.sh` green (154 tests incl. 43 doomsday: kernel
  threads, mDNS listeners, container auto-trust, RustDesk+Tailscale combo);
  CI green on 5631a50; self-updater will install this to all Macs.

First-boot flow is now: install → self-update → `setup-totp` + `setup-gh` →
`doomsday test` (score 0) → done. No lockdown noise, real threats still
caught.

Not verified: real isolation/restore cycle on the Mac (needs owner at
console), alert email delivery, TOTP enrolment.

## 2026-10-04: Authenticator enrolment with a QR code

`sudo macserver doomsday setup-totp` (no argument) now creates the secret, shows a QR
code in the terminal (`qrencode`, installed by the doomsday step; falls back to the
setup key as text), asks for the 6-digit code the app shows and saves the secret only
if that code matches. `doomsday.py totp-verify --secret S CODE` checks an unsaved
secret. `setup-totp <SECRET>` still works for a secret made elsewhere.

Verified: `tests/run.sh` in WSL Debian (157 tests, 1 skipped), ShellCheck clean; new
tests for the verify CLI and for "QR shown, code checked before the file is written".
Not verified: the QR rendering on the Mac's console or in the admin web terminal, and
a real enrolment with Google Authenticator. Codes are still not single-use (replay
within about 90 s is possible).

## 2026-10-20: public RustDesk route is the web-client ports over a Cloudflare Tunnel (the connection is behind CGNAT)

Found while setting the public name: on the home connection `curl ifconfig.me` (64.135.140.29) differs from the router's WAN IP, so router forwards cannot work. The setup from 2026-10-04 was therefore completed differently: `RUSTDESK_RELAY=rustdesk.jonahchang.com` in `/opt/macserver/rustdesk/.env` (hbbs logs `relay-servers=["rustdesk.jonahchang.com:21117"]`, `ALWAYS_USE_RELAY=N`), and a second compose project `~/rustdesk-ws` on the Mac runs only cloudflared (same pinned image `2026.9.3@sha256:072c067...` as gateway/compose.yml, host networking, publishes nothing, token in `~/rustdesk-ws/.env` 0600). Tunnel ingress (remote-managed, seen in cloudflared's log): `rustdesk.jonahchang.com` -> `^/ws/id` to `127.0.0.1:21118`, `^/ws/relay` to `127.0.0.1:21119`, everything else 404, warp-routing off.

Cloudflare terminates WSS and sets X-Real-IP itself, so the web-client-ports spoofing warning is contained by design: the firewall accepts 21115-21117/TCP and 21116/UDP but never 21118/21119, so only this tunnel reaches them (checked `nft list table inet macserver`).

Verified on 2026-10-20: hbbs/hbbr up (hbbr 10 days, hbbs 16 h after the relay change); `ss` shows hbbs on 21115/21116 TCP+UDP and 21118, hbbr on 21117/21119; an HTTP/1.1 WebSocket handshake through `https://rustdesk.jonahchang.com/ws/id` and `/ws/relay` returns `101 Switching Protocols` both from the Mac and from the Windows PC (the earlier 502 probes were plain HTTP ones, which correctly fail: these are upgrade-only ports). `macserver rustdesk key` prints `w0VRhJI6MHzbHbULGlK04uGheb54esQP8ZyScyfeEps=`; `macserver doomsday test` is quiet (mode on, state normal). Docs: docs/RUSTDESK.md gains the CGNAT section with web-client IDs `wss://rustdesk.jonahchang.com/ws/id` + `/ws/relay`, and tailnet devices speak to hbbs directly at `macserver.tail9e3c7b.ts.net` (ports 21115-21117, UDP 21116), since the firewall admits everything from `tailscale0`.

Not verified yet: a real RustDesk session (web client direct and relayed; the hand-held PC's installed client can only reach the server while inside the tailnet), and the tunnel's price/limits under real relay traffic (web-only route through Cloudflare).

## 2026-10-04: self-hosted RustDesk server (hbbs + hbbr)

Replaced a hand-made RustDesk setup (`/opt/rustdesk`, unpinned, no `-r`, ports closed
by the firewall so it was unreachable from outside) with a project-managed one:
`rustdesk/compose.yml` (image pinned `1.1.16@sha256:8ecdab65...`, host networking,
`./data:/root`, `restart: unless-stopped`, `hbbs -r $RUSTDESK_RELAY:21117`, no
always-relay switch, `cap_drop: ALL`), deployed to `/opt/macserver/rustdesk` by the new
optional `rustdesk` install step or `sudo macserver rustdesk setup`. `host/nftables.conf`
admits TCP 21115-21117 and UDP 21116 (the web-client ports 21118/21119 stay closed);
`tests/test_repo.py` now pins that exact list. Docs: `docs/RUSTDESK.md`.

Done on the Mac by hand (the updater has not shipped this commit yet): backed up the old
data and firewall file to `/var/backups/macserver/`, removed the old `hbbs`/`hbbr`
containers, started the new project with a fresh keypair and `RUSTDESK_RELAY` set to
the public IP, loaded the new firewall rules.

Verified: `ss` shows hbbs on TCP 21115/21116/21118 + UDP 21116 and hbbr on TCP 21117/21119;
logs show `ALWAYS_USE_RELAY=N`, `relay-servers=["<ip>:21117"]`, no errors; TCP 21115-21117
answer from another PC on the LAN and 21118 does not; the repo invariants pass.

Not verified: a real client session (direct and relayed), the router forwards (the owner
adds them; check for CGNAT first), a DDNS name (none yet: the relay is the public IP, which
can change), the new install step in the installer-image VM test (CI), and whether the
doomsday detector stays quiet with public RustDesk traffic (`macserver doomsday test`).

## 2026-10-26: cloudflared 2026.10.3 (eMudhra intermediates)

Cloudflare asked every tunnel to move to cloudflared 2026.10.2 or later: older builds
still trust eight eMudhra (emSign) intermediates with weak keys. Pin bumped to
`2026.10.3@sha256:faa8e0a1…` (multi-arch index, checked on Docker Hub) in
`gateway/compose.yml` and the two jonahchang.com stacks (`~/apps`, `~/rustdesk-ws`).

Done on the Mac by hand (the updater does not re-run the gateway step): the running
gateway is `~/api-gateway` (not `/opt/macserver/gateway`). Each compose file was backed
up to `compose.yml.bak-2026-10-26`, then only the cloudflared service was recreated, one tunnel at a
time. All origins are plain HTTP, so the stricter trust store rejects nothing.

Verified: all three containers log `Version 2026.10.3` and 4 registered connections, no
ERR/WRN; vexvortex, jonahchang.com and planesight answer 200 through the tunnel.
Found, not caused by this: `planesight-api.jonahchang.com` returned 404 because the
remote-managed ingress of `macserver-apps` had no rule for that hostname (the DNS record
existed). Fixed in `jonahchang.com/domains.json` (`planesight-api` ->
`http://planesight:8080`) and applied with `cloudflare-setup.mjs apply`; `/`,
`/api/health` answer 200 and `/ws` upgrades (101).

## 2026-11-03: heat is a reading, not a warning

The owner's Mac "just runs kinda hot", and the dashboard kept calling that a problem
(a "running hot" notice and amber glow from 90 °C, an event from 85 °C, "warm" from 80 °C
on the thermal panel, amber on the web page from 75 °C). Measured on the real Mac over
SSH (read-only): the package temperature swung 89 to 100 °C within 12 seconds with the
fan at 8071 of 8000 rpm, `coretemp` reports `max` and `crit` of 100 °C, and the lifetime
heat-throttle counters are 487180 (core) and 1718858 (package). Hitting the chip's own
limit is how this machine normally works, so any fixed "too hot" line below 100 °C would
flicker all day.

Now: no temperature raises a notice, an amber glow, a warning label or an event
(`admin/tui.py`, `admin/static/app.js`). The temperature colour scale stops short of alarm
red. The one cooling alarm left is a fault, not heat: a CPU at 85 °C or more whose fan
reads under 300 rpm (`tui.cooling_fault`); no fan reading at all is not a fault. The thermal
panel says "normal" or "at its limit" (100 °C).

Tests: `test_heat_alone_is_never_a_notice_only_a_stopped_fan_is`,
`test_heat_alone_never_raises_a_notice_on_the_web_page`. The dashboard test fixture now
builds a 2,400-sample history instead of 1.2 million (the screen draws only the newest few
hundred), which takes the dashboard tests from 505 s to about 10 s.

Not verified: the change on the Mac's own screen. On the Windows development PC the full
suite has six failures that are identical with and without this change: `pwd`, `os.statvfs`
and POSIX file modes do not exist there (`test_collector`, `test_live_stats`,
`test_terminal`, `test_once_prints_a_summary`, and two in `test_fans`); run it with
`PYTHONUTF8=1`.

## 2026-11-03: status.json gains the detail sections (for the redesigned dashboards)

`admin/collect_status.py` now also writes: `system` (host facts and logged-in sessions; CPU model,
cores, governor, turbo, frequency range and pressure-stall averages; memory detail; swap devices;
run queue, open files, conntrack fill; filesystems with space and inodes; disks with NVMe state and
firmware; Docker's disk use; every network interface with counters, errors, drops and addresses;
Wi-Fi network, band, channel, rate and live signal; TCP health; every temperature sensor with its
own limits; the T2 fans; battery health, cycles, power flow and time left; per compose project
roll-ups; every Tailscale device with route and last seen; Postgres size, connections, cache hit,
transactions per second, auth users and storage objects), per-container extras (restarts, start
time, health, processes, memory limit, network and block-I/O rates), and in `live` the CPU time
breakdown (user/system/iowait/irq/steal), kernel rates (context switches, interrupts, forks, swap
traffic, major faults) and per-disk throughput, IOPS and busy %.

Fixed on the way: `host.sensors.fans_rpm` was always empty on this Mac because the T2 fans are on
the SMC's ACPI device, not hwmon (now read from `/sys/bus/acpi/devices/APP0001:00`); Tailscale's
`funnel-ingress-node` relays (20+ of them here) counted as "devices online" and are now left out
of `peers_online`; Wi-Fi's reported -256 dBm noise floor is dropped.

Fixed, found while checking how the new code gets installed: `admin/units.py`, imported by both
`collect_status.py` and the dashboard, was never installed by any step. It is on this Mac only
because it was copied by hand on 2026-10-01 (file date), so a fresh install from the USB image would
have stopped both with `No module named 'units'`, and CI cannot tell (the VM test waits for a log
line). `install_tools` now installs it; `test_python_modules_that_installed_scripts_import_are_installed_too`
fails without that line.

Verified: 174 tests on the Windows PC (the collector tests now import there: `pwd` is optional);
each reader has a test fed text copied from this Mac. The new collector was run twice
on the Mac as the unprivileged `admin` user with output in /tmp (removed after): all sections filled, no errors,
31 KB of JSON; the new code costs about 0.25 s when its caches expire (every 10 s) and the first
`docker system df` takes 4.7 s once per 15 minutes.

Not verified: a run as root from the real timer (the Mac's root-only sources: `dmesg`, conntrack
flows), the installer change in the VM test (CI), and the six Windows-only test failures
(`getloadavg`, `statvfs`, `pty`, POSIX file modes) which are the same with or without these changes.

## 2026-11-03: the Mac's screen dashboard is five pages, sampled every 2 seconds

**Why sampling changed.** `main` had `SAMPLE_S = 0.001`: the kiosk dashboard redrew the whole screen in a
loop with no pause. Measured on the Mac over SSH (read-only): the `dashboard --kiosk` process had used
564,105 CPU-seconds in 669,503 s (84% of one core since boot) and held 637 MB; a second `dashboard` left
open in an SSH session used another 64%. On a 4-thread CPU that is a large part of why the Mac runs hot.
The owner had already fixed this on 2026-10-04 (branch `sampling-2s`: 2 s sampling, 600-sample history),
but it was never merged, and the auto-update on 2026-10-26 reinstalled `main`'s 1 ms loop over the hot-patch.
Now `SAMPLE_S = 2.0` and `HISTORY = 600` are on `main` (branch `sampling-2s` is superseded and can be
deleted), and the screen redraws every `DRAW_S = 1.0` so the clock still ticks. One frame costs about
11 ms on the Windows dev PC.

**What the screen shows.** `HOME_PAGES`: Overview (30 s), Hardware, Network, Services, System (15 s each),
turned by `n`/`b`, held by `p`, with a page strip and countdown under the top bar. Overview: a KPI strip
(CPU, memory, network, disk, temperature, services, power, load; fewer cards on narrow screens), CPU (every
thread, time by kind, load, pressure-stall averages, kernel rates, governor), memory breakdown, storage
(filesystems, SSD, I/O, Docker disk use), network (interfaces, Wi-Fi, reachability, TCP), the Connections
pills, services grouped by compose project (CPU bar and trend, memory, in/out/write rates, restarts) and
events. Hardware: CPU graph, every sensor with its limit, fan, battery health/cycles/time left, processes,
memory, storage, machine facts. Network: interface table, Tailscale devices (route, last seen, key expiry),
ports, connections, API traffic. Services: full container table with image, database panel (connections,
tps, cache hit, size, users, stored files), traffic, events. System: facts, status pills, events, logs,
service errors, files. The red incident view and its five pages are unchanged.

Panels read the collector's new `system` section (see the entry above) and draw what they can read: an older
collector, or a source that is missing, leaves a gap, never an error (tested for every page and size).
Console mode (TERM=linux) uses only `tui.GLYPHS`; tests draw every page, scenario and size in both modes.

Verified: 77 dashboard/repo/collector tests on the Windows PC (the only failure there is
`test_once_prints_a_summary`: `os.statvfs` does not exist on Windows, same before and after); previews of
all five pages rendered and read; the README screenshot was redrawn. Not verified: the new pages on the Mac's
own screen and in the Lat15 console font (CI's `test_console_glyphs_exist_in_the_mac_console_font` needs
`console-setup-linux`), the 1 s redraw under the real `foot` terminal, and the real CPU use of the new loop
(expect a few percent of one core; check with `ps` after the update lands).

## 2026-11-03: the web admin page is redesigned (same pages as the screen)

`admin/static/{index.html,style.css,app.js}` rebuilt; `admin/server.py` and the three-file whitelist are
unchanged, so nothing new needs installing. Pages: Overview, Hardware, Network, Services, Terminal, Tools
(keys 1 to 6, a real ARIA tab list with arrow keys). A flat, restrained console: system fonts, tabular
numbers, one accent colour, green / amber / red only for state, dark by default and light when the device
asks (`prefers-color-scheme`). It shows what the new collector writes: the stat strip with sparklines, a
"Needs attention" list that gives the command to run, CPU time by kind and pressure-stall averages, memory
breakdown, filesystems and the SSD, Docker disk use, interfaces, Wi-Fi, TCP, every Tailscale device (route,
last seen), listening ports, API traffic, sortable process and container tables (click a container for its
last log lines), the database panel, battery health and time left, and every sensor with its limit. Charts
are canvas lines with five minutes of history kept in the browser (150 samples).

Same rule as the screen: heat is only a reading. The one cooling alarm is a warm CPU whose fan reads under
300 rpm. Fixed: the tools tab had two mismatched `</pre>` tags, and its output routing sent most commands to
one shared box (each tool now writes to its own).

`tests/web_preview.py` serves the page with moving sample data (`python3 tests/web_preview.py`, then open
http://127.0.0.1:8765/); `tests/test_web_page.py` pins the CSP-relevant rules (no inline script or style, no
outside URL, no `innerHTML`), unique ids, every id the script uses exists, balanced markup, WCAG AA contrast
of the colour tokens in both themes, and that the script parses.

Verified: 38 page/repo/admin tests; every page rendered in headless Chrome with 25 to 35 real polling samples
(no console errors, no horizontal overflow at 1500 px and at a 390 px phone emulation); contrast measured
(the one failure found, the tab-number hints at 80% opacity, was fixed). Not verified: on the real Mac behind
`tailscale serve` with a real login (the headless run used sample data), in Safari and Firefox, the
terminal tab's iframe (unchanged code), and the Tools buttons against the real `/api/dev`.

## 2026-11-03: the dashboards stop colouring normal network facts red and yellow

Investigated over Tailscale SSH (`admin@macserver-1`) after the Network page showed six red "ALL NETS"
ports and yellow LAN addresses. Nothing was exposed beyond the documented RustDesk exception. Port
21115-21117 answer from the LAN by design (TCP, `host/nftables.conf`); 21118, 21119 (RustDesk web-client
sockets, reached only through the tunnel on 127.0.0.1) and 20241 (cloudflared metrics of the host-network
`rustdesk-ws` tunnel) bind every address but the input chain drops them. Probed from the Windows PC on the
same LAN (192.168.18.41 to 192.168.18.33): 21115-21117 open, 21118, 21119, 20241, 8000, 5432 not reachable.
The yellow inbound line was this PC's own direct Tailscale link (`tailscale status`: direct
192.168.18.41:41641). The yellow/red outbound lines were the router (DNS, NAT-PMP, UPnP) and ordinary
Internet services.

Changed: the collector reads the firewall's allow-list (`firewall_ports`, from `/etc/nftables.conf`, only
while the `nftables` unit is active) and tags a wildcard-bound port `open` (admitted on purpose, "FW OPEN")
or `blocked` ("FW DROP"); neither counts as exposed. A port nothing accounts for is still `all` and red.
Tailscale's direct connection to a tailnet device is `tailnet`, not an unknown LAN host (`tailscale_direct`,
only on port 41641). Outbound destinations are plain text; only inbound strangers are coloured. The System
page no longer flags `/run/macserver/status.json` as "changed in the last 10 minutes" (it is rewritten every
2 s), and cloudflared's "stream N canceled by remote with error code 0" (a client hanging up) is no longer a
service error. Web page: the listening-ports table has the same two new labels.

Verified: tests for the real `host/nftables.conf` against the parser, the listener ordering, the direct-peer
rule (the same PC on another port stays LAN) and the rendered panel; same six Windows-only failures as before.
Verified on the real Mac after the self-update (2026-11-03): `debug.listeners` reports 21115-21117 as `open`
and 20241, 21118, 21119 as `blocked`, and no inbound LAN flow is flagged.

Open, not changed: a newer kernel (6.18.60) is installed but 6.18.54 is running, so "restart needed" is
true and a reboot is the owner's call. `macserver-apps-calmlist-1` logs `Status check failed (503):
{"error":"Supabase is not configured"}` every few minutes: that is the app's own check, not MacServer.
`~/rustdesk-ws` could also set `TUNNEL_METRICS=127.0.0.1:20241` so cloudflared's metrics stop binding every
address (the firewall already drops them); that restarts the web-client tunnel, so it was left for the owner.

## 2026-11-03: text alerts to the owner's phone

`admin/notify.py` (timer `macserver-notify.timer`, every 30 s, installed by the `admin` step so the self-update
delivers it) texts the owner when the dashboards show an issue. It reuses the screen's own `tui.incidents()`
and `tui.muted_notice()`, so it says what the screen says and heat is never an issue (a test pins: 100 C with
the fan at 8071 rpm is silent; only a warm CPU with a stopped fan counts). The text is sent through the new
`alert-sms` workflow (same route as the doomsday email): the Mac POSTs a `workflow_dispatch` with the doomsday
dispatch token, and the workflow emails `SMS_GATEWAY_ADDRESS` (Verizon: `<number>@vtext.com`) with the existing
`SMTP_USERNAME` / `SMTP_PASSWORD` secrets. The phone number exists only in that secret, never in the repo or
on the Mac. The repository is public, so the workflow reads the message from `GITHUB_EVENT_PATH` and never from
an expression or env var (a test pins that), so the text does not land in the run log.

Rules: a critical (bad) issue is texted after 60 s and again every 4 h while open; a warning after 180 s, once,
and not within 15 min of the last text; one text lists everything open, worst first, in 160 ASCII characters
ending with `https://<tailscale name>/` (the live name from status.json; the phone needs the Tailscale app);
at most 4 an hour and 12 a day; a failed send retries every 5 min and does not count. `sudo macserver notify
status|test|on|off`. It cannot say the Mac is off or offline.

Verified: 25 new tests (what counts as an issue, the full timeline of texts, the 160-character limit, the
dispatch request and its error hints, workflow hygiene, install wiring, loading the installed `dashboard`
file); the rest of the suite shows the same six Windows-only failures. Not verified: any real text. Needs, once:
the `SMS_GATEWAY_ADDRESS` repo secret (setting a secret was refused in this session, so the owner adds it),
`sudo macserver doomsday setup-gh` on the Mac (`/etc/macserver/doomsday-gh-token` does not exist there yet, so
the doomsday email alert has not been working either), then `sudo macserver notify test`. Until the token
exists the service logs one line every 6 h and does nothing. Verizon's gateway is best-effort (filtering, no
delivery receipt); if the test never arrives, switch the secret to `<number>@vzwpix.com` (MMS) or move to a
paid SMS API.

## 2026-11-03: `macserver idle|doomsday|notify` lost their first argument

Found when the owner ran `sudo macserver doomsday setup-gh` and `sudo macserver notify test` on the Mac: both only
printed a status. `main()` already drops the command name, and the `idle`, `doomsday` and `notify` branches each
shifted once more, so `setup-gh`, `test`, `on`, `off`, `unlock` and `auto` never reached their handler (only the
no-argument status forms worked). The extra shifts are gone (`macserver`), and
`tests/test_repo.py::test_subcommands_reach_their_handler_with_every_argument` runs the real `main()` with stub
handlers; it fails on the old script and passes on the fixed one. Until the Mac has self-updated, the commands can
be run directly: `sudo /usr/local/lib/macserver/doomsday setup-gh` and `sudo python3 /usr/local/lib/macserver/notify.py test`.

Verified: that test (old script fails five cases, fixed passes), `bash -n macserver`; the rest of the suite shows
the same six Windows-only failures. Not verified: the commands on the real Mac after the update; shellcheck (not
installed on the dev PC, CI runs it).

## 2026-11-03: the self-updater retries a failed version and finishes a cut-short update

Asked to make sure the Mac always installs the latest version. The last 7 days on the real Mac were clean
(4,624 "up to date", 7 installs, 13 "waiting for CI", no skipped, rolled-back, refused or error; CI takes 2-7
min and the Mac installs within about 2 min of it passing), so the changes close the gaps found by reading
`installer/self-update.sh`, not a failure seen in practice:

- After a rollback the status file said `rolled-back`, but the next check overwrote it with `skipped`, and the
  check after that no longer saw the failure, so a broken version was installed and rolled back again every
  4 minutes (a backup and a service restart each time). The failed version is now kept in its own file
  (`/var/lib/macserver/self-update-failed.json`), tried again after 30 min and after 4 h (a hiccup then passes),
  and left alone after the third failure until a newer commit arrives or `macserver upgrade` forces it.
- A power loss or crash while an update was being applied left the new source tree in place, so the next check
  said "up to date" with a half-applied install. A marker (`self-update.inflight`) now makes the next check
  finish applying it, or roll back.

Verified: `tests/self_update_test.sh` in WSL Debian (the old script fails the new retry cases, the new one passes
all of them), shellcheck clean. Not verified: either path on the real Mac (it needs a failing update or a power
loss); the installer-image workflow (runs on push).

## 2026-11-04: `doomsday setup-totp` crashed before saving the authenticator

The owner ran `sudo macserver doomsday setup-totp`: the QR code was drawn, then
`lib.sh: line 56: $2: unbound variable`, and nothing was enrolled. `ask "Question"` (used for the
6-digit code) read its optional default as `$2`, which `set -u` refuses. `ask` now treats the default as
optional and shows no empty `[]`; `tests/test_lib.sh` calls it with and without a default (and fails on the
old function). The secret printed in that failed run was never saved on the Mac; rerun setup-totp for a fresh
one and delete the old entry from the phone.

Verified: `tests/test_lib.sh` in WSL against the old and new `lib.sh`; the prompt typed on a pseudo-terminal
(`script`) with and without a default; shellcheck. Not verified: the full `setup-totp` run on the Mac (it needs
the phone and a sudo password; the Mac installs this after the full install test).

## 2026-11-04: the dashboards call a running gateway "on" even without PUBLIC_DOMAIN

Both dashboards said "Public API: off, tailnet only" while Cloudflare served traffic. The real Mac runs the
gateway (the same `gateway/compose.yml`: Caddy plus cloudflared, project `macserver-gateway`) from
`/home/admin/api-gateway`, not from `/opt/macserver/gateway` where `macserver public setup` puts it, and its
hostname is set in Cloudflare, so `PUBLIC_DOMAIN` was never written to the config and that was the only thing
the row looked at. The collector now reports `public_route` from the gateway project's containers (Caddy and
cloudflared running and healthy; other projects' tunnels such as `macserver-apps` do not count). With a
`PUBLIC_DOMAIN` the row still probes `https://DOMAIN/auth/v1/health`. Also fixed: the "public API not answering"
incident looked for project `gateway` and `gateway-cloudflared-1`, but the project is `macserver-gateway`.

Verified: new tests (`public_route`, the Public API row, tests/test_collector.py and tests/test_dashboard.py);
fed the real Mac's `docker ps` through the new code: `{'caddy': 'running', 'tunnel': 'running', 'up': True}`.
Not verified: the rows on the Mac's own screen and web page after it updates. The probe from outside still needs
`PUBLIC_DOMAIN=<hostname>` in `/etc/macserver/macserver.conf` (this Mac has none).

## 2026-11-04: backups go to an encrypted USB drive

The owner plugged in a 233 GB SanDisk and asked for it to be erased and used for backups, with the live data
staying on the Mac. New: `sudo macserver backup-drive setup|status|files` (`installer/steps/65-backup-drive.sh`,
`admin/backupdrive.py`). Setup lists the USB disks the Mac may erase (whole disks over USB, big enough, nothing
mounted; the internal NVMe, zram, partitions and anything in use are refused), shows what is on the chosen one,
and erases it only after `ERASE` is typed. It then partitions it (GPT), LUKS2-encrypts it with a key kept in
`/etc/macserver/backup-drive.key` plus a recovery passphrase shown once, formats it ext4 and records
`BACKUP_DRIVE_UUID`. `macserver backup` then opens the drive, writes the database dump (checked with `gzip -t`),
the Supabase Storage folder and the settings (`.env`, `macserver.conf`; never the TOTP secret or tokens),
keeps the newest 30/7/30 and closes the drive again; `macserver-backup.timer` runs it nightly at 03:30. The
dashboards' backup line says "on the USB drive", and a failed or missed (36 h) backup is a warning (so the text
alerts mention it). `macserver update` asks before going on when the backup fails; self-update still continues.
Doomsday closes the drive if a backup was running. Without a drive, `macserver backup` behaves as before.

Verified: `tests/backup_drive_test.sh` runs the real code against a loop device in WSL Debian: old data erased,
one encrypted partition, recovery passphrase and key both open it, wrong passphrase does not, backup contents,
private file modes, pruning to 30, a failed dump leaves no partial file and keeps the last good record, drive
unplugged, wrong key, everything closed afterwards; `tests/test_backupdrive.py` runs the eligibility rules against
the Mac's real `lsblk` output (only `/dev/sda` is allowed); shellcheck. Not verified: the run on the real Mac
and the real stick (it needs the owner's sudo password and the typed ERASE; the Mac installs this after the full
install test), argon2id timing on 4 GB of RAM, and a restore into Supabase (the steps in docs/GUIDE.md are the
standard Postgres ones; do a trial restore once).

## 2026-11-04: pinned software refreshed (on a branch, merged after the full install test)

Looked up what upstream ships today and moved every pin that had a newer release, checking each new pin the same
way the old one was made (the lookup reproduced all four old pins first):

| Pin | Was | Now | Where |
|---|---|---|---|
| cloudflared image | 2026.10.3 | 2026.10.7 `@sha256:4955524a…` | `gateway/compose.yml` |
| Caddy image | 2.11.4-alpine | 2.11.7-alpine `@sha256:e70b164d…` | `gateway/compose.yml` |
| Claude Code | 2.1.284 | 2.1.318, npm SHA-512 `197fbb09…` | `installer/lib.sh` |
| Supabase self-hosted | v0.8.2 | v0.8.3 (commit `bc27d266…`) | `installer/lib.sh`, docs |
| xterm.js (web terminal) | 5.5.0 | 6.0.0, `addon-fit` 0.10.0 → 0.11.0 | `admin/static/`, hashes in `tests/test_admin.py` |

RustDesk server 1.1.16 is already the newest. Supabase v0.8.3 changes only image versions in its compose (Studio, Auth,
PostgREST, Realtime, Storage, postgres-meta, edge-runtime, Supavisor; the database image and `.env.example` are unchanged).

Verified: tests; xterm.js 6 in a real browser (page builds its screen, 57 rows, text and colours and accents drawn, no
console errors; the shell connection itself was not exercised); the installer-image workflow installs v0.8.3 and the new
Claude Code end to end in a VM before this reaches `main`. Not changed, on purpose: the running Mac's Supabase (self-update
never changes it; this entry first said `install.sh --redo supabase` would move it, which is wrong: that step skips an existing install, see the entry "Supabase upgrades" below) and the Mac's
hand-made gateway/apps/rustdesk-ws tunnels in `~/api-gateway` etc. (not managed by this repo, so still cloudflared 2026.10.3,
which the Mac's own log already calls outdated).

## 2026-11-04: lockdown screens show what happened, not a sample; dead code removed

Cleanup pass (ruff `F,E9,PLE` over `admin tests installer iso host`: 0 findings now). The one finding that mattered
for production: the doomsday lockdown output was part sample data. Both the console banner (`host/doomsday-issue`, copied
to `/etc/issue` and `wall`ed) and the dashboard's lockdown page showed a fixed event log ("17:43:31 INTRUSION DETECTED ...
port 41641 ... 21115-21119"), "12/12 containers terminated", "CONSECUTIVE HITS 2/2", a score out of 100, and "LAN SSH
only" on a Mac with no SSH server. Now:

- `host/macserver-doomsday.sh` publishes `threshold`, `notified` (the alert's HTTP code) and `ssh` (whether an SSH server
  exists) with the lockdown state; the collector passes them on.
- The banner is generated at isolation time by `doomsday.py banner` from that state (time, trigger, score and threshold, the
  detection reasons, whether the alert email was accepted, how to get in); `host/doomsday-issue` keeps only the logo.
- The dashboard page shows the same facts, and its containment rows come from the live status (Tailscale state, whether the
  public tunnel still runs, running containers) instead of fixed words. The SSH line appears only when an SSH server exists.
- The alert email no longer says the Mac is reachable over SSH from the LAN (it is not, normally); it says to go to the Mac.
- Dead code: unused imports and six dead assignments in `admin/tui.py`, `fmt_duration` imported and then redefined, unused
  imports in two tests.

Verified: `tests/test_doomsday.py` (banner facts, box alignment, no invented values anywhere in the banner file, script,
dashboard page, collector), the whole suite (same six Windows-only failures), shellcheck, ruff. Not verified: a real
lockdown (it cuts the network; the banner was rendered from sample state only).

Left alone on purpose: ruff's `B905` (zip without `strict=`) and `B023` in the battery code (the lambda runs inside its own
loop pass, so it is safe).

## 2026-11-04: doomsday no longer scores multicast chatter or processes inside containers

The owner ran `sudo macserver doomsday test` as root on the real Mac: score 5 (threshold 6), from "unexpected outbound
this Mac -> 239.255.255.250:1900" (2) and "unknown root process: MainThread (pid 1412006)" (3). Both false alarms, one finding
short of isolating the Mac (which cuts Tailscale):

- 239.255.255.250:1900 is SSDP discovery (Tailscale's port mapping sends it). `ip_kind` called multicast "public"; it is now
  "lan", and the detector also skips multicast and broadcast destinations itself.
- `MainThread` is a Node app inside a Docker container: root on the host, root only in its container. The collector now marks
  each listed process that runs in a container (`in_container`: its cgroup is a docker/containerd scope) and the detector
  trusts those unless `DOOMSDAY_TRUST_CONTAINERS=false`. On this Mac the live list also holds `beam.smp` (Supabase Realtime),
  `next-server` and more root container processes; any of them could have been the next false finding.
- The banner and lockdown page said "isolates over 6"; the code isolates at 6 or more (`score >= threshold`). Fixed, and the
  security doc says "at least".

Verified: new tests (multicast, container trust and its off switch, real-Mac pair together scores 0; `ip_kind`,
`in_container`); the new detector run against the real Mac's live `status.json` from a temporary folder: score 0 (was 5), all
nine root processes in the list classified (host services by name, container processes by cgroup). Not verified: the
collector's own `container` field on the Mac (it appears after this update) and, as before, a real lockdown.

## 2026-11-04: Supabase upgrades (`macserver supabase-upgrade`)

The owner asked for the live Mac's Supabase to move from v0.8.2 to the pinned v0.8.3. There was no way to do that: `step_supabase`
installs once and skips an existing install, so `install.sh --redo supabase` upgrades nothing (and would have re-asked the
sign-up question, whose answer rewrites `DISABLE_SIGNUP`); self-update "never changes Supabase's version" by design;
`macserver update` only pulls the images the installed compose already names.

New: `sudo macserver supabase-upgrade [--check] [--yes]` (`installer/steps/66-supabase-upgrade.sh`). It reads the installed
tag from `.supabase-version` (installs without it are taken to be v0.8.2, the previous pin, and the checks below catch a wrong
guess), fetches both tags (blobless, depth 1) and compares only `docker/`:

- stops, changing nothing, if the tags differ under `volumes/` or in `.env.example` (settings or database setup: a person reads
  that), or if a file it would replace is not byte-for-byte the stock file of the old tag (the owner edited it);
- checks the new tag against the pinned commit, and needs 6 GB free;
- backs up first (`cmd_backup`, so to the USB drive) and refuses to go on without one; then asks (`--yes` skips the question);
- replaces only the changed files (executable bits kept), never `.env`, `docker-compose.macserver.yml` or the data under
  `volumes/`; pulls, restarts with `docker compose up -d --wait`, probes `/auth/v1/health` through the gateway;
- on any failure puts the old files back and restarts the old version (the old images stay on disk; the services' own database
  migrations are forward-only, so the backup is the way back from those); the replaced files stay in `.upgrade-backup-<time>/`.

v0.8.2 -> v0.8.3 changes only `docker-compose.yml` (images: Studio, Auth, PostgREST, Realtime, Storage, postgres-meta,
edge-runtime, Supavisor, plus one edge-runtime flag) and some docs/tests; the database image and `.env.example` are unchanged.

Verified: `tests/supabase_upgrade_test.sh` (dry run; the upgrade with secrets, data and the MacServer override untouched and
modes kept; already current; no version file; an edited file; upstream settings changes; a wrong pin; a failed backup; failed
pull, restart and health probe each rolled back; bad use), shellcheck. Not verified: the run on the real Mac (needs sudo, the
~2 GB image download and a minute or two of downtime) and that the real v0.8.2 files there are stock (the command checks).

## 2026-11-04: the first real lockdown was a false alarm; restore now restarts every container

At 10:58 UTC the Mac isolated itself (trigger auto, score 13, threshold 6). The alert email went out first (GitHub run
41817769026, HTTP 204), the lockdown screens showed the real facts, and the owner restored it with an Authenticator code at
the Mac's keyboard (`doomsday off`, then `restore --code`). Nothing in the findings was an intrusion:

- 10 points: five cloudflared connections to Cloudflare's tunnel edge on port 7844 (198.41.192.x / 198.41.200.x, UDP/QUIC).
  The collector names a flow by its container (`macserver-gateway-cloudflared-1`, `macserver-apps-cloudflared-1`) or, for the
  host-network RustDesk tunnel, "this Mac", so the `cloudflared` allowlist entry never matched, and 7844 is no trusted port.
  The detector now trusts port 7844 to Cloudflare's own ranges (198.41.128.0/17, 2606:4700::/32) whoever sends it; 7844 to
  anywhere else, and other ports to Cloudflare, still score. The dashboards call the port "cf tunnel".
- 3 points: `irq/9-acpi`, a kernel thread (and `kworker/u16:2-kcryptd-25` scored right after the restore). The detector only
  trusted names in `[brackets]`, which `/proc/PID/stat` never gives, and which any process can choose. The collector now marks
  each process `kernel` from the kernel's own PF_KTHREAD flag (stat field 9) and the detector trusts that mark only.
- Restore brought back only Supabase: every other container (apps, the hand-made gateway in `~/api-gateway`, RustDesk,
  PlaneSight; all `restart: unless-stopped`) stayed exited. The pre-isolation snapshot now lists every running container by
  name, and restore starts each of them again, naming any it cannot start. This time the 16 containers were started by hand
  over SSH (each had stopped between 10:58:34 and 10:58:44 UTC).

Verified: `tests/doomsday_restore_test.sh` (new, in CI; stub docker/systemctl/tailscale: snapshot lists the containers,
restore starts them and survives a vanished one, docker down, an old snapshot without the list; 6 of its checks fail on the
old script), new detector/collector tests, the whole suite in WSL Debian (255 tests), shellcheck; the new detector and
collector run read-only on the real Mac's live data: score 0 (was 9 with the tunnels back up), `irq/9-acpi` marked kernel,
`dockerd`/`containerd`/`systemd` not. Not verified: a full isolate -> restore on the Mac with the new script (it cuts the
network). Detection is OFF on the Mac until this update is installed and `doomsday test` is quiet; then
`sudo macserver doomsday on`.

## 2026-11-04: notices say what they are, on the screen and with `macserver issues`

The owner saw "1 NOTICE(S)" on the Mac's screen with nothing saying what it was: the normal dashboard only counted
warnings, and the details existed only on the red incident screen. It was "restart needed for updates": the T2 kernel
`linux-image-6.18.60-1-t2-trixie` was installed on 2026-11-03 and `/run/reboot-required` asks for a reboot (the fix is
`sudo reboot`, then the disk passphrase at the keyboard; the owner's call).

- Screen: one line per counted issue under the top bar (level, title, first fix, why; up to 3, then "+N more:
  macserver issues"; 1 on screens under 30 rows), on the normal pages and on the overview during an incident (Space).
- Terminal: `macserver issues` (`dashboard --issues`, no root needed) prints the same list the pill counts, each with
  why, facts, fixes, files and logs; `macserver status` now starts with the one-line-each form (`--issues --brief`).
  Muted benign notices stay out of both, as they stay out of the pill (`tui.counted_issues`).
- `macserver status` itself was broken in every state: a stray `}` in its Doomsday line made jq refuse the whole program
  (found running it on the Mac). Fixed; `tests/test_repo.py` now runs its jq program for a normal and an isolated status.

Verified: new tests (the notice line at three sizes, the overview during an incident, the report full/brief/healthy/
critical/muted, `--issues` as a process, the status jq program), whole suite in WSL (259 tests), shellcheck; the new
dashboard run read-only on the real Mac: the line reads "WARN restart needed for updates fix: sudo reboot (then type the
disk passphrase)" and `--issues` prints why/fix/look. Not verified: the line on the Mac's own screen after it updates.
