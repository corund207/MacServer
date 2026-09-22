# Commissioning handoff

The requested source work is delivered in phases 9–13: installation corrections,
MacServer branding, a short README, GitHub Pages documentation, a scoped public
gateway, project bundles, private initial-user provisioning, and a branded private
console with optional live collector observations.

Installation site: https://corund207.github.io/MacServer/

## What is ready

- `docs/GETTING-STARTED.md`: complete ordered installation instructions.
- `docs/PUBLIC-APPS.md`: Cloudflare Tunnel, project schema/client generation,
  private user provisioning, exact supported routes, acceptance checks and shutdown.
- `docs/ADMIN.md`: private console and optional collector integration.
- `docs/DOWNLOAD-REVIEW.md`: reviewed sources, pins and download boundaries.
- CI: structural/security checks, Linux units/Compose, Python tests, public gateway
  negatives, real isolated PostgreSQL RLS tests, private console/TLS/browser tests,
  dashboard tests, and responsive/accessibility checks for the installation site.

All delivery commits use the verified corund207 GitHub identity and authenticated
Git transport. No co-author trailers or rewritten history.

## What must happen on the appliance

1. Identify and authorize the exact MacBook and endpoints. Qualify Debian 13/T2,
   encryption, cooling, power-loss recovery, console and private networking.
2. Commission the private stack using `docs/DEPLOYMENT.md`.
3. Establish rotating encrypted backups and complete an isolated real restore.
4. Review the real VEXVortex schema and ownership rules. The generated example is
   not a substitute for production migration or real application authorization tests.
5. Supply the actual domain and Cloudflare tunnel credential privately. Enable only
   approved HTTPS app routes after two-user negative tests and exposure checks.
6. Install the optional private console using its reviewed build artifact and
   dedicated user. Verify direct Tailscale identity, TLS, session and collector access.

No appliance was contacted, disk formatted, real account created, or production
data imported. Public management remains forbidden. Public gateway scope currently
covers approved table CRUD and password/refresh Auth; Storage, Realtime, Functions,
signup, OAuth, password reset, joins and RPC remain denied. Related apps share
Auth; unrelated trust domains need separate stacks.

Full-disk encryption currently needs local unlock after a cold boot.
Destructive operations retain immediate explicit confirmation and tested rollback.

## Revalidate source

On Linux with the reviewed development dependencies:

```sh
python3 scripts/release_verify.py --full --require-clean --require-upstream-sync
cd site
npm ci --ignore-scripts
npm run build
npm test
```

The release verifier intentionally retains a repository-only NO-GO verdict.
Only completed target evidence can establish production readiness.
