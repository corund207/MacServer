# Foundation threat model and verification boundary

Scope: repository and inert candidate configurations. No target or public endpoint
has been authorized/listed for live security probing. No live exposure certification.

| Threat | Boundary / verification required before deployment |
| --- | --- |
| LAN attacker | Default-deny host input, OpenSSH keys over Tailscale; verify listeners and selected Docker backend forwarding from an authorized test client |
| Tailnet compromise | MFA, narrow grants and app authorization; deny non-admin test identity and keep DB/Studio ports inaccessible |
| Malicious public client | Public ingress disabled; later exact route allowlist, scoped credentials, rate/body/time limits, audit, RLS negative cases |
| Lost appliance/drive | Encrypted live storage and backup encryption plus independent escrow; recovery and physical-unlock test outstanding |
| Compromised container | Later pinned images, least privilege, isolated networks, no Docker socket in web apps; not implemented yet |
| Supply-chain/source substitution | Review source and pinned dependencies; stage refuses symlinks and special files; manifest is integrity bookkeeping, not signature authentication |
| Lockout/power loss | Local console, proven config restoration, thermal/boot qualification; no remote unattended unlock promise |

Blocking deployment findings: hardware unqualified; package/service pins unresolved;
Docker firewall integration untested; backups/restores absent; no real tailnet
policy tested. These have high impact only if the inert scaffold is deployed as if
complete. Remediation and safe commands are in docs/INSTALL.md and
UPDATE-ROLLBACK.md. Retest in an isolated VM, then only on the designated appliance.

Residual risk: static tests cannot establish effective SSH settings, Tailscale
identity, firewall exposure, recovery, or application authorization. Verdict:
NO-GO for installation automation, production data or public exposure. Phase 6
must produce the detailed authorized audit with evidence and severity ratings.
