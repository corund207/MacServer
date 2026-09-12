# Encrypted backup and restore runbook

This repository provides a fail-closed, local-restic workflow for PostgreSQL logical
exports, Storage objects, service configuration and encrypted secret material. It is
not installed by the repository. A successful command is evidence about one snapshot,
not proof that the appliance can survive loss of both the internal SSD and USB drive.

The script writes only after matching all of these facts: the configured absolute
mountpoint, exact filesystem UUID, ext4/btrfs/xfs, `rw,nodev,nosuid,noexec`, one
removable 200–300 GiB block device, at least 20 GiB free, and a private sentinel.
An absent or substituted drive exits 2, writes a bounded `failed` status on the
internal disk, and does not affect live services. Repository and password paths are
passed as argument vectors, never interpolated into a shell.

## Prerequisites and one-time initialization

Complete these on the separately authorized Debian 13 target. Formatting a disk is
destructive and is deliberately not automated or described here; inspect the device,
preserve anything needed, and obtain fresh explicit confirmation before doing it.

1. Identify the already-created filesystem and its exact UUID with `lsblk -f`. Create
   `/mnt/macserver-backup`, `/etc/macserver-backup`, and
   `/var/lib/macserver-backup/staging` on the internal encrypted SSD. The two
   configuration directories must be root-owned mode 0700 or 0750 as appropriate.
2. Add an `/etc/fstab` entry using the real UUID, for example:

   ```fstab
   UUID=EXACT-UUID /mnt/macserver-backup ext4 noauto,nofail,rw,nodev,nosuid,noexec 0 2
   ```

   Mount it explicitly, then check `findmnt -no TARGET,SOURCE,FSTYPE,UUID,OPTIONS
   /mnt/macserver-backup`. `noauto` makes removal safe but means an operator must mount
   the drive before a scheduled run. Do not weaken the script's identity gates.
3. Download the locked restic archive from `infra/backup/release.lock.json` to a new
   temporary directory, separately compare its SHA-256 to the lock, decompress it,
   install it as `/opt/macserver-runtime/restic-v0.19.1/restic` mode 0755, and confirm
   `restic version` reports exactly 0.19.1. Do not pipe a download to a shell and do
   not substitute a moving `latest` artifact.
4. Copy `infra/backup/config.example.json` to
   `/etc/macserver-backup/config.json`, substitute only the observed UUID, review
   every path and keep it mode 0600. Provision `/srv/macserver/storage` on the
   encrypted internal SSD with ownership required by the pinned Storage image.
5. Generate the restic password once with `scripts/init_secrets.py` as described in
   `docs/INSTALL.md`. Put a mode-0600 target copy at
   `/etc/macserver-backup/restic-password`. Keep a second copy in an independent
   encrypted password manager or physical recovery escrow. It must not exist only on
   the appliance or backup drive. Never print or paste it into a command line.
6. Review the detected UUID one last time and initialize a new repository. Replacing
   `EXACT-UUID` is a required operator confirmation:

   ```sh
   sudo python3 /opt/macserver/scripts/backup.py \
     --config /etc/macserver-backup/config.json \
     --password-file /etc/macserver-backup/restic-password \
     init --confirm-uuid EXACT-UUID
   ```

   Expect `init completed`. If restic initialization succeeds but sentinel creation
   fails, preserve the partial repository for inspection; do not delete or retry over
   it blindly. A second initialization always refuses.

## Backup, integrity, and status

The backup creates a custom-format `postgres` database dump and a globals-only dump,
then snapshots those exports plus the explicit source allowlist. The default sources
cover Storage objects, `/etc/macserver` (including the Supabase environment), and the
deployed infrastructure. Temporary logical exports live only in a private staging
directory on the encrypted internal SSD. After each snapshot, restic checks a random
5% data subset. All operations disable plaintext local cache. The timer repeats this daily; a second timer performs
the repository check weekly.

Before relying on this online workflow, verify that applications mutate Storage only
through the Storage API. A concurrent upload can cross the database-dump/file-copy
boundary. For a recovery-point snapshot before updates or migration, put every writer
into the separately verified maintenance mode, wait for in-flight writes to drain,
then run the backup and keep maintenance mode active until the update decision.

Manual qualification command:

```sh
sudo python3 /opt/macserver/scripts/backup.py \
  --config /etc/macserver-backup/config.json \
  --password-file /etc/macserver-backup/restic-password backup
sudo cat /var/lib/macserver-backup/status.json
```

Expect `backup completed`. Status is `degraded` until a successful restore drill is
less than 30 days old, then `healthy`; it records timestamps and generic outcomes,
not filenames, credentials or command output. Failure exits 2 and records `failed`.
Inspect `journalctl -u macserver-backup.service` for the generic refusal code. Fix the
cause and rerun; do not relabel failed evidence.

After target review, install the four candidate units from `infra/backup`, run
`systemd-analyze verify`, create the untracked root-owned
`/etc/macserver-backup/DEPLOYMENT-APPROVED` marker, and enable both timers. Verify with
`systemctl list-timers 'macserver-backup*'`. Removing the marker and disabling the
timers is the non-destructive rollback; it does not remove snapshots or live data.

Retention is intentionally not scheduled because pruning deletes repository data.
Review `restic snapshots` and the 7-daily/5-weekly/12-monthly policy, then provide the
freshly observed UUID as immediate confirmation:

```sh
sudo python3 /opt/macserver/scripts/backup.py \
  --config /etc/macserver-backup/config.json \
  --password-file /etc/macserver-backup/restic-password \
  retention --confirm-uuid EXACT-UUID
```

## Isolated restore drill

Use a new target below the configured private restore parent on the encrypted internal SSD. The drill
restores and hash-checks the logical dumps, launches the exact pinned Supabase
PostgreSQL image with no network and tmpfs-only database state, loads the database,
runs a catalog query, and removes only its generated container. It does not touch the
live database or volumes.

```sh
sudo install -d -m 0700 /var/lib/macserver-backup/restores
sudo python3 /opt/macserver/scripts/backup.py \
  --config /etc/macserver-backup/config.json \
  --password-file /etc/macserver-backup/restic-password \
  restore-drill --target /var/lib/macserver-backup/restores/DRILL-ID \
  --confirm-uuid EXACT-UUID
```

Expect `restore-drill completed` and `healthy` status. On failure, the new restore
directory remains for inspection. It can contain decrypted configuration and secrets;
restrict access and, only after review and fresh confirmation, remove it securely.
The globals dump is integrity-checked but not replayed because the pinned target image
already contains Supabase system roles. A production rehearsal must separately compare
and reconcile application roles without overwriting built-in roles.

A production restore is intentionally not automated. First preserve the failed live
state, verify the selected snapshot and secret escrow, repeat the isolated drill,
write a target-specific recovery/rollback plan, stop all writers, and obtain immediate
explicit approval. Restore into new volumes/paths and cut over only after health and
RLS-negative tests. Never restore over live data or use `docker compose down -v`.

## Off-host recovery

Rotate at least two removable drives and keep one disconnected in a different physical
location. For stronger disaster recovery, create a second independently encrypted
restic repository with separate credentials at a reviewed off-host destination and
copy snapshots while the source repository is not being pruned. Test restore from that
destination on a schedule. Confirm provider retention, access revocation, cost and data
residency before enabling network access; this local workflow itself has no network.
