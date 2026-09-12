# Local dashboard deployment and recovery

The dashboard is a read-only local-console display. Its HTTP listener must remain
on `127.0.0.1:7460`; it has no remote route, credentials, mutation endpoint, Docker
socket, database connection, or access to secret files. A separate root collector
runs fixed read-only CLI commands and writes a bounded `0640` snapshot to
`/run/macserver/status.json`. Missing or older-than-60-second evidence is visible.

## Target prerequisites and approval

On the Debian 13 target, verify the pinned Node runtime, Docker Compose, Tailscale,
Chromium package, display manager, graphics acceleration, temperature sensor source,
and dedicated `macserver-dashboard` user/group. The supplied units are inert until
an operator creates `/etc/macserver-dashboard/DEPLOYMENT-APPROVED` after those checks.
Do not create that marker from automation or on this development host.

Before installation, copy the candidate units into an isolated Debian 13 VM and run:

```sh
systemd-analyze verify infra/dashboard/*.service infra/dashboard/*.timer
python3 scripts/collect_status.py --compose /absolute/review/compose.json \
  --backup-status /absolute/review/backup.json \
  --request-status /absolute/review/requests.json \
  --output /absolute/private/status.json
curl --fail --max-time 2 http://127.0.0.1:7460/healthz
ss -ltnp | grep 127.0.0.1:7460
```

Expected: unit verification has no errors; the snapshot contains only state/detail
metadata; health returns `{"status":"serving"}`; and no non-loopback dashboard
listener exists. Inspect the snapshot before letting the display user read it.
The collector's Docker access is privileged; never reuse it as an HTTP helper.

Install `macserver-kiosk.service` as a user unit for the dedicated graphical login.
Its exact session activation differs by display manager and must be tested after a
reboot. The kiosk profile is local state, not a general browsing profile. Disable
navigation, remote debugging, sync, extensions, autofill, and external URLs through
managed Chromium policy on the target; command flags alone are not a security boundary.

## Recovery tests

With console access, stop the dashboard service. Chromium should retain the last
screen and show a degraded connection within 15 seconds; systemd should restart the
service. Kill Chromium and verify its user service restarts. Disconnect networking:
local host readings continue while Tailscale and request evidence becomes degraded or
stale. Reboot and verify the collector, dashboard, and graphical-session user service
recover without making the dashboard remotely reachable. Record timestamps and logs:

```sh
systemctl status macserver-dashboard macserver-status-collector.timer
systemctl --user status macserver-kiosk
journalctl -u macserver-dashboard -u macserver-status-collector --since -15m
```

Rollback: remove the approval marker, stop/disable the three units, restore the saved
display-manager configuration, and verify port 7460 is closed. Removing the marker or
units does not delete appliance data. Preserve logs for diagnosis.

Temperature remains unavailable until the exact hardware sensor and safe read path are
qualified. Request rates require a separate ingress exporter that writes only bounded
counts to `/var/lib/macserver-ingress/status.json`. Never give the display raw access
to gateway logs. Dashboard recovery is not proof of database or backup recovery.
