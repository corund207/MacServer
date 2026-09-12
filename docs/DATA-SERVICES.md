# Phase 2 data-service foundation

This is a reviewable, offline configuration, not a deployed appliance. Do not run
`up`, `run`, `exec`, `start`, upstream reset scripts or migrations on this development
host. `infra/versions.json` retains `deployment_enabled: false`; Compose itself does
not enforce that flag. Profiles are selection aids, not an authorization mechanism.

## Provenance and validation

Pinned [official distribution](https://github.com/supabase/supabase/tree/241bb11c0627f2981746d37033f57dbfa81d29b0/docker):
`self-hosted/v0.8.0`, commit `241bb11c0627f2981746d37033f57dbfa81d29b0`.
`infra/supabase/release.lock.json` records upstream file SHA-256 hashes, original
Compose YAML hash, registry index and linux/amd64 image digests, and exact Debian
13 Docker Engine/CLI/containerd/Compose/buildx package versions and archive hashes.
Package metadata was obtained over HTTPS; authenticated APT repository verification
and target compatibility are still required before installation. No packages were
installed and no container images were pulled or run to resolve those digests.

`upstream/compose.json` is the lossless JSON data-model conversion of the official
YAML (comments omitted); other vendored files retain the upstream license. Only three trailing
spaces in the reference Functions runner are normalized, recorded in the lock;
all other copied files are byte-for-byte upstream. Never deploy this upstream source directly. The deterministic
adaptation is `compose.json`; `scripts/supabase_config.py` defines every change.
Use these commands from the repository root:

```sh
python3 scripts/supabase_config.py
python3 scripts/validate.py
python3 -m unittest discover -s tests -v
```

Expected: PASS lines and all tests successful. Tests create only temporary synthetic
secrets and invoke Compose `config --quiet`, which requires no Docker daemon. A
missing Compose plugin is reported as a skipped test, not a configuration pass.
Do not display expanded Compose output: it contains secrets.

## Topology and resource budget

Every service is profile-gated on one internal Docker network; no published ports,
Docker socket, host networking or privileged container. Database volumes are named
volumes on the Docker data root, which must be placed on the encrypted internal SSD.
Storage objects use the explicit `/srv/macserver/storage` bind mount so they can be
included in the encrypted backup allowlist; provision it with the image's required
ownership before startup. No change to Docker's data root is automated. All
configuration mounts are read-only; Storage's imgproxy mount is also read-only. Upstream service users/capabilities are
retained except for `no-new-privileges`; blanket capability drops need image-specific
startup tests, especially for PostgreSQL initialization.

| Selection | Services | Initial memory caps |
| --- | --- | --- |
| core | PostgreSQL 17, Auth, PostgREST, Envoy | 2 GiB + 3 × 256 MiB |
| functions (with core) | Edge Runtime | 512 MiB |
| storage (with core) | Storage API, imgproxy | 512 + 256 MiB |
| realtime (with core) | Realtime | 512 MiB |
| management (with core) | Studio, postgres-meta | 768 + 256 MiB |

Core plus functions caps total 3.25 GiB; all selections total 5.5 GiB before kernel,
Docker, admin app, display and filesystem caches. These are proposed caps, not
measured usage. Keep at least 2 GiB headroom on an 8 GiB MacBook; measure thermal
throttling, RSS, connection pressure and OOM behavior before enabling optional
services. A 4 GiB machine is not qualified for this complete workload. Analytics,
Vector and Supavisor are omitted. Realtime is off until an app requires it; WAL
retention and replication-slot growth then need monitoring. Initial health checks
come from upstream; meta has no supplied health check and requires a future probe.

Envoy has upstream management routes, so it must NEVER be attached wholesale to a
public tunnel. A future separately tested HTTPS route allowlist and scoped app
authorization layer must precede any public use. Internal Docker networking blocks
SMTP, OAuth and external function requests; enabling narrow outbound access requires
an explicit target network design and tests. Do not change `internal: true` casually.
Private administration will need a reviewed Tailscale identity/authentication entry
point; there is currently no host listener, including on loopback.

## Secrets and API boundaries

Create an environment outside Git in a new directory with an existing trusted
parent. This creates files only, never deploys or rotates secrets:

```sh
python3 scripts/supabase_secrets.py --directory /secure/operator/chosen-new-directory \
  --api-url https://api.example.test --site-url https://app.example.test
```

Replace illustrative paths/URLs with approved values. The output is a mode-0600
`.env` in a mode-0700 directory; existing files and symlink paths are refused. The
script prints no values. Use an encrypted filesystem and escrow the environment
before stateful deployment. Do not source unreviewed env files in a shell. Docker
administrators can read container environments and are inside the trust boundary.

This foundation deliberately uses the release's supported legacy HS256 path.
`anon` is a public application bootstrap key, not user identity, app verification or
an authorization boundary. The service-role key bypasses RLS and stays in trusted
infrastructure; no browser, admin frontend or function worker receives it. Legacy
API keys expire after one year; user access tokens after 900 seconds. Plan rotation
and reauthentication before expiry. Rotation is disruptive and is not automated.
Opaque publishable/secret keys and asymmetric JWKS are left empty; do not partially
enable them. A future migration must coordinate Auth, gateway, REST, Storage and
Realtime verification as described in the official auth-key guide.

The function dispatcher replaces upstream's all-environment forwarding runner. It
uses Auth `/user` with the caller JWT, rejects anonymous users, bounds its auth
request, returns generic errors, and passes only the internal API URL and anon key
to workers. It has no external module dependencies and an empty allowlist. Deploying
a function requires reviewed code, a pinned dependency bundle, an allowlist change,
per-operation authorization and caller-JWT forwarding to preserve RLS. It supports
user-token calls only: scheduled service-role calls and unauthenticated webhooks
need a separate design. This is not a sandbox for untrusted customer code.
CORS/preflight behavior must be designed alongside the application ingress.

## Compatibility and deployment gates

One deployment is one Supabase project. Logical schemas in one database may separate
trusted applications' objects, but share Auth, keys, service roles, resources and
failure domain. They are not strong isolation. Start with exposed schema `api`
only; explicit grants and RLS are mandatory. Unrelated trust domains need separate
stacks and budgets. Additional databases do not create Supabase platform projects.
There is no managed branching, managed PITR, managed backup service or hosted
platform management API. Studio is not the managed platform control plane.

Before deployment: qualify Debian/T2 hardware; authenticated package installation;
Docker/firewall/Tailscale forwarding tests; initialize and audit the API schema;
complete app/RLS migration rehearsals; establish encrypted rotating backups of DB,
objects, db-config/pgsodium keys, configuration and secret escrow; restore into an
isolated target; approve ingress and required egress; validate pinned image startup,
health, memory, reboot and rollback. Phase 5 recovery remains mandatory for data.
Never point PG17 at PG15 data files or use volume deletion to recover a failed boot.
Rollback requires a compatible pre-change snapshot/backup restored into a separate
private target; image downgrade alone cannot roll back service schema migrations.

## Sources and version-specific decisions

Reviewed 2026-09-07: [Docker setup](https://supabase.com/docs/guides/self-hosting/docker),
[Envoy change](https://supabase.com/changelog/48048-self-hosted-supabase-envoy-becomes-the-default-api-gateway-b),
[Auth URL change](https://supabase.com/changelog/47093-self-hosted-supabase-api-external-url-to-include-auth-v1),
[auth keys](https://supabase.com/docs/guides/self-hosting/self-hosted-auth-keys),
[functions](https://supabase.com/docs/guides/self-hosting/self-hosted-functions).
The changelog also flags opt-in Analytics, Studio role changes and the PG15→17
transition. This release's checked-in Compose is authoritative for its PG17 pin;
some migration-guide prose still describes an older PG15 default.
