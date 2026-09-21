# Install MacServer

MacServer turns a 2019 Intel MacBook Air into a private, security-first backend
appliance running Debian 13, Tailscale, Docker, and self-hosted
Supabase-compatible services. Administration stays inside Tailscale, backups are
encrypted, and no database or management service is exposed to the public Internet.

> **Current state:** the source bundle includes fail-closed commissioning and update
> tooling, but no physical appliance has been qualified or deployed.
> `deployment_enabled` means the private installer exists; `production_approved`
> remains `false` until the real hardware, recovery, migration, and application
> evidence passes.

## The finished system

```text
Administrator -- Tailscale --> key-only SSH / private admin app
Local display -- loopback  --> read-only status dashboard
Applications  -- approved route and credentials --> Supabase services
Internal SSD  -- encrypted --> Docker state, database, Storage, configuration
USB drives    -- encrypted --> rotating restic backups and restore drills

Public Internet --> no management access and no router port forwarding
```

PostgreSQL, Supabase Studio, Docker, SSH, metrics, the dashboard, and the admin
app must never be public. A future public application route must use an
outbound-only HTTPS tunnel plus server-side authorization, scoped credentials,
rate limits, audit logs, and Row Level Security (RLS).

## What you need

- The 2019 Intel MacBook Air, its charger, and physical console access.
- A verified backup of everything currently on the Mac and working recovery media.
- A Debian 13 `amd64` installer USB.
- A second computer with an SSH key, a safe way to transfer its public key, and
  access to your MFA-protected tailnet.
- Two 200–300 GiB removable drives for rotating encrypted backups; the planned
  250 GB flash drive is suitable only after its health and restore behavior pass.
- The source Supabase project inventory and a maintenance window if migrating data.
- Recovery-time and recovery-point targets, plus an off-site backup destination.

Do not store passwords, tokens, private keys, real domains, private IPs, device
UUIDs, database exports, or approval evidence in this repository.

## Prepare

**Destructive checkpoint:** installing Debian or changing partitions can erase the
Mac. Verify a separate restorable backup and recovery boot, then obtain explicit
confirmation immediately before the installer writes to disk.

