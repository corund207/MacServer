# Handoff: finish the Wi-Fi / clock fix for the USB installer

Paste the prompt below into a new chat in this repository.

---

Read `AGENTS.md`, `context/STATUS.md`, `docs/DEVELOPMENT.md` and this file first.

## Situation

I am installing MacServer on my 2019 MacBook Air (T2, MacBookAir8,2) with my personal
USB image (`Downloads\macserver-personal-wifi.iso`, built with bundled Apple Wi-Fi
firmware; see "Personal image" in `docs/DEVELOPMENT.md`). On the Mac:

- The image boots, the T2 kernel works, the Wi-Fi firmware loads, and `nmtui-connect`
  connects to my Wi-Fi (the network then shows "Deactivate", which means connected).
- The installer's Internet check still says "not connected". In the shell, `date`
  showed the **clock is wrong** (a Mac whose battery ran flat). So every HTTPS check
  fails (certificates look invalid). The live system has no time sync.
- Setting the time by hand with `date -s "$(curl -sI http://deb.debian.org | grep -i
  '^date:' | cut -d' ' -f2-)"` did **not** work on the Mac. Cause unknown (maybe the
  network blocks plain HTTP, maybe DNS). Ask me for the output of the diagnostic
  commands below if you need it.

## Work in progress (uncommitted, on `main`, last commit 5af6f62)

1. `installer/lib.sh`: new clock helpers at the end: `sntp_time IP` (SNTP over UDP 123
   by IP, no DNS, via python3), `http_time URL`, `network_time` (tries
   Cloudflare/Google NTP IPs first, then HTTP Date headers), `set_clock` (also
   `hwclock --systohc --utc` when available), `sync_clock`, `https_works`. Checked in
   WSL: all four NTP IPs and the HTTP fallback return the correct time.
2. `iso/setup/macserver-setup`: `setup_network` rewritten: waits up to 20 s for a
   link, then calls `sync_clock` and retries. `net_problem` explains why the check fails
   and is shown in the menu. The Wi-Fi hint now says to choose **Quit** when the network
   shows "Deactivate". BUT it still contains its own old `sync_clock` (HTTP-only,
   around line 133) that shadows the new lib version: **delete it**, so the lib one is used.
3. `tests/iso_install_test.sh`: phase 1 VM starts with `-rtc base=2019-06-01` to
   simulate the wrong clock.

## To do

1. Delete the old `sync_clock` in `macserver-setup`. In `setup_network`, call
   `sync_clock` as soon as there is any route (`can_reach_ip` or `can_resolve`), before
   the first `online` check. Also call it at the start of `install_debian`.
2. Add a menu option **"Set the date and time by hand"**: ask for local date/time
   (`YYYY-MM-DD HH:MM`) and a time zone (default UTC), apply with
   `TZ=zone date -s ...`, then `hwclock --systohc --utc`.
3. Add `util-linux-extra` (provides `hwclock`) to `iso/base/packages.txt` and to the
   target package list in `install_debian`. The target already installs
   `systemd-timesyncd`; make sure it is enabled.
4. First boot (`installer/steps/10-system.sh`, `step_network`): if DNS works but
   `https_works` fails, call `sync_clock` and retry before failing.
5. Make `online` / `net_problem` show the clock (and certificate errors) clearly.
6. Test locally in WSL (Debian with KVM is already set up):
   - `wsl -d Debian -u root -- bash tests/local.sh disk`
   - rebuild the base (package list changed):
     `rm -rf /var/tmp/macserver-local/repo/build/base` inside WSL, then
     `MACSERVER_WIFI_FIRMWARE=/var/tmp/macserver-wifi-firmware.tar bash tests/local.sh image`
     (the firmware tar already exists in WSL at that path)
   - `FULL_UEFI=1 bash tests/local.sh vm`: must pass with the VM clock in 2019.
   Run `bash tests/run.sh` checks (ShellCheck, Python tests). Keep files LF.
   Tip: PowerShell expands `$(...)` inside `wsl ... bash -c "..."`, so put commands in
   a script file.
7. Copy `/var/tmp/macserver-local/repo/build/macserver-local-personal.iso` to
   `Downloads\macserver-personal-wifi.iso` (verify SHA-256). Never commit or publish it.
8. Commit (repo identity is configured, no co-author lines), push, update
   `context/STATUS.md`. Then walk me through reflashing and booting the Mac again.

## Diagnostics to ask me for if the new image still fails on the Mac

In the installer menu choose "Open a shell", then:

```
date
ip addr show | grep -E 'wl|inet '
getent hosts deb.debian.org
curl -sSI http://deb.debian.org | head -3
curl -sSI https://deb.debian.org | head -3
cat /tmp/macserver-setup.log | tail -20
```
