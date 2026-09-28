# Manual install

Use this path if you would rather install Debian 13 yourself instead of using the
MacServer USB image. Do [part 4 of the guide](GUIDE.md#4-let-the-mac-start-from-usb-once) (Startup Security Utility) first. You need a
**USB keyboard** (the built-in one works only once the T2 kernel is installed) and a
cable or phone connection (see [NETWORK.md](NETWORK.md)). Use the Debian 13 `amd64`
netinst image, or DVD-1 for an offline install.

## 1. Install Debian

1. Hold **⌥ Option** while turning the Mac on, and pick the orange **EFI Boot** disk.
2. Choose **Install** (the text installer). When asked for missing firmware
   (`brcmfmac…`), answer **No**; MacServer installs it later.
3. Network: pick the wired or tethered interface. With an iPhone, see the DNS fix in
   [docs/NETWORK.md](NETWORK.md#iphone-tethering).
4. Hostname: `macserver`. **Leave the root password empty.** Your user then gets `sudo`.
5. Partitioning: **Guided – use entire disk and set up encrypted LVM**, all files
   in one partition. Choose a strong passphrase. You type it at every boot (on the
   USB keyboard until the T2 kernel is installed, then on the built-in one).
6. Software selection: untick everything except **standard system utilities**.
7. Finish, remove the stick, type the disk passphrase and log in.

## 2. Run the MacServer installer

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