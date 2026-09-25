# Private administration operator guide

The private console provides host observations, optional fresh service/backup status,
curated files, audit history, and a project connection assistant. The assistant
generates a reviewed setup command locally; it never executes a browser terminal
or provisions a database. Privileged controls remain unavailable in this console.
No target installation or production access is implied by source/browser tests.
See [current UI status](../context/PHASE-12-STATUS.md).
Read [identity contract](ADMIN-IDENTITY.md) and [threat model](../security/ADMIN-THREAT-MODEL.md).

## Reproduce offline verification

From the repository root:

```sh
python3 scripts/validate.py
python3 scripts/supabase_config.py
python3 scripts/validate_admin_unit.py
python3 -m unittest discover -s tests -v
cd apps/admin
npm ci --ignore-scripts
npm test
npm run test:browser
npm audit --omit=dev
```

Expected: structural validators pass, systemd parser passes with temporary stubs,
18 Python tests, 13 admin tests and 5 browser tests pass. Tests require Linux,
OpenSSL and Node; browser tests use installed `/usr/bin/chromium` or the browser
installed by the pinned Playwright CLI (`npx playwright install chromium`). An
explicit `ADMIN_TEST_CHROMIUM` path affects tests only. Browser identity, credentials
and certificates are synthetic. Listeners bind loopback, close after each fixture,
and never contact tailscaled or Supabase. Test output under apps/admin/test-results
is ignored; do not publish it as real appliance health evidence.

The CI job uses a commit-pinned setup-node action, exact Node 26.8.1, locked npm
install, both suites and dependency audit. It may install browser system packages
inside the disposable CI runner. No deployment secrets, credentials or service
commands are present. Hosted CI runs on the configured GitHub repository.

## Project connections and live observations

Open **Connect a project**, enter a lowercase project/table name and the exact
HTTPS API URL and app origin, then choose **Prepare connection**. Copy the command
to a key-only SSH session on the qualified appliance. It generates a new private
bundle; follow [Public apps](PUBLIC-APPS.md) to review and activate it. Form values
are not sent to the server or saved in browser storage, and sign-out clears them.

To enable service and backup observations after qualifying the existing collector:

```sh
sudo usermod -a -G macserver-dashboard macserver-admin
```

Add `ADMIN_COLLECTOR_ENABLED=true` to the private `/etc/macserver-admin/admin.env`,
then restart only the admin service. The `macserver-dashboard` group (shared with the console display) must already exist and
must grant read-only access to `/run/macserver/status.json`; never add Docker,
sudo or journal groups. The reader only accepts a bounded root-owned regular file,
rejects symlinks and evidence older than 60 seconds, and returns allowlisted fields.
Missing or stale evidence is unavailable. A backup observation does not certify
a successful restore. Remove that setting and restart to disable the integration.

The collector reads the root-only Compose environment without printing it and
handles array or newline-delimited Docker JSON. Its output never includes those
credentials. Review effective directory/group permissions on the actual target.

Brand fonts are local, license-included assets. No CDN or analytics is used by the
private console. `npm run test:ui` runs additional synthetic loopback browser checks.

## Private deployment candidate — not applied

`infra/admin/macserver-admin.service` is a reviewed inert candidate. It requires
an independently provisioned unprivileged `macserver-admin` user/group, root-owned
code at `/opt/macserver`, reviewed Node runtime at the versioned path in ExecStart,
and Tailscale 1.102.3. Node archive URL/checksum is recorded in
`infra/admin/runtime.lock.json`. Verify Node's signed release checksums and
authenticated Tailscale package provenance before installing; this task fetched
only the checksum/source metadata and installed no host runtime/package.

Build the app in a disposable build workspace using `npm ci --ignore-scripts` and
`npm run build`. A deployment artifact needs `build/src`, `build/public`, `public`
(HTML/CSS), `package.json` and production `node_modules` from the lockfile. Omit
test files, browser packages, operator credentials and development output. Keep a
private manifest and a previous tested artifact. Do not run npm/build as the web
service account or give it write access to source/dependencies.

Prepare a private root-owned `/etc/macserver-admin` directory outside the repository.
Its `admin.env` contains only `ADMIN_BIND`, `ADMIN_PORT`, `ADMIN_ORIGIN` and optional
`ADMIN_FILE_ROOT`, based on the checked-in env example. The candidate's credential
and audit variables are already set by systemd; do not override them. The origin
must be the actual appliance's HTTPS MagicDNS FQDN with port 8443, exactly matching
Host and browser Origin. Use the exact assigned Tailscale IP for ADMIN_BIND.
The example loopback value is deliberately unusable for remote admin login.

The dedicated user must have no sudo, Docker, journal-reading, Tailscale operator
or other privileged group membership. Systemd loads root-owned mode-0600
`credentials.json`, `tls.key`, `tls.crt` as private credentials. The service writes
only its mode-0700 state directory. It needs read access to the local daemon's
read-only status/whois CLI operations through its existing Unix socket; if denied,
stop and review the target's permissions. Never grant broad operator/root access
or relax socket permissions to make the UI work.

