# Phase 4 — local operations dashboard

2026-09-11. The repository implementation and synthetic verification for the local
fullscreen, read-only operations display are complete. No target service was installed,
started, exposed, or approved. This is not hardware or production qualification.

## Delivered scope

- Loopback-only dependency-light Node server with a strict read-only route surface,
  CSP/security headers, bounded status parsing, local host metrics, and explicit
  startup refusal for non-loopback binds.
- Responsive fullscreen display for CPU, allocated RAM, root filesystem capacity,
  aggregate network rates, uptime, service/Tailscale/request/backup state, alerts,
  timestamps, staleness, and unavailable readings. Temperature stays unavailable
  until a real sensor source is qualified.
- Fixed-command Python collector for Docker Compose and Tailscale plus bounded atomic
  backup/request status inputs. It writes no secrets and degrades safely when inputs
  or commands fail.
- Inert systemd service/timer candidates for collector and display, plus a graphical
  user-service candidate that restarts Chromium after failure. All require the
  untracked operator approval marker.
- Operator deployment, interruption/restart/reboot, and rollback guidance; pinned
  npm dependency lock; CI jobs; product truth; and local unit/browser tests.

## Verification evidence

- Dashboard Node tests: 3 pass.
- Dashboard Playwright tests: 4 pass at 1440x900 and 390x844, including fixture
  evidence, zero controls/credential copy, no horizontal overflow, failure state,
  and last-value retention during API interruption.
- Axe: zero listed WCAG 2 A/AA and 2.1 AA violations.
- `npm audit --omit=dev`: zero reported vulnerabilities.
- Collector Python tests: 3 pass.
- Dashboard unit syntax parser: pass with disposable dependency/target stubs.
- Desktop/mobile screenshots were inspected. The one-time Impeccable detector's
  hero-label, padding, font, tiny-text, and decorative-glow issues were fixed.
- The independent finish reviewer returned `ship` after unavailable/stale wording,
  separate host/collector provenance, the direction contract, and chart label size
  were corrected. `DESIGN.md` and `.impeccable/design.json` record the shared system.

All status observations in browser tests are explicitly synthetic. No Docker daemon,
tailnet identity, production traffic, backup, or physical sensor was queried by them.

## Target blockers and residual risk

The Debian package index exposed Chromium `152.0.7977.82-1~deb13u1` on 2026-09-11,
while Debian's security tracker listed open issues fixed upstream in 153.0.8010.36.
The recorded minimum security version therefore exceeds the observed package. Do not
approve the kiosk until an adequate Debian update exists and the target candidate is
rechecked; do not bypass this gate with an unreviewed browser source.

The target must still qualify graphics/session startup, browser policy, sandbox and
resource behavior, exact temperature sensors, Docker/Tailscale permissions, firewall
exposure, display/dashboard/collector crash recovery, offline behavior, reboot and
power-loss behavior. Initial disk encryption still requires physical unlock after a
power loss. Dashboard health is not backup or database recovery evidence.

Phase 5 is next: encrypted backups, retention, integrity, isolated restore drills,
and update/recovery automation. Push remains blocked by the absent Git remote/upstream.
