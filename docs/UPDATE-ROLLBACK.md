# Change and rollback gates

The Phase 1 bundle is a read-only review artifact. Re-stage changes to a fresh
path; retain the old bundle and compare manifests/content. No rollback command is
needed for a host that has not been changed. Do not use staging to apply `/etc`.

For future host changes, record the exact package versions and effective settings,
securely back up the files to be replaced, and demonstrate restoration in an
isolated Debian 13 VM. Retain console access and a second tested SSH session.
Validate configuration before reload; change one subsystem at a time. If independent
login, time sync, network, or health fails, use the console to restore the saved
files and reload the affected service, then retest. A target-specific, rehearsed
command list is required before application; this document is not that rehearsal.
Never flush a firewall remotely or replace the whole tailnet policy blindly.

Before data-service upgrades, require maintenance mode, a verified backup of data,
Storage objects and configuration, independent secret escrow, and a successful
isolated restore. Check migration compatibility with old binaries. An image revert
cannot reverse schema/data migrations. Do not delete volumes, automatically run
`compose down -v`, or restore over live data. Destructive restore and disk work
require immediate explicit confirmation plus the tested recovery path.

Phase 5 provides the repository-side backup, integrity, retention and isolated restore
workflow in `docs/BACKUP-RESTORE.md`. It remains an inert candidate until the exact
target drive, Docker stack and recovery drill pass. A local USB repository alone is
not a complete disaster-recovery plan.

## Data-service update sequence

There is no unattended image update. Start from a clean committed release, record the
current Git commit, Compose render, release locks and running image digests, and stage
the proposed release in a separate checkout. Resolve every new tag to an amd64 digest;
review upstream migration and rollback notes, the Supabase changelog, PostgreSQL major
compatibility, environment changes and health checks. Never replace a digest in place
without regenerating and testing the lock.

Before changing the target, require all of the following:

- console access and a second working key-only SSH session over Tailscale;
- maintenance behavior that rejects new writes and a verified drain of in-flight work;
- a backup less than 24 hours old, a repository subset check, and an isolated restore
  drill less than 30 days old, with independent restic password escrow;
- a disposable target-equivalent rehearsal of Compose parsing/startup, database and
  application migrations, RLS negative cases, Storage reads, and rollback;
- adequate disk/thermal headroom and no unresolved SMART, time-sync or reboot alert.

The public ingress layer is not yet implemented, so there is currently no verified
maintenance switch. That blocks a production data-service update even when backup
status is healthy. Do not mistake a marker file or dashboard message for write drain.

During an authorized window, retain maintenance mode, change one subsystem at a time,
and capture generic timestamps/results without secrets. Use `docker compose config
--quiet` before any create/start operation. Pull pinned digests before stopping the
old service. After each step require service health, local API probes, application
authorization checks and RLS negative tests. A timeout or failed gate stops the change;
it does not justify deleting volumes or widening access.

Rollback means returning to the recorded Git/config/image set only when its binaries
remain compatible with the current data. If a migration is not backward compatible,
an image rollback is unsafe: keep maintenance active and use the rehearsed restore into
new isolated volumes after immediate explicit approval. Preserve the failed version
and logs for diagnosis. End maintenance only after the complete application path,
backup timer, dashboard, private administration and exposure tests are healthy.
