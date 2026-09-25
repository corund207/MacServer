# Connect a public app

MacServer includes a small, scoped HTTPS application gateway. Cloudflare Tunnel
provides the outbound connection; the gateway exposes only approved table methods
and password/refresh Auth routes. It never forwards to Studio or the broad Envoy
gateway. No host ports are published. The tunnel cannot join the database network.

This is source-tested setup tooling, not a deployed endpoint. You still need a
qualified Mac, working backups, a Cloudflare-managed domain, and real RLS tests.

## What works

| Capability | Public gateway |
| --- | --- |
| Sign in with password; refresh session; sign out; get user | Supported |
| Select, insert, update, delete on approved `api` tables | Supported with user session and RLS |
| Upsert (`on_conflict`, `resolution=merge-duplicates`) | Only where the table also allows `PATCH` |
| Multiple related apps | Separate scoped keys and table allowlists; shared Auth |
| Anonymous database access, signup, password reset, OAuth | Denied by default |
| Storage, Realtime, Functions, RPC, joins/embedded resources | Not exposed in this release |
| Studio, SQL console, database ports, metrics, administration | Never public |

The publishable app key selects a route scope. It is intentionally safe for client
code and **does not prove app identity**. Anybody can copy a browser credential;
user sessions and RLS authorize rows. If an operation must be exclusive to your
server, keep it in your backend with separate server-side authorization.

Tokens are validated at Auth before data access. An existing access token can stay
valid until expiry after logout/revocation; use the configured short token lifetime.
Do not claim instant revocation without additional session checks in your data policies.
Unrelated trust domains need separate stacks/appliances, not another key in this config.

## Generate a project

On the Debian operator account, create a new bundle outside Git. Substitute your
real HTTPS API hostname and app origin. The command does not install or import:

```sh
install -d -m 0700 "$HOME/.macserver-private"
python3 scripts/project.py --name vexvortex --table vexvortex_items \
  --api-url https://api.example.test --origin https://app.example.test \
  --output "$HOME/.macserver-private/vexvortex"
```

Expected: `Project bundle created`. The new directory contains:

- `schema.sql`: an owner-scoped example table, grants, and RLS policies.
- `ingress.json`: one app with a fresh scoped key and explicit table/method allowlist.
- `client.ts`: a ready-to-use Supabase client using the `api` schema.
- `README.md`: the remaining steps. Reruns refuse to overwrite the bundle.

Review SQL before applying. Use an empty rehearsal target, a tested rollback path,
and immediate confirmation before a migration as required by the migration runbook.
Apply `supabase/migrations/20260907091838_establish_api_boundary.sql` once before
the generated schema. Run psql with `ON_ERROR_STOP=1`; never continue after an error.
The example is a starting schema, not VEXVortex's real data model.

For a brand-new rehearsal stack, the exact schema import commands are:

```sh
sudo docker compose --env-file /etc/macserver/supabase.env \
  -f /opt/macserver/infra/supabase/compose.json exec -T db \
  psql -U postgres -d postgres -v ON_ERROR_STOP=1 \
  < supabase/migrations/20260907091838_establish_api_boundary.sql
sudo docker compose --env-file /etc/macserver/supabase.env \
  -f /opt/macserver/infra/supabase/compose.json exec -T db \
  psql -U postgres -d postgres -v ON_ERROR_STOP=1 \
  < "$HOME/.macserver-private/vexvortex/schema.sql"
```

Run the boundary migration only once for this stack. Stop on any error. These
commands create database objects and must follow your migration review/confirmation.
Do not run them against an existing populated deployment as a shortcut.

After pulling the reviewed ingress images below, create two test users privately:

```sh
sudo python3 /opt/macserver/scripts/auth_user.py
```

Run once per user. It prompts for email and a hidden, confirmed password, then
creates an email-confirmed Auth user without sending mail. A temporary read-only
container joins only the internal data network. The service credential and password
travel over stdin, never command arguments or environment variables, and are not
printed. It refuses to pull an uninstalled image. An existing user is not updated.
Share initial credentials privately. This is a small-deployment provisioning flow,
not a public signup/password-recovery system.
Existing managed users can instead be migrated following [Migration](MIGRATION.md).

## Configure Cloudflare Tunnel

1. In Cloudflare Zero Trust, create a remotely managed Cloudflare Tunnel for this
   appliance. Do not execute the suggested shell installer.
2. Add exactly your approved API hostname, with service `http://gateway:8080`.
   Do not add wildcard hostnames, private network routes, SSH, Studio or Envoy.
