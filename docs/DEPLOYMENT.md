# Appliance commissioning and GitHub updates

The repository now has a fail-closed deployment path for an empty, private MacServer
stack. It does not format disks, install Debian/packages, alter the firewall, create
secrets, import data, expose ports, or approve production. Complete the README through
Docker qualification first. Run commands only on the designated MacBook.

## Prepare private configuration

As the ordinary operator, update a clean checkout and run the complete verifier. Use
the full commit printed by Git; the installer refuses dirty or non-upstream HEADs.

```sh
cd "$HOME/MacServer"
git pull --ff-only
python3 scripts/release_verify.py --full --require-clean --require-upstream-sync \
  --output build/release-verification.json
git rev-parse HEAD
```

Generate Supabase secrets as described in the README. Then install them and the
runtime profile without printing their contents:

```sh
sudo install -d -m 0700 /etc/macserver
sudo install -o root -g root -m 0600 \
  "$HOME/.macserver-private/macserver-supabase/.env" /etc/macserver/supabase.env
sudo install -o root -g root -m 0600 infra/deploy/runtime.env.example \
  /etc/macserver/runtime.env
sudo install -o root -g root -m 0600 infra/deploy/qualification.example.json \
  /etc/macserver/qualification.json
sudo install -o root -g root -m 0600 infra/deploy/update-approval.example.json \
  /etc/macserver/update-approval.json
```

Edit the private qualification file as root. Set `approvedCommit` to the exact full
commit, `checkedAt` to the current UTC timestamp, and each check to `true` only after
collecting that evidence on this MacBook. The evidence expires after seven days.
Never weaken the checks or commit the completed file.

Review `COMPOSE_PROFILES` in `/etc/macserver/runtime.env`. Start with `core`; add only
comma-separated `functions`, `storage`, `realtime`, or `management` profiles that were
needed and qualified. Studio remains private even when management is enabled.

## Commission a new empty appliance

This changes `/opt`, installs/enables one systemd unit, pulls pinned images, and creates
new Docker state. It refuses an existing MacServer release or Compose containers. It
does not delete anything. Review the full commit again immediately before running:

```sh
git rev-parse HEAD
sudo python3 scripts/appliance.py commission --source "$PWD" \
  --confirm-commit FULL_40_CHARACTER_COMMIT
sudo python3 /opt/macserver/scripts/appliance.py status
```

Expected: `PASS: commissioned private MacServer release ...`, followed by JSON service
state. The Compose unit uses the internal-only network, publishes no ports, waits up to
180 seconds for health, and starts after reboot. On a first-start failure it stops the
unit and preserves containers, logs, configuration, and release files for diagnosis.
Inspect `systemctl status macserver-data` and `journalctl -u macserver-data` locally;
never delete volumes to retry.

Commissioning is approval for an empty private rehearsal only. Before production data,
complete encrypted backup initialization and restore, real RLS/application tests, and
migration rehearsal. Public application ingress is still a separate blocked feature.

## Update from GitHub

Updates are operator-triggered, not unattended. GitHub CI must pass first. The update
command accepts only the configured MacServer GitHub origin, a clean fast-forward pull,
the exact upstream commit, fresh qualification for that commit, a healthy backup under
24 hours, an isolated restore drill under 30 days, and a one-hour update approval that
confirms writers are drained and data compatibility was reviewed. It never runs SQL
migrations or deletes volumes.

```sh
cd "$HOME/MacServer"
git pull --ff-only
python3 scripts/release_verify.py --full --require-clean --require-upstream-sync \
  --output build/release-verification.json
git rev-parse HEAD
# Update /etc/macserver/qualification.json for this exact commit and current evidence.
# In /etc/macserver/update-approval.json, record current/target commits and current UTC
# time, then set its booleans true only after maintenance and compatibility review.
sudo python3 /opt/macserver/scripts/appliance.py update --source "$PWD" \
  --confirm-commit FULL_40_CHARACTER_COMMIT
sudo python3 /opt/macserver/scripts/appliance.py status
```

The updater copies tracked files into `/opt/macserver-releases/<commit>`, pulls the
new pinned images before switching `/opt/macserver`, and restarts through the health-
gated unit. If activation fails, it switches the release pointer back and restarts the
previous release. Database/schema compatibility must still be reviewed: reverting an
image cannot undo a data migration.

## Manual rollback

Rollback is disruptive and requires a current healthy backup/restore drill. Confirm
both installed commit IDs and compatibility before running:

```sh
sudo python3 /opt/macserver/scripts/appliance.py rollback \
  --to PREVIOUS_FULL_COMMIT --confirm-current CURRENT_FULL_COMMIT
sudo python3 /opt/macserver/scripts/appliance.py status
```

The tool only switches between already-installed immutable releases. If restart fails,
it restores the original pointer. It never restores or overwrites database data. Keep
maintenance active and follow `UPDATE-ROLLBACK.md` when data compatibility is uncertain.

## Emergency stop

Stopping is non-destructive and preserves all data:

```sh
sudo systemctl stop macserver-data.service
```

Do not remove the approval file, releases, containers, or volumes while diagnosing.
