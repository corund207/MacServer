# Phase 3 read-only administration threat model

Scope: checked-in app and inert deployment candidate; tests use synthetic data and
isolated loopback TLS only. No real tailnet, public endpoint, appliance service or
production database was probed. This is scoped implementation review, not Phase 6
security certification. Public administration is always NO-GO; target deployment
also remains gated by the operator checks in [ADMIN.md](../docs/ADMIN.md).

## Trust boundaries

```text
Approved tailnet device -> direct TLS to assigned appliance IP
  -> local daemon status + whois -> independent user-specific admin token
  -> 15-minute user/device-bound session -> read-only app routes
App -> local host observations / curated immutable text exports
App -> private append-and-sync audit file
App -X-> Docker / SQL / service operations / browser terminal
```

The local root, tailscaled, root-owned configuration/code and approved dependency
build are trusted. Tailnet membership alone is insufficient. Source IP locates
the daemon's identity record and is not proof by itself. Browser Origin is used
only for CSRF, not identity. Local app compromise is bounded by the service account
and OS permissions, not by the JavaScript router alone.

| Threat / impact | Implemented boundary and evidence | Residual / priority |
| --- | --- | --- |
| LAN/public access to administrative data | Private bind assertion, daemon identity, no proxy headers, proposed BPF deny-by-default; bind/header tests | Effective target network/firewall/ACL verification required; blocking deployment |
| Stolen tailnet account or tagged/shared node | Approved numeric user, explicitly authorized untagged/unshared device, expiry/status, separate random app token; negative identity/auth tests | IdP MFA/device posture required; cached control state can delay revocation; high |
| Stolen app token/session | Scrypt verifier, rate limits, 256-bit session ID, Secure/HttpOnly/Strict cookie, absolute expiry and node binding; TLS/browser tests | Browser/device compromise can use current session; revoke token + restart; high |
| CSRF/XSS/browser credential leakage | Exact origin/Host, custom login header and session CSRF, CSP, textContent rendering, no third-party assets, no request logging; route/browser tests | Browser extensions and local compromise outside app control; medium |
| Path/symlink/hardlink escape | Descriptor-relative traversal, no-follow, regular-file and single-link checks, allowlisted extensions and bounded reads; file route/unit tests | Root/privileged mount changes and live-file content races remain trusted; curated immutable exports only; high |
| Audit unavailable or tampered | Serialize, append, sync, cap before write; login/file delivery fail closed, logout still revokes; failure/capacity/reopen tests | App user/root can alter its audit; no external archive/signature; medium |
| Resource exhaustion | Request/login/session/CLI concurrency limits, time/body/output bounds, telemetry/list/read/audit caps; limits tests; proposed cgroup caps | No stress/DoS test; target memory and syscall/resource tuning outstanding; medium |
| Privileged operation or SQL abuse | No Docker/DB credentials/client/helper; execution endpoints return 501 even for authorized sessions | Capability not implemented; future helpers and SQL require fresh security design; blocking those features |
| Supply-chain/deployment drift | Exact npm lock, Node checksum, versioned identity review, pinned CI actions, unit approval marker | Upstream package signature validation, hosted CI and target runtime not exercised; blocking deployment |

## Fixed during resumed review

- Added explicit device authorization, shared-device refusal, peer/self expiry,
  supported-daemon and kernel/online-state checks. Versioned contract and source
  hashes: [ADMIN-IDENTITY.md](../docs/ADMIN-IDENTITY.md).
- Enforced audit byte cap before append; prevented blocking on configured FIFOs;
  tested audit-failure behavior and session invalidation on logout.
- Completed bounded file reads, audited denied file access, refused unsafe
  provisioning parents/IDs and preserved existing credentials on rerun.

Safe retest: run the local commands in ADMIN.md. Thirteen backend tests cover
identity, session/auth/CSRF, TLS, file/credential paths and audit failure boundaries.
Five browser tests include 21 axe scans, both themes, mobile, keyboard, network
failure and expiry. These do not prove screen-reader usability, daemon behavior,
remote revocation latency, OS sandboxing, recovery or lack of all vulnerabilities.

Before any target deployment, perform the listed private target checks with one
approved and one denied test identity, without scanning unrelated devices or
exercising destructive actions. Stop on instability. Keep private evidence of
versions, effective policy, observed listeners, audit outcomes and rollback drill.
All Phase 2 SQL/RLS/runtime and Phase 5 recovery gates remain in force. Detailed
severity/CVSS analysis, broad authorized audit and maintenance certification belong
to Phase 6; they were not silently included in this slice.
