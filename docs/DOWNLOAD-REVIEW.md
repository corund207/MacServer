# Dependency and download review

The installation site uses exact npm versions with SHA-512 integrity in
`site/package-lock.json`. Registry metadata, source repositories, dependencies,
and lifecycle hooks were inspected before installation. All resolved artifacts
use `registry.npmjs.org`; none has an install hook. Use `npm ci --ignore-scripts`.

| Package | Version | Source | Purpose |
| --- | --- | --- | --- |
| gsap | 3.15.0 | github.com/greensock/GSAP | ScrollTrigger animations |
| @fontsource/outfit | 5.3.0 | github.com/fontsource/font-files | Local fonts, OFL license |
| marked | 18.0.13 | github.com/markedjs/marked | Build-time tracked documentation |
| @playwright/test | 1.63.0 | github.com/microsoft/playwright | Browser tests |
| @axe-core/playwright | 4.13.0 | github.com/dequelabs/axe-core-npm | Accessibility tests |

`npm audit` reported zero known vulnerabilities at review. This is an advisory
check, not proof that software cannot contain defects or malicious code.
The deployed site includes local fonts and GSAP files with licenses. It loads
no analytics, remote scripts, remote images, or third-party font requests.
The original SVG branding contains no executable content.

## Appliance software

Existing locks record upstream revisions, image digests, versions, and hashes:
`infra/supabase/release.lock.json`, `infra/admin/runtime.lock.json`, and
`infra/backup/`. Use official repositories and verify artifacts against recorded
hashes. Never pipe downloaded scripts into a shell. Review updates, regenerate
locks deliberately, run audits/tests, and retain the previous release.
Pins establish artifact identity; they are not vulnerability scans.

## GitHub Pages

Official GitHub actions are pinned to full commit IDs. Only the deploy job receives
`pages: write` and `id-token: write`. The artifact is exclusively `build/site`;
private configuration and appliance data are excluded.

Build with `cd site`, `npm ci --ignore-scripts`, then `npm run build`.
Run `npm test` after installing the pinned Playwright Chromium build on Linux;
Windows tests use installed Microsoft Edge.