3. Enforce HTTPS at Cloudflare, configure appropriate edge abuse controls, and
   review request logging for privacy. The local gateway also enforces request,
   body, response, concurrency and authentication budgets. Auth routes allow 20
   requests per minute per client (IPv6 grouped by /64) and 300 in total. The client
   comes from Cloudflare's `CF-Connecting-IP` header. It is used only for throttling,
   never logged or used to authorize. A flood from many addresses can still reach
   the total limit, so add a Cloudflare rate-limiting rule for `/auth/v1/token`.
4. Transfer the tunnel token privately into a new file outside Git. Never put it
   on the command line or paste it into logs. The container reads a token file.
5. Install private configuration on the target:

```sh
sudo install -o root -g 65532 -m 0640 \
  "$HOME/.macserver-private/vexvortex/ingress.json" /etc/macserver/ingress.json
sudo install -o root -g 65532 -m 0640 \
  /PRIVATE/PATH/tunnel-token /etc/macserver/tunnel-token
```

These commands are for the first install. Do not overwrite existing config or
rotate a live tunnel token without reviewing and preserving rollback copies.
The parent `/etc/macserver` remains root-owned mode 0700; Docker mounts only the
individual files. Container UID/GID 65532 can read its mounted file, not host secrets.

The pinned Linux amd64 images were resolved from the official Docker Hub repositories:
Node `24.20.0-alpine` and Cloudflare `2026.9.1`. Compose records exact manifest digests.
No npm installation occurs inside the gateway; it uses Node's built-in modules.
Check vendor advisories before target installation; identity pins are not a security
certification. Review the official [Tunnel documentation](https://developers.cloudflare.com/cloudflare-one/networks/connectors/cloudflare-tunnel/).

## Validate and start

The data stack must already be commissioned and healthy. The reviewed release
under `/opt/macserver` must contain the ingress source and Compose file.

```sh
sudo docker compose -f /opt/macserver/infra/ingress/compose.json config --quiet
sudo docker compose -f /opt/macserver/infra/ingress/compose.json pull
sudo docker compose -f /opt/macserver/infra/ingress/compose.json \
  run --rm --no-deps gateway node /app/src/check.mjs
```

Expected: `PASS: scoped ingress configuration`. Nothing above starts the tunnel.
Before activation, pass the host/firewall, backup restore, and application/RLS gates.
Record your exact approved hostname/routes and evidence outside Git. Then create
the approval marker and enable the service:

```sh
sudo install -o root -g root -m 0600 /PRIVATE/PATH/public-approval \
  /etc/macserver/PUBLIC-APPROVED
sudo install -m 0644 /opt/macserver/infra/ingress/macserver-ingress.service \
  /etc/systemd/system/macserver-ingress.service
sudo systemctl daemon-reload
sudo systemctl enable --now macserver-ingress
```

The marker is an operator guard, not evidence verification. Check Cloudflare reports
a healthy connector; Compose health only proves the local gateway process answers.
Use only this hostname for low-rate acceptance checks:

- Valid app key + user A: create and read A's row.
- User B: cannot read, change, delete, or claim A's row.
- Missing/forged/expired/service-role token: denied before database access.
- Wrong app key, unapproved table, schema override or method: denied.
- `/`, `/studio`, `/metrics`, `/auth/v1/admin/users`, and `/rest/v1/rpc/...`: denied.
- Verify LAN/public database, SSH, Studio, admin and metrics remain unreachable.

Never use a service-role token to test client authorization. A successful empty
result can be an RLS denial; verify both positive and negative row assertions.

## Use it in your app

Copy `client.ts` into your app and use its Supabase client. Sign in before database
queries. The gateway accepts simple column selection, filtering, ordering, counts,
pagination and JSON writes; it rejects joins, RPC and arbitrary schemas.
The publishable key is not a Supabase JWT. Only this gateway understands it; do not
substitute a service-role key or connect the app directly to internal services.

For another related app, generate another new bundle, review its schema and merge
its entry into the existing private `apps` array. Each ID and key must be unique.
Configure all approved frontend origins exactly. CORS is not authentication.

## Stop, update, and recover

```sh
sudo systemctl stop macserver-ingress
```

This closes public application access and preserves data. To update: stop ingress
to drain public writers, perform the guarded appliance update, validate the ingress
config, then restart it and repeat route checks. Also stop any private/background
writers before a consistent database/Storage backup or migration.
If validation fails, leave ingress stopped, restore the prior reviewed config and
release using [Update and rollback](UPDATE-ROLLBACK.md), then retest. Never delete
volumes. Gateway logs contain event IDs, app IDs, route classes and outcomes only;
request paths, queries, bodies, credentials, and Auth response tokens are omitted.
