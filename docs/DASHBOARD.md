# Local console display

The MacBook's own screen shows a read-only, btop-style terminal display on `tty1`:
an overall verdict, CPU history with per-core meters, memory, disk, power, network,
service health, alerts, and Tailscale, backup and request state. It replaces the earlier
Chromium kiosk, so the appliance needs no browser, display manager or graphical session.

The display (`apps/console/macserver_top.py`) is standard-library Python. It reads
`/proc`, `/sys` and the collector's bounded snapshot at `/run/macserver/status.json`.
It opens no network socket, runs no commands, writes no files and, in kiosk mode,
ignores every key, including `q` and Ctrl+C. A separate root collector runs fixed
read-only Docker and Tailscale queries every 15 seconds. The display never gets
Docker, database or secret access.

## What the screen means

| Verdict | Meaning |
| --- | --- |
| NOMINAL | Fresh evidence, and everything observed is healthy. |
| ATTENTION | Something is degraded, or the snapshot is older than 60 seconds. |
| ACTION REQUIRED | A verified failure, such as a stopped container. |
| UNVERIFIED | No valid collector snapshot. Unobserved is never shown as healthy. |

Text from the snapshot is reduced to printable single-width characters before it
reaches the terminal, so status data cannot send escape codes to the console.

## Prerequisites and approval

On the Debian 13 target, confirm the `python3` package, the dedicated
`macserver-dashboard` user and group, and a console font with box drawing and block
elements. For the Retina panel, a large Terminus font keeps the text readable:

```sh
sudo sed -i 's/^CODESET=.*/CODESET="Uni2"/; s/^FONTFACE=.*/FONTFACE="Terminus"/; s/^FONTSIZE=.*/FONTSIZE="16x32"/' /etc/default/console-setup
sudo setupcon --save
```

This changes only the console font. The units stay inert until an operator creates
`/etc/macserver-dashboard/DEPLOYMENT-APPROVED` after the checks below. Do not create
that marker from automation or on a development machine.

## Preview anywhere

```sh
python3 apps/console/macserver_top.py --demo              # live curses view, q quits
python3 apps/console/macserver_top.py --demo --once 160x50  # one plain-text frame
```

`--demo` uses synthetic data and labels every frame DEMO DATA. Without `--demo`, the
display reads this machine's `/proc` and the snapshot path given by `--status`.

## Install on the appliance

Validate the units first, in an isolated Debian 13 VM or on the target:

```sh
python3 scripts/validate_dashboard_units.py
python3 scripts/collect_status.py --compose /opt/macserver/infra/supabase/compose.json \
  --output /tmp/macserver-status-review.json
python3 apps/console/macserver_top.py --status /tmp/macserver-status-review.json --once 160x50
```

Expected: the unit parser passes, and the frame shows real host readings and the
collector's service states. Then install and start them. This replaces the login
prompt on `tty1`; local logins remain available on `tty2` (Ctrl+Alt+F2):

```sh
sudo install -m 0644 /opt/macserver/infra/dashboard/macserver-status-collector.service \
  /opt/macserver/infra/dashboard/macserver-status-collector.timer \
  /opt/macserver/infra/dashboard/macserver-console.service /etc/systemd/system/
sudo touch /etc/macserver-dashboard/DEPLOYMENT-APPROVED
sudo systemctl daemon-reload
sudo systemctl disable --now getty@tty1.service
sudo systemctl enable --now macserver-status-collector.timer macserver-console.service
```

On `TERM=linux` the display turns off console blanking and powerdown so the screen
stays on. Lid, backlight and thermal behaviour still follow `infra/host/logind.conf`
and the hardware qualification.

## Recovery tests

With console access:

1. Stop the collector timer. Within 60 seconds the verdict becomes ATTENTION with
   "Collector evidence is stale"; host readings keep updating.
2. Kill the display process. systemd restarts it on `tty1` within a few seconds.
3. Press keys and Ctrl+C on the Mac's keyboard. Nothing changes.
4. Disconnect networking. Host readings continue; Tailscale and request evidence
   degrade.
5. Reboot. The collector and display return without a login.

```sh
systemctl status macserver-console macserver-status-collector.timer
journalctl -u macserver-console -u macserver-status-collector --since -15m
```

## Rollback

Non-destructive. It deletes no appliance data:

```sh
sudo systemctl disable --now macserver-console.service macserver-status-collector.timer
sudo rm /etc/macserver-dashboard/DEPLOYMENT-APPROVED
sudo systemctl enable --now getty@tty1.service
```

Temperature comes from the kernel's `coretemp` or `applesmc` sensors when present.
Request rates need a separate ingress exporter that writes only bounded counts to
`/var/lib/macserver-ingress/status.json`; never give the display raw gateway logs.
A healthy display is not proof of database or backup recovery.
