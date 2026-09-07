# Phase 3 handoff

Phase 2 delivered an offline foundation only. Read PHASE-2-STATUS.md,
docs/DATA-SERVICES.md and docs/MIGRATION.md; preserve every runtime/deployment gate.
No SQL, services or production access was exercised. Phase 3 must not assume a
running Supabase appliance or invent live health data.

Precise next-task prompt:

> Implement Phase 3, the Tailscale-only administration app described in
> PROMPT_TAILSCALE_ADMIN_APP.md. Read all applicable AGENTS.md files, all prompts,
> README, context handoffs and data-service/migration runbooks. Inspect git status,
> history and service definitions before editing; preserve existing work. Respect
> the user's local delivery authorization: commit each validated unit with the
> configured identity and no co-author trailers; push only when an upstream exists,
> otherwise report skipped without inventing a remote. Build a private control
> plane requiring verified Tailscale identity plus independent app authorization.
> Never trust source IP or browser-supplied identity headers alone. Keep service-role,
> database and Docker credentials out of the browser; use narrow operation helpers,
> no browser terminal. Start with offline/testable interfaces and honest unavailable
> telemetry. Supabase currently has no host listeners, api-only REST exposure,
> optional service profiles and an empty function allowlist. Preserve these limits.
> Do not start appliance services, apply migrations, connect to production or expose
> routes without authorization and the documented backup/network gates. Add tests
> for authorization, CSRF, input boundaries and secret redaction; follow the phase
> prompt for build and accessibility checks. Update PHASE-3-STATUS.md, ARCHITECTURE.md
> and NEXT-PHASE.md. Carry forward Phase 2's unexecuted SQL/RLS/runtime tests and
> Phase 5 recovery prerequisites. Do not begin Phase 4. Report commits, validation,
> skipped pushes and residual risks precisely.
