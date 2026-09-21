# Phase 12 — private workspace and live observations

The private console now shares MacServer's local Outfit fonts, lime/forest palette,
original mark, and compact workspace styling. Dark/light themes and mobile remain
supported. Connection setup validates identifiers/HTTPS origins and prepares a
project-generator command in the browser. It never executes it, saves form values,
or sends project details to the server; logout clears the form and command.

Optional collector integration reads only /run/macserver/status.json. It rejects
non-root-owned/writable, non-regular, symlinked, oversized, malformed, stale or
future observations. Only known service names, bounded details and backup state
are returned. It never receives the Docker socket or database credentials.
Collector invocation now supplies the private Compose env-file and accepts Docker
JSON arrays, individual objects or JSON lines. This fixes persistent unavailable
status caused by missing interpolation values/output shape.

Validation: TypeScript build, collector normalization tests, three workspace
browser tests covering connection generation/rejection, observations, logout,
dark/light accessibility and desktop/mobile overflow. Screenshots reviewed.
Existing Linux security/TLS/browser suites remain in CI alongside the new suite.
Dependencies were inspected and audited before installing with --ignore-scripts;
fonts were copied from the already-reviewed locked package with its OFL license.

No live appliance, status collector, credential or database was configured.
Target installation still follows ADMIN.md and PUBLIC-APPS.md.
