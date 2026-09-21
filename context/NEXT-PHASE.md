# Target commissioning handoff

Phase 13 supplies private initial user creation and isolated SQL CI. Real application
schemas, user migration, domain configuration and target commissioning remain the
operator-specific steps. The example project schema is not VEXVortex's real schema.

Phase 12 completes the branded connection assistant and optional collector reader.
Remaining work is real target qualification and application-specific configuration,
not an assertion that source tests prove production reliability. Use the published
installation guide, then PUBLIC-APPS.md with your actual domain and Cloudflare token.

Phase 11 delivers source-tested Cloudflare/public gateway setup and project
scaffolding. Next complete the private console's project connection experience,
then commission the actual target using docs/PUBLIC-APPS.md and the hardware gates.
No production app schema, domain, tunnel credential, or target evidence exists here.

Phase 10 adds a branded GitHub Pages installation site and short README. The public
site's Linux browser checks also require explicit code-sample keyboard focus. The
HTTPS app workflow requested by the user is next; no appliance is authorized or
reachable in this workspace. Preserve the source/target evidence distinction.

Eight repository phases are delivered and no target deployment was performed. Start
target work with `context/PHASE-8-STATUS.md`, `docs/DEPLOYMENT.md`,
`docs/FINAL-VERIFICATION.md`, and `security/SECURITY-AUDIT.md`.
The root `README.md` now gives the concise end-to-end setup order and routes every
target-changing operation to the detailed operator runbooks.
All future work must remain within the MacServer repository boundary; no other product
repository is an instruction source or implementation dependency.

The next legitimate work is target commissioning, which requires the user to identify
and authorize the exact MacBook appliance and any test endpoints. Gather the missing
choices first: recovery objectives and media/off-host destination; application/domain/
route inventory; app credential scopes; managed Supabase schema and RLS cases; tailnet
identities/policy; and acceptable maintenance windows. Keep production data and public
exposure disabled until every blocking finding has evidence.

Follow the ordered checklist in FINAL-VERIFICATION. Use only the designated appliance
and explicitly listed endpoints; never scan the broader LAN. Preserve local console and
rollback access. Any OS/disk formatting, volume deletion, production restore, migration,
credential rotation or policy replacement needs immediate explicit confirmation and a
tested backup/rollback path. Public management remains forbidden.

For source-only revalidation, use a clean checkout and run:

```sh
python3 scripts/release_verify.py --full --require-clean --require-upstream-sync \
  --output build/release-verification.json
```

Passing output remains repository-only NO-GO evidence until the manual target matrix is
completed and independently reviewed. Future delivery units must retain focused commits,
configured Git identity, no co-author trailers, secret review and upstream push.

## Phase 9 installation corrections

Canonical GitHub origin and Python CLI invocation now match the actual checkout.
Full source verification is documented; runtime and public exposure remain gated.
The requested next delivery adds a branded installation site and public-app setup.
