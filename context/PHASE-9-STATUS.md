# Phase 9 — installation corrections

Source-only changes; no appliance or production endpoint contacted.

- Correct the deployment origin to the current authenticated owner's repository.
- Invoke the installed appliance CLI through Python; executable Git bits are not assumed.
- Require the full verifier in the deployment guide and document its dependencies.
- Enforce LF checkout bytes for reviewed upstream hashes.
- Resolve Git through PATH for read-only source verification on Windows and Linux.

Validation: structural validator, deterministic Compose renderer, offline security
posture, schema audit and security regression tests. Linux runtime qualification
remains required; GitHub CI provides source-only Linux verification.
