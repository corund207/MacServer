# Development and delivery

Use a short-lived `infra/<feature>`, `admin/<feature>`, `dashboard/<feature>` or
`security/<review>` branch for subsequent work. Main contains reviewed source,
not deployed host state. Run the offline validator and unit tests before a focused
imperative commit. CI uses a commit-pinned checkout action, read-only contents
permission and no stored checkout credentials. Python has no third-party packages.

Do not change the configured Git identity or add co-author trailers. Review
`git diff --cached` for secrets before committing. Every delivery unit must be
pushed to its configured upstream. Do not force push or rewrite history.

This workspace had no Git metadata. Phase 1 initialized branch `main` and inherited
the existing Git identity. No remote URL was provided. The operator must supply
the real GitHub OWNER/REPOSITORY and create/identify that repository first. For an
EMPTY repository, replace the placeholder and run:

```sh
git remote add origin git@github.com:OWNER/REPOSITORY.git
git push --set-upstream origin main
```

If it already has commits, fetch and review its history before integration; do not
force push the local root commit. If the chosen upstream branch differs from main,
resolve that choice explicitly before pushing. Never put tokens in a remote URL.

Version policy: Debian major 13; security updates reviewed through Debian channels;
Docker/service upgrades require exact package versions, upstream commit and image
digests recorded together with validation. No service is deployable with null pins.
CI validates offline structure, not OS behavior, Tailscale semantics or exposure.