1. Download the current Debian 13 `amd64` netinst image from the
   [official installer page](https://www.debian.org/releases/trixie/debian-installer/)
   and verify its published checksum/signature.
2. Boot the installer and first confirm that the internal SSD, keyboard/trackpad,
   networking, display, and recovery boot work on this T2-era Mac. Stop if they do
   not; stock Debian support for this exact machine is not yet verified.
3. Install a minimal system using encrypted LVM on the internal SSD. Create a
   non-root sudo user, but do not enable an SSH server yet. Do not create
   unencrypted swap.
4. Remember that full-disk encryption requires someone at the MacBook to unlock it
   after a cold boot or power failure.

Use the [Debian 13 amd64 installation manual](https://www.debian.org/releases/trixie/amd64/)
for the installer screens. After the first login, update the base OS and install
the small set of host tools used by this repository:

```sh
sudo apt update
sudo apt full-upgrade
sudo apt install ca-certificates curl git python3 nftables \
  unattended-upgrades smartmontools
```

These commands change the host. Keep the console available, reboot once, unlock
the disk locally, and verify Debian, storage, memory, networking, time, temperature,
lid behavior, and another console login before continuing.

## 2. Clone and verify MacServer

Clone the repository as the ordinary operator, not as root:

```sh
git clone https://github.com/corund207/MacServer.git
cd MacServer
python3 scripts/bootstrap.py preflight --state-parent /srv
python3 scripts/validate.py
python3 -m unittest discover -s tests -v
python3 scripts/bootstrap.py stage --output /tmp/macserver-stage
python3 scripts/bootstrap.py stage --output /tmp/macserver-stage
```

The preflight must report `eligible: true`. Staging should report `Bundle staged`
and then `Bundle unchanged`. This proves the checkout is internally consistent; it
does not install anything or certify the MacBook. If preflight fails, fix the
reported OS, architecture, RAM, or disk issue instead of bypassing it.

The full source verifier additionally needs the pinned Node/browser development
dependencies described in [development](DEVELOPMENT.md):

```sh
python3 scripts/release_verify.py --full --require-clean \
  --require-upstream-sync --output build/release-verification.json
```

## 3. Join Tailscale

MacServer uses ordinary OpenSSH over Tailscale. It does **not** use Tailscale SSH,
Serve, Funnel, an exit node, a subnet router, or router port forwarding.

1. Follow Tailscale's [manual Debian 13 package instructions](https://dl.tailscale.com/stable/)
   so the repository key and APT source are reviewable; do not pipe an installer
   script into a shell.
2. The private admin application currently requires Tailscale `1.102.3`. Confirm
   that exact package is available and install/pin it before deploying the app; a
   newer daemon requires a reviewed identity-parser update and its negative tests.
3. Enroll interactively, without putting an auth key in Git or shell history:

   ```sh
   sudo tailscale up
   tailscale status
   tailscale ip -4
   ```

4. Enable tailnet MFA, device approval, MagicDNS, and expiring user-device keys.
5. Privately replace `operator@example.com` in
   `infra/tailscale-policy.example.json`, merge the narrow grant into the complete
   tailnet policy, and assign `tag:macserver` to this server. Tailscale rules are
   additive, so remove any broad rule that would override the intended boundary.
6. Run the example allow/deny tests in Tailscale's policy editor. Only approved
   administrators should reach TCP 22 and 8443. Test a real non-admin identity too.

Do not commit the completed policy: it contains a real identity.

## 4. Qualify the firewall and host hardening

The files under `infra/host/` are candidates, not an installer. Rehearse each one
in a Debian 13 VM, save the target's original file, apply one subsystem at a time,
and prove its rollback before moving on:

| Candidate | Purpose | Minimum check before reload |
| --- | --- | --- |
| `sshd.conf` | Key-only SSH, no root login | `sshd -t` and a second login |
| `nftables.nft` | Tailscale-only host input | `nft --check --file ...` plus allow/deny tests |
| `journald.conf` | Persistent bounded logs | effective config and `journalctl --disk-usage` |
| `timesyncd.conf` | Correct security/audit time | one active synchronized time source |
| `logind.conf` | Server lid/power behavior | thermal, suspend, reboot, and console tests |
| unattended-upgrade files | Security updates without auto-reboot | reviewed origins and a dry run |

Do not apply these files as a batch. The nftables candidate covers host input only;
it is not complete Docker forwarding protection. From the local console, qualify
and install the host-input candidate before starting SSH. Confirm Tailscale remains
connected. In the target-equivalent VM, use test listeners to prove a LAN client
cannot reach TCP 22 or 8443; repeat that negative test on the appliance after each
real listener starts. If it fails, disable the listener and restore the saved
firewall configuration.

## 5. Set up key-only SSH

On the administrator's computer, create a dedicated key if needed:

```sh
ssh-keygen -t ed25519 -a 100 -f ~/.ssh/macserver
ssh-keygen -lf ~/.ssh/macserver.pub
```

Transfer only `macserver.pub` to the MacBook through trusted removable media. At
the local console, verify the fingerprint, install it for the Debian operator, and
keep the private key on the administrator's computer. On a fresh account with no
existing `authorized_keys` file:

```sh
install -d -m 0700 "$HOME/.ssh"
install -m 0600 /PATH/TO/macserver.pub "$HOME/.ssh/authorized_keys"
```

If `authorized_keys` already exists, merge the reviewed public key without
overwriting existing access. Keep SSH stopped while installing the package and
candidate configuration. Validate it before the first start:

```sh
sudo systemctl mask ssh.service ssh.socket
sudo apt install openssh-server
sudo install -m 0644 infra/host/sshd.conf \
  /etc/ssh/sshd_config.d/00-macserver.conf
sudo sshd -t
sudo sshd -T
sudo systemctl unmask ssh.service ssh.socket
sudo systemctl enable --now ssh
```

From the administrator's computer, connect over MagicDNS:

```sh
ssh -i ~/.ssh/macserver OPERATOR@MACSERVER_MAGICDNS_NAME
```

Keep that successful session open and prove a second independent key login. If it
fails, stop SSH and restore the saved configuration from the console. Never expose
TCP 22 on the LAN or Internet.

## 6. Install and qualify Docker

Use Docker's [official Debian repository instructions](https://docs.docker.com/engine/install/debian/),
not the convenience script. Install the exact package versions and verify the
SHA-256 values recorded under `docker_packages` in
`infra/supabase/release.lock.json`. Do not simply install `latest`.

Keep Docker administration behind `sudo`; membership in the `docker` group is
effectively root access. Put `/var/lib/docker` on the encrypted internal SSD before
creating production state. Confirm the locked Engine, CLI, containerd, Buildx, and
Compose versions, then validate Docker/Tailscale/firewall interaction on the target.
Docker 29's nftables backend is experimental, so select and document one supported
backend and test forwarding and reboot behavior instead of assuming the host-input
rules cover containers.

The Compose adaptation is deliberately internal-only and publishes no host ports:

```sh
python3 scripts/supabase_config.py
```

Do not print expanded Compose output after secrets exist. It can reveal them.

## 7. Prepare MacServer services

Create the live Storage directory on the encrypted internal SSD and generate the
Supabase environment in a new private directory outside Git:

```sh
sudo install -d -m 0750 /srv/macserver/storage
install -d -m 0700 "$HOME/.macserver-private"
python3 scripts/supabase_secrets.py \
  --directory "$HOME/.macserver-private/macserver-supabase" \
  --api-url https://api.example.test \
  --site-url https://app.example.test
```

Replace the example URLs with the approved application URLs. Review image-required
ownership before changing `/srv/macserver/storage`, escrow the generated environment
independently, and install the target copy as root-owned mode `0600` at
`/etc/macserver/supabase.env`.

Once a reviewed, root-owned deployment artifact is installed at `/opt/macserver`,
verify its Compose model using the private environment without displaying it:

```sh
sudo docker compose --env-file /etc/macserver/supabase.env \
  -f /opt/macserver/infra/supabase/compose.json config --quiet
```

Do not run `docker compose up` directly. After completing the real qualification
checks, use the fail-closed [deployment guide](DEPLOYMENT.md) to commission a
brand-new private stack. Production approval still requires the backup, migration,
RLS, and final acceptance gates below.

Optional components should be enabled only when needed:

| Profile | Includes | Initial memory cap |
| --- | --- | --- |
| `core` | PostgreSQL, Auth, PostgREST, Envoy | 2.75 GiB |
| `functions` | Edge Functions, with core | +512 MiB |
| `storage` | Storage API and imgproxy, with core | +768 MiB |
| `realtime` | Realtime, with core | +512 MiB |
| `management` | Studio and postgres-meta, with core | +1 GiB |

An 8 GiB MacBook needs at least 2 GiB measured headroom. The complete 5.5 GiB
selection is not qualified on a 4 GiB machine.

## 8. Set up encrypted backups before migration

Backups come before production data. Use at least two rotating removable drives and
keep one disconnected off-site. Formatting a drive is destructive and requires a
fresh confirmation after checking its exact device and preserving anything needed.

The supported flow is:

1. Create a supported filesystem on the already-confirmed drive outside this repo.
2. Record its exact UUID and mount it at `/mnt/macserver-backup` with
   `rw,nodev,nosuid,noexec`.
3. Install the exact locked restic `0.19.1` binary after checking its SHA-256.
4. Copy `infra/backup/config.example.json` to a private target config and replace
   only reviewed paths and the observed UUID.
5. Generate and independently escrow the backup password:

   ```sh
   python3 scripts/init_secrets.py \
     --directory "$HOME/.macserver-backup-secrets"
   ```

6. Initialize the repository, make a real backup, run its integrity check, and pass
   an isolated restore drill. Each UUID confirmation must use a freshly observed
   value, never one copied from this README.
7. Only after those checks, install the candidate systemd units and create the
   untracked approval marker. Retention pruning is manual because it deletes old
   repository data.

Follow [backup and restore](BACKUP-RESTORE.md) for the exact commands, expected
output, failure handling, rotation, retention, restore drill, and off-host copy.
Never restore over live data or use `docker compose down -v` as recovery.

## Connect

Do the first import into a new isolated private target, never into a populated
deployment. One MacServer stack is one Auth/key/failure boundary; unrelated trust
domains need separate stacks.

1. Inventory database/Auth/Storage versions, extensions, roles, schemas, policies,
   jobs, functions, buckets, OAuth/SMTP settings, app routes, credential scopes, and
   the real tenant ownership model.
2. Freeze writes for the final export. From an authorized workstation, use the
   pinned Supabase CLI `2.116.0`, a password-free TLS-verified database URL, and a
   mode-`0600` `PGPASSFILE` to export roles, schema, and data separately.
3. Seal and verify the private export directory:

   ```sh
   python3 scripts/migration_bundle.py seal /PRIVATE/EXPORT/DIRECTORY
   python3 scripts/migration_bundle.py verify /PRIVATE/EXPORT/DIRECTORY
   ```

4. Review the SQL as privileged code. Import roles, schema, and data in that order
   into the isolated target, stopping on the first error.
5. Capture the target catalog and run the offline audit:

   ```sh
   python3 scripts/schema_audit.py /PRIVATE/CATALOG/catalog.json
   ```

6. Test every real app through its API using two users from different tenants plus
   anonymous, expired, forged, and revoked credentials. Verify foreign row and
   Storage access is denied. Never test client authorization with `service_role`.
7. Copy Storage bytes through authenticated Storage APIs and compare item counts,
   byte counts, and checksums. Deploy only reviewed function bundles and secrets.
8. Cut over only after backup restore, RLS negatives, object parity, application
   checks, and routing checks all pass. Keep the old source intact and read-only for
   the agreed rollback window.

The exact export/import cautions, SQL order, Storage procedure, tests, cutover, and
rollback are in [migration](MIGRATION.md).

## 10. Qualify private admin and the local dashboard

These interfaces are optional and remain inert until their target checks pass.

- The [private admin guide](ADMIN.md) covers the dedicated unprivileged user,
  pinned Node runtime, Tailscale HTTPS certificate, per-user app token, TCP 8443
  policy, audit storage, and rollback. It is read-only and has no browser terminal.
- The [dashboard guide](DASHBOARD.md) covers its loopback-only service, bounded
  collector, kiosk session, stale-data behavior, browser floor, and recovery.

Never make either interface public. Do not create a `DEPLOYMENT-APPROVED` marker
until the guide's real target tests pass; a marker is a guard, not proof.

## 11. Public application traffic

Public ingress is not implemented. Keep it disabled until the exact domain,
applications, HTTPS routes, authorization rules, credential scopes, rate limits,
audit events, abuse controls, and RLS tests are reviewed. When implemented, expose
only the approved application routes through an outbound-only tunnel. Never attach
the whole Envoy gateway or any management surface to that tunnel.

Origin, Referer, User-Agent, and client IP are context, not authentication.

## 12. Final acceptance

MacServer is ready for production data only when all of these are true:

- Debian/T2 hardware, encrypted storage, recovery boot, console, thermals, lid,
  network, reboot, and power-loss behavior passed on the exact MacBook.
- Tailscale policy, unauthorized-user tests, key-only SSH, and host/Docker firewall
  exposure tests passed.
- Pinned services started cleanly with measured memory, health, reboot, and rollback.
- Two rotating encrypted backups, independent password escrow, an off-host copy,
  and a measured isolated restore passed.
- The real migration rehearsal, app authorization, RLS, Storage, and function tests
  passed with no blocking findings.
- Private admin/dashboard checks passed if those components are enabled.
- Only explicitly approved public HTTPS application routes exist; every management
  and database route remains unreachable publicly.
- A failed update and compatible rollback were rehearsed.

Record the result without secrets, review
[the security audit](../security/SECURITY-AUDIT.md), and issue an evidence-backed GO or
NO-GO. Missing evidence means NO-GO.

## Detailed guides

| Task | Runbook |
| --- | --- |
| Debian, staging, host templates, and Tailscale | [Installation](INSTALL.md) |
| Docker and Supabase-compatible services | [Data services](DATA-SERVICES.md) |
| Managed Supabase migration | [Migration](MIGRATION.md) |
| Private admin and identity | [Admin](ADMIN.md) · [Identity](ADMIN-IDENTITY.md) |
| Local status display | [Dashboard](DASHBOARD.md) |
| Encrypted backup and recovery | [Backup and restore](BACKUP-RESTORE.md) |
| Maintenance and rollback | [Update and rollback](UPDATE-ROLLBACK.md) |
| Commission, update, and release rollback | [Deployment](DEPLOYMENT.md) |
| Final evidence checklist | [Final verification](FINAL-VERIFICATION.md) |
| Current project handoff | [Next phase](../context/NEXT-PHASE.md) |

For development and source-only testing, see [development](DEVELOPMENT.md).
