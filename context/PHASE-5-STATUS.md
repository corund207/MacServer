# Phase 5 — encrypted backup and recovery candidates

2026-09-11. The repository implementation and synthetic verification for encrypted
removable-drive backups, retention, integrity checks, isolated restore drills and
update/rollback gates are complete. No drive was formatted or mounted, no repository
was initialized, no Docker container or systemd service was started, and no production
data or secret was read. This is not target recovery qualification.

## Delivered scope

- Pinned official restic 0.19.1 linux/amd64 binary metadata with independently checked
  SHA-256, version output and relevant command help.
- Fixed-command backup tool with private configuration/password validation, exclusive
  locking, exact mountpoint/filesystem UUID/sentinel/filesystem/options/removable-device/
  capacity/free-space gates, and safe absent-drive failure status.
- PostgreSQL custom-format and globals logical exports, explicit Storage/configuration
  source allowlist, encrypted restic snapshot, 5% repository data check, atomic bounded
  status evidence and 7-daily/5-weekly/12-monthly confirmed retention.
- Restore only to a new destination, hash verification, and a pinned PostgreSQL restore
  container with no network and tmpfs database state. Production overwrite is absent.
- Inert daily backup and weekly check systemd candidates protected by an untracked
  approval marker and systemd credential loading.
- Operator initialization, escrow, absence/failure, retention, off-host, restore,
  production-recovery and update/rollback guidance.
- Storage object data moved from an opaque named volume to the explicit encrypted-SSD
  path `/srv/macserver/storage`, which both Storage and read-only imgproxy mount and the
  backup allowlist covers.

## Verification evidence

- Backup unit tests use disposable paths and a fake fixed-command runner; they do not
  access a drive, Docker daemon, database or secrets.
- Wrong filesystem UUID, non-removable device, unsafe mount flags, missing confirmation,
  unsafe password permissions and placeholder configuration are refusal cases.
- Synthetic backup evidence verifies both logical exports, encrypted snapshot command,
  subset check, no password value in command arguments, retention prune and a no-network
  restore/database-load path.
- Python: 26 tests pass; all foundation, admin, dashboard and backup unit parsers pass;
  Compose deterministically regenerates and parses without daemon use.
- Admin: 13 Node tests and 5 browser/accessibility tests pass; dashboard: 3 Node tests
  and 4 browser tests pass. Both production dependency audits report zero findings.

CI performs only offline/synthetic checks; it cannot certify a physical backup.

## Target blockers and residual risk

The exact 250 GB drive UUID, filesystem, SMART/physical health, mount behavior, write
performance, removal during backup, power-loss behavior and restore time are untested.
Restic and the pinned PostgreSQL image must be authenticated/downloaded and tested on
the target. A real Supabase logical dump may expose extension/role incompatibilities
not present in fixtures; application roles and globals require a separate rehearsal.

Online Storage backup has a consistency window between database export and object-file
snapshot. Recovery-point backups require a future ingress maintenance mode that rejects
and drains writes. Since public ingress is not implemented, that gate does not yet
exist and production updates remain blocked. One locally rotated USB drive does not
protect against theft, fire, common-mode corruption or lost encryption credentials;
independent escrow and an off-host restore test remain mandatory.

All Phase 1–4 blockers remain: Debian/T2 and encrypted-disk qualification, authenticated
package installation, Docker/firewall/Tailscale integration, private admin TLS/identity,
real app/RLS migration, public route/credential design, kiosk browser security floor,
and target reboot/network/thermal/power-loss tests. Verdict remains NO-GO for production
deployment or public exposure.
