# ADR 0001: Stage the appliance foundation before target installation

Accepted for Phase 1, 2026-09-06. This records the existing scaffold's intent before
completing it. The original brief is preserved in PROMPT_MAIN_BUILD.md; the phased
scope in context/PHASE-PLAN.md governs delivery.

Use Debian 13 amd64 minimal on the MacBook. Development-host eligibility is not
appliance qualification. Require hands-on T2 boot, SSD, keyboard, network, cooling,
and recovery checks before installation. No disk operation is automated here.
Use encrypted internal storage for live data; do not enable unencrypted disk swap.
Physical unlock after power loss is an accepted initial availability limitation.

Phase 1 produces immutable, private staging bundles and candidate host settings.
It has no apply mode. The subsequent data-service phase must select exact Docker
package versions and a reviewed upstream service revision/image digests before any
installation. Null pins in infra/versions.json mean unresolved, never latest.

Management uses ordinary OpenSSH keys over Tailscale, with tailnet least-privilege
policy plus application authorization for the future admin app. No browser terminal.
The candidate nftables input policy covers host traffic only. Docker forwarding
and Tailscale's generated rules need target-specific integration tests; the candidate
must not be installed as a complete production firewall.

Live data belongs on the internal SSD. The 250 GB removable drive is an encrypted,
rotating backup destination, never a live database disk. Mount identity, capacity,
absent-drive alerts, escrow, retention and isolated restores are Phase 5 gates.
One local copy cannot cover theft, fire, or loss of both devices.

Public ingress stays disabled until a domain/provider decision, exact route list,
server-side authorization, scoped credentials, limits, and negative tests exist.
Origin, Referer, User-Agent and client IP are not application identity.

Alternatives rejected: installing packages on the development host; manufacturing
unverified service pins; silently applying SSH/firewall settings; treating a tunnel
or tailnet membership as sufficient application authorization.
