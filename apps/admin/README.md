# Private admin read-only foundation

Phase 3 is incomplete and not deployed. See [status](../../context/PHASE-3-STATUS.md).
No Supabase services, database connections or privileged operations are required
for compilation or the in-process synthetic tests:

```sh
cd apps/admin
npm ci
npm test
npm audit --omit=dev
npm run test:browser
```

Expected: production TypeScript builds, 13 backend tests, 5 browser tests and
a dependency audit result. Browser tests use an isolated loopback HTTPS fixture,
synthetic identity/credentials, temporary exports and ephemeral certificates.
They use /usr/bin/chromium if present; otherwise run `npx playwright install chromium`
for the pinned Playwright browser. ADMIN_TEST_CHROMIUM can select an explicit test
executable. Automated accessibility checks supplement manual screen-reader testing. `npm start` is a production entry point, not a local unauthenticated demo.
Do not run it as root, a Docker-group member, a sudoer or a Tailscale operator.

The deployment candidate requires direct HTTPS on a Tailscale address verified
against `tailscale status --json`; actual peers are verified with
`tailscale whois --json IP` on every request. Tagged devices, unapproved user IDs
and all forwarded identity headers are rejected. Loopback bind cannot authenticate
real loopback clients; use in-process tests until an authorized private test setup
exists. Never expose this app through Serve, Funnel, a public tunnel or a proxy.
Use an administrator-only tailnet grant and target firewall rules before deployment.

The env example is documentation only. Startup requires valid protected TLS files,
a canonical HTTPS `.ts.net` origin with the same port, a private credential file,
and a writable private audit path. Credentials are loaded at startup; restarting
invalidates sessions and reloads authorization. Certificate renewal/restart and
the systemd hardening example remain to be implemented and verified.

`npm run provision -- NUMERIC_TAILSCALE_USER_ID /trusted/new-private-directory`
creates a new directory with a salted verifier and `initial-token.txt`, both 0600.
It refuses an existing directory and never prints the token. This command was not
run against real configuration. Retrieve the token privately and keep it in a
password manager; do not send it through chat, commit it, or add it to command
arguments. Credential/config parents must be trusted and non-writable by attackers.

File roots must contain only curated non-secret text exports, be read-only to this
service account, and exclude live database, Storage, secret and backup directories.
Downloads are attachments, limited to 1 MiB, and are not content-redacted. Listing
is capped at 200 entries or 1,000 scanned records. Descriptor traversal prevents
symlink escape; deployment must still enforce OS mount/ownership boundaries.

Audit writes are durable before login/file responses. Events omit raw credentials,
SQL, filenames and contents. The UI shows only 200 events since startup; durable
JSONL remains on disk. At 16 MiB, audited actions fail closed until an operator
archives the file and restarts using a reviewed procedure. External archival and
tamper resistance remain future work. Logout revokes the session even if audit
fails. Keep the audit path and its parent private and preserve evidence.

No host settings were applied, so rollback at this checkpoint is retaining the
previous Git revision; do not discard the working copy. Future deployment rollback
must stop only the admin service, preserve audit/credentials and restore a reviewed
prior build/configuration. Never stop or remove database volumes to roll back this
UI. All production data and destructive changes retain the existing recovery gates.

Official references checked during implementation:
[Tailscale CLI](https://tailscale.com/docs/reference/tailscale-cli),
[HTTPS certificates](https://tailscale.com/docs/how-to/set-up-https-certificates),
[WhoIsResponse source](https://github.com/tailscale/tailscale/blob/main/client/tailscale/apitype/apitype.go),
[Fastify server](https://fastify.dev/docs/latest/Reference/Server/),
[Supabase private proxy boundaries](https://supabase.com/docs/guides/self-hosting/self-hosted-proxy-https).
Pin and review the actual target CLI's identity/expiry fields before deployment.
