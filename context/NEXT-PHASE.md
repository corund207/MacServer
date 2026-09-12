# Handoff after Phase 6 repository delivery

The non-destructive repository security audit, offline posture assertions and safe
private-admin policy correction are complete. Read PHASE-6-STATUS and
security/SECURITY-AUDIT.md. No target or endpoint was tested, and the NO-GO verdict
remains. Do not treat source checks as effective runtime controls.

## Phase 7 task boundary

Complete the repository-level end-to-end verification and production-readiness review.
Build one dependency-free release verifier that runs or composes the existing offline
checks, records exact scope and emits a machine-readable manifest without secrets.
Add a traceability matrix from every main-build requirement to implementation,
automated evidence, manual target evidence and status. Exercise only disposable local
fixtures: failure/interruption/restart cases must not touch a real service, Docker
daemon, tailnet, drive, database or network endpoint.

Produce `context/PHASE-7-STATUS.md`, update ARCHITECTURE and replace this handoff with
the final operational boundary. The final report must distinguish repository-complete,
target-unverified and blocked work, include safe installation/qualification ordering,
rollback and retest commands, and retain a NO-GO recommendation wherever required
evidence is unavailable. Do not fabricate public ingress, app credentials, application
schemas/RLS results, physical recovery, hardware compatibility or uptime evidence.

Review all diffs and staged files for secrets, rerun all Python/unit/parser/Compose,
Node, browser/accessibility and production-dependency checks, then make one focused
commit with configured identity and no co-author trailer. Push to the existing upstream.
Do not deploy, create approval markers, expose ports, apply SQL, modify a tailnet,
format media, restore data, or perform any destructive action.
