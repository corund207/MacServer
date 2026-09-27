# Get the MacBook online

Stock Debian 13 has no driver firmware for the 2019 MacBook Air's T2-era Broadcom
Wi-Fi, so the internal Wi-Fi does not work during installation. iPhone USB
tethering connects, but its hotspot DNS often does not answer Linux, so downloads
fail even though the link is up. Use one of the routes below instead, in order of
preference. None of them needs router port forwarding or exposes anything.

| Route | Works in the installer | Works after install | Notes |
| --- | --- | --- | --- |
| A. USB-C Ethernet adapter | Yes | Yes | Most reliable. Best for a server anyway |
| B. Offline DVD install, then A, C or D | No network needed | Yes | Nothing is downloaded during install |
| C. Android USB tethering | Yes | Yes | No DNS problem |
| D. iPhone tethering with fixed DNS | Yes, with the steps below | Yes, with `fix-dns` | Works, but needs manual steps |
| Internal Wi-Fi | No | Only after adding Apple firmware | Optional, and not recommended for the server |

## A. USB-C Ethernet adapter (recommended)

Buy an adapter or hub with a **Realtek RTL8153** or **ASIX AX88179** chip. The stock
Debian kernel and installer drive these chips without extra firmware. The Air has only two USB-C ports,
and the server also needs power and the backup drive. A hub with **USB-C power
pass-through, Gigabit Ethernet and a USB-A port** covers all three. Plug the
Ethernet cable into your router, not into the Internet directly.

Boot the installer with the adapter already connected, choose the wired interface
(`enx...` or `eth0`) when asked, and let DHCP configure it. There are no further steps.

## B. Install offline from the DVD image

If you have no working network during installation, download
`debian-13.x.x-amd64-DVD-1.iso` instead of the netinst image from the
[official Debian CD page](https://www.debian.org/CD/http-ftp/), verify its checksum and
signature, and write it to the USB stick. When the installer asks to configure
the network, choose **Do not configure the network at this time**. When asked about
a network mirror, choose **No**. The installer finishes from the DVD alone.

After the first login, bring a network up using A, C or D. Then update the package source:

```sh
sudo sed -i '/^deb cdrom:/s/^/# /' /etc/apt/sources.list
```

Then add the network sources. `/etc/apt/sources.list.d/debian.sources` should contain:

```text
Types: deb
URIs: https://deb.debian.org/debian
Suites: trixie trixie-updates
Components: main non-free-firmware
Signed-By: /usr/share/keyrings/debian-archive-keyring.gpg

Types: deb
URIs: https://security.debian.org/debian-security
Suites: trixie-security
Components: main non-free-firmware
Signed-By: /usr/share/keyrings/debian-archive-keyring.gpg
```

Run `sudo apt update` and continue with [Install MacServer](GETTING-STARTED.md).

## C. Android USB tethering

Connect the phone by USB and turn on **USB tethering** in its hotspot settings. It
appears as a wired interface (`usb0` or `enx...`) in the installer and works with DHCP,
including DNS.

## D. iPhone USB tethering with fixed DNS

1. On the iPhone, turn on **Settings → Personal Hotspot → Allow Others to Join**.
   Connect the USB cable, unlock the phone and tap **Trust** if asked.
2. In the installer, select the `enx...` interface and let DHCP finish.
3. The installer usually fails at the mirror or security-update step. Before
   retrying, press **Alt+F2**, press Enter, and replace the broken DNS server:

   ```sh
   echo "nameserver 9.9.9.9" > /etc/resolv.conf
   echo "nameserver 149.112.112.112" >> /etc/resolv.conf
   ```

   Press **Alt+F1** to return to the installer and retry the step. If the installer
   restarts network configuration later, repeat this step.
4. After the first login, get DNS working again so you can clone this repository:

   ```sh
   echo "nameserver 9.9.9.9" | sudo tee /etc/resolv.conf
   ```

   Clone MacServer as in [Install MacServer](GETTING-STARTED.md), then make the fix
   permanent so the next DHCP renewal does not break it:

   ```sh
   sudo python3 scripts/network_doctor.py fix-dns
   ```

   The command shows which files it will change and waits for you to type `yes`.
   It saves a timestamped backup of each file it changes. To undo, copy the backup
   back over the original.

The resolvers are Quad9's public servers (9.9.9.9 and 149.112.112.112). Use
`--nameserver IP` (repeatable) to choose others.

If the tether interface does not come back after a reboot, add it to
`/etc/network/interfaces` using the name from `ip link`:

```text
allow-hotplug enxAABBCCDDEEFF
iface enxAABBCCDDEEFF inet dhcp
```

## Check the connection

At any point after cloning the repository, run the read-only checker:

```sh
python3 scripts/network_doctor.py
```

It lists network devices (iPhone tether, USB Ethernet, Wi-Fi), the default route and
DNS servers. It tests reaching the Internet by IP and by name. It prints one verdict:
`ok`, `dns-broken`, `no-internet` or `no-network`, plus the next step. It changes
nothing. The checker exits 0 only when the verdict is `ok`.

## Optional: internal Wi-Fi

Debian 13's kernel includes the `brcmfmac` driver for this Wi-Fi chip, but Apple's
firmware files are not redistributable. They must be copied from macOS on this
Mac. The [t2linux Wi-Fi guide](https://wiki.t2linux.org/guides/wifi-bluetooth/)
documents how to extract and rename them into `/lib/firmware/brcm/`. Review that
script before running it; do not pipe it into a shell. If macOS is still installed,
copy `/usr/share/firmware/wifi` to a USB stick **before** installing Debian. It is
the easiest source.

For the server itself, keep the wired link as the primary connection. Wi-Fi adds a
stored network password and a less stable link. It does not change the security
boundary: Tailscale still carries all administration, and no ports are forwarded.
The T2 internal keyboard and trackpad also need a driver that stock Debian lacks.
Use a USB keyboard for the installer and local console.
