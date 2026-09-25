# Architecture

MacServer is a self-contained Debian 13 appliance for an Intel MacBook Air.
Source tests and hosted documentation are delivered; the physical appliance is
not commissioned or production-approved.

## Data plane

The digest-pinned Supabase-compatible stack runs PostgreSQL, Auth, PostgREST and
Envoy on `macserver-data_default`, an internal Docker network with no published
ports. Storage, Realtime, Functions and Studio/meta are optional profiles.
Live database and Storage bytes belong on the encrypted internal SSD.

REST exposes only the `api` schema. The application owner is a NOLOGIN role with
restricted defaults; tables require explicit grants and ownership-based RLS.
The example project generator creates a new private bundle containing a schema,
scoped gateway config and Supabase client. It never installs or imports anything.
Related apps share this Auth/key/failure boundary; unrelated trust domains need
separate deployments.

## Public application boundary

```text
Public HTTPS -> Cloudflare Tunnel -> scoped gateway -> Auth / PostgREST -> RLS
                 edge network       edge + internal      internal only

Administrator -> direct Tailscale HTTPS -> private console
              -> key-only SSH -> operator tools
```

The separate `macserver-ingress` Compose project publishes no host ports.
Cloudflared joins only the edge network. The dependency-free Node gateway joins
edge plus data and uses fixed Auth/PostgREST destinations. It has no signing,
database or service-role credential and no route to Envoy, Studio or administration.

A publishable app key selects explicit table/method scopes; upserts that merge rows
need the table's PATCH scope. It is not proof of app identity. Data requests also need an Auth-verified user token; RLS authorizes
rows. Origin, Referer, User-Agent and IP never replace authentication.
The gateway limits bodies, responses, time, concurrent requests and request budgets,
and records redacted events. RPC, joins, anonymous data, signup, OAuth, Storage,
Realtime and Functions are not exposed. Access-token revocation follows expiry.

Initial users can be created by a root-run private CLI calling Auth through an
ephemeral, pinned, read-only Node container on the internal network. Credentials
enter through stdin, not arguments or environment, and are never printed.

## Administration and observations

The console binds only to the configured Tailscale address with HTTPS. Every
request verifies the actual socket peer through the reviewed Tailscale daemon
contract, then requires an independent per-user credential/session. It denies
forwarded identity and public proxies. There is no browser terminal.

The console provides host metrics, curated read-only files, audit events and
a local connection-command assistant. Privileged browser operations are disabled.
An optional fixed-file reader consumes root-owned, bounded collector snapshots;
stale or missing evidence is unavailable. The reader has no Docker socket.
The separate root collector performs fixed Docker/Tailscale queries and writes
a curated snapshot. The local dashboard remains loopback-only.

Local Outfit fonts, an original mark, forest/lime colors, and accessible dark/light
themes form the brand. Public documentation is a separate static GitHub Pages
artifact with no appliance connection, credentials or live status.

## Recovery and deployment

Restic backup tooling verifies removable media identity and mount constraints,
encrypts database/object/config snapshots, and supports isolated restore drills.
Use rotating drives, independent password escrow and an off-host copy. Stop public
and private/background writers when a consistent cross-resource snapshot is needed.

Commissioning requires a clean, synchronized canonical GitHub commit, pinned
packages, protected configuration and fresh target qualification. Root-owned
versioned source releases are activated through an approval-gated systemd unit.
Updates require current backup/restore evidence and explicit compatibility review.
Reverting a release does not revert a database migration. Optional admin build
artifacts must follow their separate deployment guide.

## Evidence and outstanding qualification

CI covers structural posture, generated Compose, Linux units, gateway denial cases,
schema execution and two-user RLS in an isolated PostgreSQL fixture, private admin
security/TLS/browser flows, collector behavior, dashboard behavior, and site
responsive/accessibility checks. Images, npm dependencies and actions are pinned;
download review records provenance and audit limits.

None of this substitutes for exact-Mac T2 support, Docker/firewall behavior, real
restore, real app migration/RLS, private identity/TLS, power recovery, or external
route acceptance. Production remains false. Full-disk encryption requires local
unlock after cold boot. Public management and router port forwarding are forbidden.

Historical implementation evidence is retained in `PHASE-1-STATUS.md` through
`PHASE-13-STATUS.md`. The current operator handoff is `NEXT-PHASE.md`.
