# Getting the Mac online

**With the MacServer USB image** the T2 kernel is already running, so the only
missing piece for Wi-Fi is Apple's firmware, which may not be redistributed. The
installer handles everything else: it detects Ethernet adapters and USB-tethered
phones, fixes the DNS problem common with iPhone tethering, pairs an iPhone if
needed, and (once online) can download the Wi-Fi firmware from Apple and connect.
Without cable or phone, put a `firmware.tar` made on macOS with t2linux's
`get-apple-firmware` on a USB drive and choose *Load Wi-Fi firmware*.

The rest of this page is for the [manual install](MANUAL-INSTALL.md), where stock
Debian's installer has no T2 support, so **Wi-Fi does not work during installation**.
Use one of these:

| Option | Works in the installer | Notes |
| --- | --- | --- |
| USB-C Ethernet adapter or hub | Yes | Best. Choose one with a Realtek RTL8153 or ASIX AX88179 chip |
| Android USB tethering | Yes | Turn on *USB tethering* in the phone's hotspot settings |
| iPhone USB tethering | Yes, with the DNS fix below | The link works, but its DNS often does not |
| Offline (DVD-1 image) | No network needed | Install offline, then connect before running MacServer |

For a server, keep a wired connection if you can. It is the most stable.

## iPhone tethering

1. On the iPhone: **Settings → Personal Hotspot → Allow Others to Join**. Connect
   the cable, unlock the phone and tap **Trust**.
2. In the Debian installer, pick the `enx…` interface and let it configure itself.
3. If the installer then fails to reach the Debian mirror, press **Alt+F2**, press
   Enter, and run:

   ```sh
   echo "nameserver 9.9.9.9" > /etc/resolv.conf
   ```

   Press **Alt+F1** to go back and retry the step.
4. After installation, if `apt` cannot resolve names:

   ```sh
   echo "nameserver 9.9.9.9" | sudo tee /etc/resolv.conf
   ```

   The MacServer `network` step then makes this permanent (it asks first). It
   configures the DHCP client so a lease renewal does not undo it. Once the `host`
   step has run, do not edit `/etc/resolv.conf` any more: see DNS below.

## Offline install

Use the **DVD-1** image. When asked to configure the network, choose **Do not
configure the network at this time**; when asked about a network mirror, choose **No**.
After the first boot, connect by Ethernet or tethering. Make sure
`/etc/apt/sources.list` points to `https://deb.debian.org/debian` (trixie, trixie-updates)
and `https://security.debian.org/debian-security` (trixie-security), with components
`main non-free-firmware`, and not only to the DVD. Then run the MacServer installer.

## Built-in Wi-Fi

The installer's `wifi` step (or `sudo macserver wifi` later) does this:

1. Runs `get-apple-firmware get_from_online` from the t2linux project, which
   downloads a macOS Recovery image from Apple and extracts only the Wi-Fi and
   Bluetooth firmware into `/lib/firmware/brcm/`.
2. Installs NetworkManager and connects to the network you name. The Wi-Fi
   password is stored by NetworkManager, readable by root only.

Wired and Wi-Fi can be used together. Either way, nothing on the Mac is reachable
from the local network: the firewall only admits Tailscale.

## DNS on the installed Mac

The `host` step (asks first) makes systemd-resolved the Mac's DNS: `/etc/resolv.conf`
points at its local cache on 127.0.0.53, which Docker's containers use too. It asks the
network's own DNS server and Quad9 / Cloudflare side by side (`host/resolved.conf`),
over TLS when a server offers it, so a phone or router whose DNS misbehaves does not
take the server offline. LLMNR and mDNS are off. Tailscale does not manage the Mac's
DNS (`tailscale set --accept-dns=false`); your other devices still reach the Mac by
its MagicDNS name.

Some routers drop DNS queries that carry EDNS0, which programs written in Go (such as
tailscaled) always send, and answer only plain UDP queries. Tools such as `curl` and
`apt` then work while tailscaled cannot fetch the admin page's HTTPS certificate.
systemd-resolved notices and falls back to plain queries for that server.

```sh
resolvectl status                          # servers per link and the global ones
resolvectl query deb.debian.org            # a lookup, and which server answered
sudo resolvectl show-server-state          # what each server supports (UDP, EDNS0, TLS)
```
