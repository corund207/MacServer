# MacServer

Turn a 2019 Intel MacBook Air into a private server for your apps' backend:
**Supabase** (database, sign-in, file storage, realtime, functions) on **Debian 13**,
with the Mac's T2 hardware working, reachable only through **Tailscale**, and
optionally public for your apps through a **Cloudflare Tunnel**.

![The MacServer dashboard on the Mac's screen](docs/images/dashboard.png)

## Start here: **[the step-by-step guide](docs/GUIDE.md)**

In short:

1. Set up a free Tailscale account.
2. On Windows, run `iso\make-personal-usb.ps1` and flash the image to a USB stick.
3. Allow the Mac to start from USB, then start it from the stick.
4. Pick your Wi-Fi, answer a few questions, type **ERASE**. It installs itself.
5. Type the disk passphrase, scan the Tailscale QR code, and wait for the dashboard.
6. Open `https://macserver.<your-tailnet>.ts.net/` to get your app's URL and key.

## What you get

- **An always-on dashboard** on the Mac's screen: health, connections, services,
  live charts, and your addresses.
- **A private admin page** and **Supabase Studio** on your devices, over Tailscale.
- **Claude on the server** with a MacServer skill, started from the admin page, to
  help connect your apps and backends.
- **Safe defaults**: an encrypted disk, a firewall that admits only Tailscale, no
  open ports on your network, and only Supabase's app APIs ever public.

```text
Your devices ── Tailscale ──> admin page, Studio, SSH          (private)
Your apps ──── Cloudflare Tunnel ──> sign-in, data, storage APIs only (optional)
Everyone else ──> nothing
```

## Everyday commands

```sh
sudo macserver status      # health at a glance
sudo macserver keys        # app keys and the Studio login
sudo macserver update      # backup, then all updates
sudo macserver public off  # take the API off the Internet now
```

More: [guide](docs/GUIDE.md) · [troubleshooting](docs/TROUBLESHOOTING.md) ·
[network](docs/NETWORK.md) · [security design](docs/SECURITY.md) ·
[install Debian yourself](docs/MANUAL-INSTALL.md) · [development](docs/DEVELOPMENT.md)
