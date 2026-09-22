# Staged installation and qualification

Start with [Install MacServer](GETTING-STARTED.md) for the full sequence. This
reference covers host qualification and inert configuration candidates. These
steps do not automatically install an OS or apply host settings. Run the offline
repository tools without sudo. Debian 13's Python 3.13 is the intended target.

1. Identify the MacBook and its disks locally. Verify a separate, restorable copy
   of existing data and a working recovery boot before considering installation.
   A reinstall/partition/format is destructive and requires fresh explicit user
   confirmation immediately beforehand. No destructive commands are supplied.
2. Obtain Debian 13 stable amd64 minimal installation media from the
   [official installer page](https://www.debian.org/releases/trixie/debian-installer/).
   Verify its checksums/signature following that page. Consult the
   [installation manual](https://www.debian.org/releases/trixie/installmanual).
   Qualify T2 startup requirements, SSD visibility, keyboard/trackpad, wired or
   wireless network, temperature and recovery from live media. Stock-image support
   for this exact MacBook has NOT been verified. Stop on a hardware blocker;
   do not silently substitute an OS or install an unreviewed custom kernel.
3. After separately authorized installation, require encrypted internal live
   storage, a non-root sudo operator, local console recovery, and no unencrypted
   disk swap. Initial reboot recovery requires physical disk unlock. Automated
   remote unlock is out of scope; do not promise unattended power-loss recovery.
4. From the checkout on that target run:

   ```sh
   python3 scripts/bootstrap.py preflight --state-parent /srv
   ```

   Expect `eligible: true` only on Debian 13 x86_64 with >=4 GiB RAM and >=40 GiB
   free on the selected filesystem. Verify `/srv` belongs to the intended internal
   SSD separately. This is a minimum screen, not workload sizing or disk approval.
   Exit 1 means ineligible; exit 2 means an input/read failure.
5. Stage a review bundle on either development or target host:

   ```sh
   python3 scripts/bootstrap.py stage --output /tmp/macserver-stage
   python3 scripts/bootstrap.py stage --output /tmp/macserver-stage
   python3 scripts/validate.py
   python3 -m unittest discover -s tests -v
   ```

   Expect `Bundle staged`, then `Bundle unchanged`. Choose a new path if that path
   exists with different content. The parent must already exist with no symlink
   components. Source must be a trusted, quiescent checkout, with no secrets under
   infra. Staging rejects symlinks/special files and never overwrites a destination.
   Bundle directory is 0700; files are 0600. SHA-256 manifest detects drift, not
   publisher authenticity. A failed partial stage must be preserved for inspection;
   retry into a new path. Do not edit source while staging.

## Candidate host settings — do not apply as a batch

Templates under infra/host are inert. Before copying any into `/etc`, save the
original configuration securely, test restoring it on an isolated Debian 13 VM,
keep local console and a known-good SSH session open, and follow the change/rollback
runbook. Require successful independent key login before removing password login.

| Template | Intended destination / gate |
| --- | --- |
| sshd.conf | `/etc/ssh/sshd_config.d/00-macserver.conf`; `sshd -t` and effective `sshd -T`, including applicable Match contexts, must confirm key-only login and disabled root login |
| nftables.nft | Candidate host input table only; `nft --check --file` in an isolated target-equivalent VM and exposure tests required; not a complete Docker firewall |
| journald.conf | `/etc/systemd/journald.conf.d/60-macserver.conf`; inspect effective config and journal disk usage |
| timesyncd.conf | `/etc/systemd/timesyncd.conf.d/60-macserver.conf`; one active time-sync service, synchronized clock required |
| logind.conf | `/etc/systemd/logind.conf.d/60-macserver.conf`; lid/idle policy requires thermal testing and console recovery; does not alone prevent every suspend path |
| 20auto-upgrades | `/etc/apt/apt.conf.d/20auto-upgrades`; unattended-upgrades package and reviewed Debian security origins required |
| 52macserver-unattended | `/etc/apt/apt.conf.d/52macserver-unattended`; automatic reboot disabled; maintenance operator must handle pending reboot |

Use Debian's security-origin defaults after reviewing `apt-config dump` and an
`unattended-upgrade --dry-run` on the target. No claim is made that a template alone
enables patching. Monitor SSD SMART/NVMe health and temperature using tools matched
to discovered hardware; no device path is assumed. Key-only SSH plus the tailnet
policy is the initial brute-force control; fail2ban is deferred unless evidence
justifies it. Verify journal persistence and bounded use with `journalctl --disk-usage`.

## Network and package gates

Enroll the appliance interactively in Tailscale with MFA/device controls. Do not
store enrollment keys here. Use ordinary OpenSSH over Tailscale, not Tailscale SSH.
Replace example identities in infra/tailscale-policy.example.json privately and
validate the complete policy with its built-in tests in the Tailscale editor.
Rules are additive: remove or narrow existing broad grants that defeat the intended
deny tests. Test a real unauthorized identity as well as the authorized operator.
See [grants](https://tailscale.com/docs/reference/syntax/grants) and
[policy tests](https://tailscale.com/docs/reference/syntax/policy-file).
Do not enable subnet routing, exit-node access, Serve or Funnel during this phase.

The nftables candidate restricts input but does not restrict Docker forwarding.
Docker's [Debian installation guide](https://docs.docker.com/engine/install/debian/)
and [nftables backend guide](https://docs.docker.com/engine/network/firewall-nftables)
have backend-specific requirements. Choose and document ONE supported backend with
an exact Engine version in Phase 2; validate interaction with Tailscale rules in a
VM before deployment. No database, Studio, Docker socket/API, metrics or management
container port may be published publicly. Even localhost mappings need exposure
tests against the selected Engine version. Public ingress is disabled.

Exact Docker Engine, CLI, containerd and Compose package pins, repository key
verification, reviewed image digests and an upstream revision are required before
an installer may be added. Null version fields are blockers, not permission to
use moving tags. No curl-to-shell setup is allowed.

## Local secret bootstrap

This command generates the backup password. The separate data-service environment
generator is documented in GETTING-STARTED.md. Use a NEW directory below an
existing private operator directory:

```sh
python3 scripts/init_secrets.py --directory "$HOME/.macserver-backup-secrets"
```

Expected: a generic success message, directory mode 0700 and password mode 0600.
Reruns refuse to rotate/overwrite. No password is printed. Never put this directory
in Git or infra. Store encrypted escrow independently of both the appliance and
backup drive before using the password; loss of it makes encrypted backups
unrecoverable. Generation alone does not create or verify a backup.
