# MacServer

Turn a 2019 Intel MacBook Air into a private backend server for your apps:
**Supabase** (Postgres, Auth, Storage, Realtime, Edge Functions) running in Docker
on **Debian 13**, with the **t2linux kernel** so the Mac's T2 hardware works. It is
managed privately over **Tailscale**, and your apps can optionally reach the API
publicly through a **Cloudflare Tunnel**.

```text
Your devices ── Tailscale ──> SSH, admin page, Supabase Studio   (private)
Your apps ──── Cloudflare Tunnel ──> /auth /rest /storage /realtime /functions only
LAN / Internet ──> nothing (firewall drops it, no router port forwarding)
```

One guided installer does the work. It checks the Mac and installs the T2 kernel,
Wi-Fi firmware, Tailscale, the firewall, Docker, Supabase and the admin page. It
asks before every risky step and can be re-run safely at any point.

## What you need

- The 2019 MacBook Air and its charger.
- A USB stick (2 GB or more) for the Debian installer.
- A **USB keyboard**. The built-in keyboard only works after the T2 kernel is installed.
- A network the Debian installer can use: a **USB-C Ethernet adapter or hub** (best),
  Android USB tethering, or iPhone USB tethering. The built-in Wi-Fi works only after
  installation. See [docs/NETWORK.md](docs/NETWORK.md).
- A USB-C hub with power pass-through helps: the Air has only two ports.
- A free [Tailscale](https://tailscale.com) account.
- For public access (optional): a domain on Cloudflare.

## 1. Prepare the Mac (erases macOS)

Installing Debian **erases everything on the Mac**. Copy off anything you want to keep first.

1. **Allow Linux to boot.** Turn the Mac on while holding **⌘ Command + R** to enter
   macOS Recovery. Choose **Utilities → Startup Security Utility** and set:
   - Secure Boot: **No Security**
   - Allowed Boot Media: **Allow booting from external or removable media**

   Without this, the T2 chip refuses to boot the Debian USB stick.
2. **Make the installer stick.** Download the Debian 13 `amd64` **netinst** image
   from [debian.org](https://www.debian.org/distrib/netinst). If you will have no
   network during installation, use the **DVD-1** image instead. Check it against
   the published `SHA512SUMS`, then write it with
   [balenaEtcher](https://etcher.balena.io) or `dd`.
3. Plug in the USB keyboard, the network adapter (or phone) and the stick.

## 2. Install Debian

1. Hold **⌥ Option** while turning the Mac on, and pick the orange **EFI Boot** disk.
2. Choose **Install** (the text installer). When asked for missing firmware
   (`brcmfmac…`), answer **No**; MacServer installs it later.
3. Network: pick the wired or tethered interface. With an iPhone, see the DNS fix in
   [docs/NETWORK.md](docs/NETWORK.md#iphone-tethering).
4. Hostname: `macserver`. **Leave the root password empty.** Your user then gets `sudo`.
5. Partitioning: **Guided – use entire disk and set up encrypted LVM**, all files
   in one partition. Choose a strong passphrase. You type it at every boot (on the
   USB keyboard until the T2 kernel is installed, then on the built-in one).
6. Software selection: untick everything except **standard system utilities**.
7. Finish, remove the stick, type the disk passphrase and log in.

## 3. Run the MacServer installer

```sh
sudo apt install -y git
git clone https://github.com/corund207/MacServer.git
cd MacServer
sudo ./install.sh
```

If `apt` says it cannot resolve hosts (common with iPhone tethering), run
`echo "nameserver 9.9.9.9" | sudo tee /etc/resolv.conf` and try again.

The installer walks through these steps (`sudo ./install.sh --status` shows progress):

| Step | What happens |
| --- | --- |
| preflight | Confirms Debian 13, UEFI, the T2 chip, RAM, disk space and encryption |
| network | Checks Internet and DNS; fixes phone-tethering DNS if needed |
| base | Base tools and automatic Debian security updates |
| t2 | t2linux kernel, Apple firmware tool and fan daemon (signed repo, pinned key) |
| host | Lid close does nothing, no sleep, 80% battery charge limit, log limits |
| reboot | Reboots into the T2 kernel. **Log in and run `sudo ./install.sh` again** |
| wifi | Optional: downloads Apple's Wi-Fi firmware and connects to your network |
| tailscale | Joins your tailnet with Tailscale SSH; you choose the admin logins |
| firewall | Drops everything not arriving over Tailscale |
| docker | Docker Engine, publishing container ports on 127.0.0.1 only |
| supabase | Supabase `self-hosted/v0.8.2` with generated secrets |
| admin | Private admin page and Studio on your tailnet over HTTPS |
| public | Optional: Cloudflare Tunnel for your apps' API |

In the [Tailscale DNS settings](https://login.tailscale.com/admin/dns), turn on
**MagicDNS** and **HTTPS Certificates** before the `admin` step.

## 4. Use it

From any device on your tailnet:

- **Admin page:** `https://macserver.<your-tailnet>.ts.net/` shows health,
  temperature, fans, battery, services and your app keys.
- **Supabase Studio:** `https://macserver.<your-tailnet>.ts.net:8443/`. Get the login
  with `sudo macserver keys`.
- **Shell:** `ssh <user>@macserver` (Tailscale SSH, no keys to copy).

Connect an app:

```js
import { createClient } from '@supabase/supabase-js'
const supabase = createClient('https://api.example.com', '<publishable key>')
```

Use the public URL if you enabled the tunnel, or the tailnet URL for private apps.
**Never** put the secret or `service_role` key in an app.

Day to day:

```sh
sudo macserver status      # health at a glance
sudo macserver update      # database backup, then Debian + T2 kernel + containers
sudo macserver backup      # database dump to /var/backups/macserver (copy it elsewhere)
sudo macserver public off  # instantly take the API off the Internet
sudo macserver doctor      # network, DNS and T2 driver checks
```

## Good to know

- **Power cuts:** with disk encryption, someone must type the passphrase after a
  reboot. That is the price of a stolen laptop not exposing your database.
- **Battery:** the charge stops at 80% when the kernel supports it, which protects
  a battery that is always plugged in. The admin page shows whether the limit is active.
- **Kernel updates** come from the t2linux project through `sudo macserver update`. They are
  not automatic. Debian security updates are automatic and never reboot on their own.
- **Backups:** `sudo macserver backup` makes a local dump only. Copy dumps off the Mac.

More: [network options](docs/NETWORK.md) · [security design](docs/SECURITY.md) ·
[troubleshooting](docs/TROUBLESHOOTING.md)
