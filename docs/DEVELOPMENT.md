# Development and delivery

Use a short-lived `infra/<feature>`, `admin/<feature>`, `dashboard/<feature>` or
`security/<review>` branch for subsequent work. Main contains reviewed source,
not deployed host state. Run the offline validator and unit tests before a focused
imperative commit. CI uses a commit-pinned checkout action, read-only contents
permission and no stored checkout credentials. Python has no third-party packages.

Do not change the configured Git identity or add co-author trailers. Review
`git diff --cached` for secrets before committing. Every delivery unit must be
pushed to its configured upstream. Do not force push or rewrite history.

The canonical repository is `https://github.com/corund207/MacServer.git`.
Use the configured identity and authenticate Git transport with `gh auth setup-git`.
Never rewrite an existing author's identity. A clone of `main` already tracks origin.

## Full verification

On Debian 13, install the exact Node runtime recorded in
`infra/admin/runtime.lock.json` after verifying its official checksum. In both
`apps/admin` and `apps/dashboard`, run `npm ci --ignore-scripts`, `npm audit`,
and `npx --no-install playwright install chromium`. Package versions and integrity
hashes are committed in each lockfile; review changes before installation.
Then, from the repository root:

```sh
python3 scripts/release_verify.py --full --require-clean --require-upstream-sync
```

Expected: every source check passes, while the target verdict remains NO-GO until
hardware qualification. Linux permissions, procfs, systemd, and browser fixtures
require Linux; native Windows is suitable for editing and structural checks only.
Git enforces LF line endings so vendored SHA-256 checks remain portable.

Version policy: Debian major 13; security updates reviewed through Debian channels;
Docker/service upgrades require exact package versions, upstream commit and image
digests recorded together with validation. No service is deployable with null pins.
CI validates offline structure, not OS behavior, Tailscale semantics or exposure.

## Documentation site

```sh
cd site
npm ci --ignore-scripts
npm run build
npm test
```

The site builds into `build/site`. If that folder is locked on a local machine (for
example, created by another account or blocked by Windows Controlled Folder Access),
set `SITE_OUT` to another ignored or temporary folder for both commands, such as
`SITE_OUT=/tmp/macserver-site/site`. CI and GitHub Pages use the default path.
