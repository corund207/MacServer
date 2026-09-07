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

Phase 5 will implement encrypted removable-drive backups, mount UUID verification,
absent-drive failure/alerts, capacity checks, retention, integrity checking and a
restore drill. None of these exists in Phase 1. Until then, stateful deployment
and production migration remain gated on a separately verified recovery plan.
