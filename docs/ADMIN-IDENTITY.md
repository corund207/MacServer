# Administration identity contract

Reviewed against Tailscale **v1.102.3**, 2026-09-07. No live identity or daemon
status was queried during verification; installed CLI version/help and public
source were inspected. The app requires this daemon version at runtime. Future
upgrades require a reviewed parser change and the same negative tests, not merely
relaxing the version check. The package installation/authentication is a target gate.

Versioned official sources and SHA-256 of their raw contents:

| Source | SHA-256 |
| --- | --- |
| [tailcfg.Node and UserProfile](https://github.com/tailscale/tailscale/blob/v1.102.3/tailcfg/tailcfg.go) | `0af864d103ca14a0d8aecfc1b2395430bf1f65c36305ccc7e076126069b3a9d3` |
| [ipnstate.Status and PeerStatus](https://github.com/tailscale/tailscale/blob/v1.102.3/ipn/ipnstate/ipnstate.go) | `f0a1ec32918502b0c248a095c5d123704e4ad47a4bbc72db6e3e18f3af084268` |
| [CLI whois JSON encoder](https://github.com/tailscale/tailscale/blob/v1.102.3/cmd/tailscale/cli/whois.go) | `a34ea651d4fbfbdc437f3a3caaf919567fbf6d9e957b9a63295c94d767f88ef4` |
| [WhoIsResponse](https://github.com/tailscale/tailscale/blob/v1.102.3/client/tailscale/apitype/apitype.go) | `5d03084d35beeb67a3cc3504ada5035103ac4757eb9794aec1890ed817520047` |

`whois --json IP` encodes Node and UserProfile. The app accepts only an exact
single-address node prefix matching the actual socket peer, a stable node ID,
positive safely representable numeric user ID matching Node.User, and explicit
MachineAuthorized=true. Tagged, shared, WireGuard-only, unsigned-only and expired
nodes are denied. It never uses AllowedIPs/subnet routes as proof of node ownership.
UserProfile.LoginName and browser identity headers are not authorization inputs.
Node.KeyExpiry is checked when present. The version's omitted zero time means
expiry disabled; malformed/past present values are denied. Require expiring user
devices in operator policy; omission is not misreported as proof of expiry enabled.

Before whois, `status --json --peers=false` must report the reviewed daemon version,
Running, kernel TUN, online Self, non-expired self key, and only Tailscale addresses.
Startup verifies a non-loopback bind belongs to those assigned addresses. Daemon
logout/expiry, unknown formats, command errors, 2.5-second timeout, excess output
and verifier saturation fail closed. Four CLI commands maximum run concurrently.
Private HTTPS connects directly to the app; no proxy identity scheme is supported.

The daemon is a trust boundary and can have stale control-plane knowledge during
partitions. This app does not promise instantaneous revocation or replace tailnet
MFA, narrow grants, device approval, key expiry, clock synchronization and firewall
verification. An independent random app token remains mandatory; sessions are tied
to both user and node and are checked against freshly resolved identity per request.

Safe local verification: `cd apps/admin && npm test`. Fixtures exercise supported
and rejected shapes without real tailnet requests. Before deployment, use only the
authorized appliance and test peers to verify actual CLI/daemon version agreement,
approved/denied users, tagged/shared/expired peers, logout/expiry, certificates,
clock and effective network policy. Keep outputs private; do not paste identities.
Rollback of a parser change is a reviewed prior app build plus matching daemon
contract in a maintenance window; never weaken identity checks to restore access.
