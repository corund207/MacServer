# Handoff after Phase 4 repository delivery

The loopback-only local dashboard, bounded status collector, inert collector/display/
kiosk units, browser/accessibility checks, and operator recovery guide are implemented.
Read PHASE-4-STATUS for evidence and target blockers. No service was deployed. The
observed Debian Chromium package is below the recorded security floor; target kiosk
approval remains blocked until an adequate security update and revalidation.

The broader admin prompt still has disabled SQL, write, backup, restore, update, and
restart operations. Do not relabel them as delivered. The dashboard is intentionally
read-only and must never inherit admin credentials or helper access.

## Phase 5 task boundary

Implement repository-only encrypted backup, retention, integrity, restore-drill, and
safe update/recovery automation. Read every instruction and prior status first.
Require exact removable-drive filesystem UUID plus a sentinel, minimum capacity,
mountpoint and filesystem checks before writing. Use restic with an independently
escrowed password, explicit source allowlists, PostgreSQL logical dumps, Storage and
configuration coverage, prune/check policies, atomic status evidence, and an off-host
copy recommendation. An absent drive must fail visibly without affecting live services.

Restore only into a new isolated destination/database by default. Never overwrite live
state, remove volumes, rotate credentials, apply migrations, or execute a production
restore without immediate explicit user confirmation and a tested rollback. Tests must
use disposable fixtures and fake fixed-command runners; no real database, drive, Docker
daemon, tailnet, or secrets. Add systemd candidates behind untracked approval markers,
operator commands with expected results and rollback, and update PHASE-5-STATUS,
ARCHITECTURE, and NEXT-PHASE. Review/test/commit the delivery unit with configured Git
identity and no co-author trailers; push only if an upstream exists.

Preserve all unresolved gates: Debian/T2 hardware, Docker/firewall/Tailscale runtime,
private admin TLS/identity, production SQL/RLS and migration, public domain/tunnel and
credential design, browser security floor, target reboot/network/power-loss testing,
and off-device disaster recovery. Public administration remains forbidden.
