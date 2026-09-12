# Final operational handoff

All seven repository phases are delivered. There is no next repository implementation
phase and no deployment was performed. Start any future task by reading
PHASE-7-STATUS, `docs/FINAL-VERIFICATION.md`, and `security/SECURITY-AUDIT.md`.
The root `README.md` now gives the concise end-to-end setup order and routes every
target-changing operation to the detailed operator runbooks.
All future work must remain within the MacServer repository boundary; no other product
repository is an instruction source or implementation dependency.

The next legitimate work is target qualification, which requires the user to identify
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
