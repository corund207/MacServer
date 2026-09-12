# Phase 8 — fail-closed appliance deployment and updates

2026-09-12. MacServer now has repository-complete commissioning and GitHub update
tooling. No appliance, Docker daemon, tailnet, backup device, database, systemd unit,
secret, or public endpoint was contacted or changed during implementation.

## Delivered scope

- A root-run Python appliance tool accepts only the configured MacServer GitHub origin,
  a clean upstream-synchronized full commit, safe checkout permissions, exact reviewed
  Debian/Docker/Tailscale boundaries, private root-owned configuration, and fresh
  commit-specific target qualification.
- First commissioning refuses existing releases and Compose containers, copies tracked
  files into an immutable versioned release, pulls digest-pinned images, atomically
  switches `/opt/macserver`, and starts an approval-gated systemd unit.
- The data unit uses only selected Compose profiles, the internal-only network, no
  published ports, boot recovery, and Compose health waiting. Failure preserves state.
- GitHub updates are operator-triggered after a fast-forward pull and full verification.
  They additionally require a healthy backup under 24 hours and isolated restore drill
  under 30 days plus a one-hour exact-release approval confirming writers are drained
  and data compatibility was reviewed. Activation failure returns to the previous
  release pointer.
- Manual rollback only selects an already-installed release, requires current commit
  confirmation and backup evidence, and restores the starting pointer on restart failure.
  Neither update nor rollback deletes volumes or runs SQL migrations.

## Evidence boundary

Unit tests cover qualification refusal, backup freshness, short-lived update approval,
reviewed Compose profiles, and immutable release staging;
offline posture checks cover deployment refusal strings and service gates. The unit is
parsed with disposable dependencies. These are source tests, not target qualification.
The structural validator, deterministic Compose renderer, security posture checker,
36 Python tests, and all four disposable systemd parser groups pass. CLI help and
tracked secret-pattern review also pass. The complete 12-group verifier, including
both Node and both local browser/accessibility suites, passes while retaining
`targetEvidence: false`, `publicExposureApproved: false`, and verdict `NO-GO`.

`deployment_enabled: true` means the guarded deployment mechanism exists.
`production_approved: false` remains authoritative until the exact MacBook completes
hardware, firewall, recovery, real application/RLS, migration, and exposure testing.