Obtain/renew a certificate for the actual node using the official
[Tailscale HTTPS workflow](https://tailscale.com/docs/how-to/set-up-https-certificates).
Certificate issuance exposes the hostname in public certificate transparency logs.
Use non-sensitive hostnames. Renewal is an operator maintenance responsibility;
the app reads the key/cert at startup and has no renewal automation. Check certificate
expiry before every maintenance window and schedule operator renewal. Replacing
credentials or certificates requires a controlled restart, which revokes all app
sessions. Retain independent key-only SSH and console access before restarting.

For app authorization, securely obtain the approved user's numeric ID from the
authorized appliance's whois result, without copying its output into Git/chat.
The exclusive provisioning command is:

```sh
npm run provision -- NUMERIC_USER_ID /trusted/private/new-directory
```

Replace both placeholders. The parent must be owned by the operator/root and not
group/world-writable. This creates 0700 directory and 0600 salted verifier/token
files and refuses overwrite. Read `initial-token.txt` privately into a password
manager; never put the token in a command argument, screenshot, ticket or frontend
configuration. Install only the verifier in the service configuration. Keep the
initial plaintext token outside the service's readable filesystem. Supporting more
admins requires an operator-reviewed private array of per-user verifiers (maximum
32); never share one token across users. Restart to apply revocation/rotation.

Allow only the administrator group to reach the appliance on TCP 8443 in a reviewed
tailnet policy. The repository example now aligns its host-input and tailnet rules on
8443, but additive broad grants can still defeat that boundary; inspect the entire
effective policy and test allow/deny cases in the policy editor. Do not apply a
replacement tailnet policy from this guide. Require direct Tailscale connections. Do
not use Serve, Funnel, subnet proxies, ingress tunnels, router forwarding or public
reverse proxies.

Keep `DEPLOYMENT-APPROVED` absent until the appliance is qualified and the following
checks pass in an isolated Debian 13 rehearsal, then on only the approved target:

- Exact Node/Tailscale versions, working certificate trust, synchronized time and
  read-only daemon permissions as the actual unprivileged service user.
- Effective systemd credential isolation, syscall restrictions, resource limits,
  `/proc/self/fd` traversal, root-filesystem readings and Tailscale-only sockets.
- BPF network restrictions and host/Docker/Tailscale firewall behavior, including
  refusal from an authorized LAN test client and a non-admin tailnet identity.
- Login/expiry/logout, denied tagged/shared/expired device and wrong app token,
  daemon offline state, audit failure, restart/certificate replacement and rollback.
- Existing Phase 2 runtime/RLS/network and Phase 5 encrypted-recovery prerequisites
  for any production data. No existing runtime gate is waived by this UI.

The marker is an operational guard, not proof of testing. Unit parsing does not
start or install the unit, create the user, enable BPF or validate sandbox behavior.
This guide deliberately provides no automatic enable/start/apply command.

## Read-only operation

After real authorization, the UI shows host observations with sample time and
staleness. CPU needs two observations; RAM includes reclaimable cache, network
totals include virtual interfaces, and disk refers to the root filesystem as seen
by the service. Missing sensor/service/backup values remain unavailable. Overview
refreshes every 15 seconds in visible tabs; sample history is bounded in process
memory and resets at restart. This is not durable monitoring or a recovery system.

For files, use a dedicated root such as `/srv/macserver-admin-exports`, owned by
root and readable (not writable) by the app group. Root-controlled directories
and separate regular text exports only. Do not mount live DB/Storage/secrets or
backup escrow there. Downloads are attachments, not content-redacted. File contents
can change during reading; export immutable completed reports for consistent reads.
Symlinks, hardlinks, hidden names, unsupported extensions and >1 MiB downloads are
denied. Listing bounds are documented in the UI. A compromised process can read
all exports allowed by its OS permissions; the app is not an untrusted-code sandbox.

Audit events contain hashed actor references, fixed event/outcome names and server
time. No raw secrets, SQL, filename or exception payload is logged. Latest 200
events since startup are visible in the UI; durable history is private JSONL.
At the 16 MiB cap, audited actions fail closed. Before capacity is exhausted,
schedule maintenance to stop only the admin service, copy the audit file to verified
private archival storage, compare SHA-256/size, retain the original, and arrange a
new private audit file before restart. Do not truncate evidence or rotate beneath
an open descriptor. Automated archive/retention and tamper-resistant remote audit
storage are not implemented. Logout invalidates the session even if audit fails.

## Rollback and future capabilities

No host changes were applied, so this repository delivery needs no host rollback.
Before future deployment retain a tested previous artifact, private config and
audit copy. During rollback stop only the admin unit, preserve audit evidence,
restore the previous compatible app/config/certificate set, validate ownership and
private binding, and restart in the approved maintenance window. A restart drops
sessions. If identity fails, retain SSH/console access and fix the reviewed contract;
never bypass auth or enable a public listener. Do not touch Supabase volumes or
restore database state to roll back this admin app.

SQL, metadata connections, service/raw-log collectors, file mutations and privileged
operations remain deferred. A future helper needs a separate service account and
explicit operation API, bounded/redacted outputs, server-side authorization and
audit. Disruptive actions additionally need recent re-authentication, explicit
confirmation/reason and verified backup/health/rollback evidence. A SQL executor
requires a restricted RLS-respecting role, read-only transaction, timeout, row/byte
limits and function restrictions; a SELECT prefix check alone is insufficient.
There is no browser terminal; use [key-only SSH](INSTALL.md).
