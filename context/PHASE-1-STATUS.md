# Phase 1 status

2026-09-06 — offline foundation completed; target installation and production
readiness NOT claimed. Delivery is blocked only at push by absent Git remote.

The resumed task read all MacServer prompts, README, context and nested agent
instructions. The VEXVortex main prompt matches the local copy. No Git metadata
existed; there were no branches, upstream, tracked diff or history to preserve.
Initialized main using the existing configured Git identity. Preserved the initial
scaffold and completed it as one focused foundation delivery unit.

Implemented: architecture ADR; monorepo layout; ignore/env templates; disabled
version manifest; dependency-free preflight and staging; secure exclusive backup
password creation; inert SSH, nftables input, journald, security-update, time-sync
and lid-policy templates; tailnet policy example; pinned-checkout read-only CI;
installation, version/delivery and rollback guides; threat model and phase handoff.
Staging additionally rejects source symlinks/special files and changed output
permissions. Preflight now checks the selected state filesystem rather than checkout
space. These tools have no apply mode and request no elevation.

Validation: `python3 scripts/validate.py` passed; all seven unittest safety cases
passed; CLI staging and identical re-staging passed in an ephemeral directory;
preflight correctly returned 1 on this unsupported development host. A local
OpenSSH 10.5 `sshd -T` check with an ephemeral host key confirmed nine candidate
restrictions (root/password/interactive login, public-key authentication, forwarding,
X11 and tunnels); no daemon started. This does not validate Debian's effective
installed policy. `nft --check --file infra/host/nftables.nft` was blocked by kernel
permissions (`Operation not permitted`); no firewall validation pass is claimed.
Git staged whitespace checks passed. Runtime/target-only checks
remain explicitly unverified: Debian/T2 hardware, effective installed SSH policy,
firewall/Docker/Tailscale integration, tailnet policy editor tests, unattended
updates, time sync, SSD/cooling, reboot/network recovery, service health and restore.
There is no Compose stack or service deployment yet; pins intentionally remain null.

No host changes, public listeners, package installation, data migration, volume
removal or destructive operations occurred. The generated-password tests use only
ephemeral synthetic data; no real secrets are tracked.

Blockers and residual work:
- Operator must provide a real GitHub repository URL and upstream branch. For an
  empty remote: `git remote add origin git@github.com:OWNER/REPOSITORY.git`, then
  `git push --set-upstream origin main` after replacing the placeholder.
- Qualify target hardware and independently tested recovery before any OS change.
- Phase 2 must resolve pins and Docker firewall integration before service setup.
- Public route/domain/credential choices remain unresolved; ingress stays disabled.
- Backups, restores and resilience implementation belong to Phase 5 and are
  prerequisites for production data. Secret generation is not backup coverage.

Next task: exact Phase 2 prompt in NEXT-PHASE.md, after Git push is resolved and
usage permits. No Phase 2 work started here.
