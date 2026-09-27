# Getting the Mac online

Stock Debian does not include Apple's firmware for the MacBook Air's Broadcom
Wi-Fi, so **Wi-Fi does not work during installation**. MacServer installs the
firmware afterwards. Until then, use one of these:

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
   configures the DHCP client so a lease renewal does not undo it.

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
