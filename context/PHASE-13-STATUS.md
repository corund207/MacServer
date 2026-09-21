# Phase 13 — usable initial accounts and database verification

Added private Auth user provisioning with a hidden password prompt. It sends only
the service credential and new user inputs through stdin into an ephemeral,
read-only, capability-dropped, memory-bounded Node container on the internal data
network. The runtime is the previously reviewed image; --pull=never forbids new
downloads. The host environment must be root-owned mode 0600. No public Auth admin
route was added, no existing account is updated, and no credentials are printed.

The public guide includes exact first-schema import and user-creation commands.
The source tests verify validation, credential placement and redacted failure.
A new isolated PostgreSQL CI job executes the real generated schema and verifies
positive own-row access, cross-user read/update/delete/insertion/reassignment
denial, and anonymous denial. It is fixture evidence, not production app validation.

No user was created and no SQL was applied on an appliance. CI runtime is isolated
with --network none and uses only synthetic rows; the official image is pinned.
