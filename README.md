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

Installing is like [Omarchy](https://omarchy.org): flash the MacServer image to a USB
stick, boot the Mac from it, answer a few questions, and walk away. The image is
Debian 13 with the T2 kernel already in it, so the Mac's own keyboard, trackpad
and Wi-Fi work while installing. The installer erases the SSD, installs encrypted
Debian, and on first boot finishes Tailscale, the firewall, Docker, Supabase and
the admin page by itself.

## What you need

- The 2019 MacBook Air (MacBookAir8,2) and its charger.
- A USB stick of 4 GB or more, and a USB-C adapter for it if the stick is USB-A.
- An Internet connection for the install: a **USB-C Ethernet adapter** (best), a
  phone with **USB tethering** (iPhone or Android), or Wi-Fi (see below).
- A free [Tailscale](https://tailscale.com) account. In its
  [DNS settings](https://login.tailscale.com/admin/dns), turn on **MagicDNS** and
  **HTTPS Certificates**.
- For public access (optional): a domain on Cloudflare.

## 1. Let the Mac boot from USB (once)

Turn the Mac on while holding **⌘ Command + R** to enter macOS Recovery. Choose
**Utilities → Startup Security Utility** and set **Secure Boot: No Security** and
**Allow booting from external or removable media**. Without this, the T2 chip
refuses to start anything but macOS.

## 2. Make the USB stick

Download `macserver-<version>.iso` and its `.sha256` from the
[latest release](https://github.com/corund207/MacServer/releases/latest), check the
checksum, and write the image to the stick with [balenaEtcher](https://etcher.balena.io)
(or `dd if=macserver-<version>.iso of=/dev/<stick> bs=4M`). This erases the stick.

## 3. Install

1. Plug in the stick and your network (Ethernet adapter or phone), hold **⌥ Option**
   while turning the Mac on, and choose the orange **EFI Boot** disk.
2. The MacServer installer starts by itself. It checks the connection, and fixes the
   DNS problem common with iPhone tethering automatically. If there is no cable or
   phone, choose **Connect to Wi-Fi**. Wi-Fi needs Apple's firmware, which only comes
   from Apple: connect once by cable/phone and the installer downloads it, or load a
   `firmware.tar` from a USB drive.
3. Answer the questions: server name, your username and password, disk encryption
   (recommended) and its passphrase, time zone, built-in Wi-Fi, and optionally a
   Tailscale auth key.
4. Type **ERASE** to confirm. **Everything on the SSD, including macOS, is erased.**
   The install takes 10–20 minutes.
5. Remove the stick and restart. Type the disk passphrase on the Mac's keyboard.
6. First-boot setup runs on the screen (about 15 minutes; it downloads Supabase).
   When a **QR code** appears, scan it with your phone and sign in to Tailscale to
   add the server to your tailnet. Setup then finishes and the screen switches to
   the **MacServer dashboard**: health, addresses and what to do next. It stays on
   and never blanks. To log in on the Mac itself, press **Ctrl + ⌥ Option + F2**
   (hold fn too if the brightness changes); **Ctrl + ⌥ Option + F1** returns to the
   dashboard. Logging in also prints the dashboard; `macserver dashboard` opens the
   live one.

Prefer to install Debian yourself? See [docs/MANUAL-INSTALL.md](docs/MANUAL-INSTALL.md).

## 4. Use it

From any device on your tailnet:

- **Admin page:** `https://macserver.<your-tailnet>.ts.net/` shows health,
  temperature, fans, battery, services and your app keys.
- **Supabase Studio:** `https://macserver.<your-tailnet>.ts.net:8443/`. Get the login
  with `sudo macserver keys`.
- **Shell:** `ssh <user>@macserver` (Tailscale SSH, no keys to copy).
- **Claude Code:** installed on the server with a `macserver` skill that knows what the
  Mac runs and its rules. Sign in once over SSH (`ssh <user>@macserver`, run `claude`,
  follow the login link; then run `claude remote-control`, answer `y`, press Ctrl+C).
  After that, the admin page's **Start Claude session** button opens a session you
  continue at claude.ai/code or in the Claude app, to help connect your apps and
  backends. Its version is pinned by MacServer.

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

More: [development](docs/DEVELOPMENT.md) · [network options](docs/NETWORK.md) · [security design](docs/SECURITY.md) ·
[troubleshooting](docs/TROUBLESHOOTING.md)
