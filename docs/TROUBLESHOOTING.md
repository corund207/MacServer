# Troubleshooting

Start with `sudo macserver doctor` and `sudo ./install.sh --status`. Any installer
step can be repeated with `sudo ./install.sh --redo STEP`.

**The Mac will not boot the USB stick.** Set Startup Security Utility to *No
Security* and allow external boot media ([guide, part 4](GUIDE.md#4-let-the-mac-start-from-usb-once)), then hold ⌥ Option at power on.

**The USB installer says "not connected" although Wi-Fi shows Deactivate.** Read the
reason under "No Internet connection yet". The installer sets the clock from the
network by itself (NTP by IP address, then a web server's Date header); if the
network blocks both, the reason mentions a certificate or a WRONG clock: choose
**Set the date and time by hand**. The log is `/tmp/macserver-setup.log` (menu:
Open a shell).

**The screen is black.** Press Shift. After a restart the screen can stay dark at
the disk passphrase prompt: type the passphrase and press Enter.

**The dashboard shows "Setup has not finished".** Press 2 on the dashboard, log in, and
run `sudo /opt/macserver-src/install.sh`; it continues where it stopped. The
first-boot log is `/var/log/macserver-firstboot.log`.

**The dashboard is not on the screen (a login prompt instead).** Run
`sudo ./install.sh --redo host` from a current MacServer checkout (see the
[guide, part 11](GUIDE.md#11-updates-to-macserver-itself)), then log out.
The dashboard itself: `systemctl status getty@tty1`, and `macserver dashboard` to
see errors in a terminal.

**Start Claude session does nothing, or asks to sign in.** Sign in once as your user:
`claude`, then `claude remote-control` (answer y, Ctrl+C). Details:
`journalctl -u macserver-claude -n 50`.

**The built-in keyboard does not work at the disk passphrase prompt.** It works only
after the `t2` step and reboot, because the step adds the T2 keyboard driver (`t2bce_vhci`) to the boot image.
Until then, use a USB keyboard. If it stops working after an update, run
`sudo update-initramfs -u -k all`.

**`uname -r` does not contain `t2` after the reboot.** Choose the T2 kernel under
*Advanced options for Debian* in GRUB (hold Shift or press Esc at boot), then run
`sudo apt install --reinstall linux-t2-lts && sudo update-grub`.

**No Wi-Fi networks listed.** Run `sudo macserver wifi`. It fetches the firmware
if it is missing. Check `sudo dmesg | grep brcmfmac`.

**`apt` or the installer cannot resolve names.** `sudo ./install.sh --redo network`
(or see [NETWORK.md](NETWORK.md)).

**The admin page says Forbidden.** Your Tailscale login is not in `ADMIN_LOGINS` in
`/etc/macserver/macserver.conf`. Edit it; no restart is needed.

**`tailscale serve` failed in the admin step.** Turn on MagicDNS and HTTPS
Certificates at https://login.tailscale.com/admin/dns, then
`sudo ./install.sh --redo admin`.

**The admin page does not load ("Secure Connection Failed",
`SSL_ERROR_INTERNAL_ERROR_ALERT`, or a TLS error from curl).** Tailscale has no HTTPS
certificate for the Mac yet. `journalctl -u tailscaled | grep -i cert` says why. If it
shows `lookup acme-v02.api.letsencrypt.org ... i/o timeout`, tailscaled cannot use
the Mac's DNS: run `sudo tailscale set --accept-dns=false` and
`sudo /opt/macserver-src/install.sh --redo host` (DNS through systemd-resolved, see
[NETWORK.md](NETWORK.md#dns-on-the-installed-mac)), then reload the page; the first
load after that takes up to a minute while the certificate is issued.

**Containers cannot resolve names (Auth e-mail, Edge Functions calling other sites)
after a DNS change.** A container keeps the DNS servers it was started with. Restart
it (`sudo docker restart NAME`, or `sudo macserver restart` for all of Supabase);
`sudo docker exec NAME cat /etc/resolv.conf` shows `ExtServers: [host(127.0.0.53)]`
when it uses the Mac's resolver.

**The public URL returns 502 or 1033.** In Cloudflare, the public hostname must point
to `http://caddy:8080`. Check `sudo docker logs macserver-gateway-cloudflared-1`.

**A Supabase service keeps restarting.** `sudo macserver logs <service>` (for
example `auth`, `db`, `storage`).

**Fans are loud or the Mac runs hot.** `systemctl status t2fanrd`. The admin page shows
the CPU temperature and fan speed. Keep the vents clear. Running with the lid open helps cooling.

**The dashboard's MacServer line says it was rolled back or skipped.** A new version
failed its tests or the health check after installing; the Mac stays on the version
that worked. `sudo macserver autoupdate status` says why; the details are in
`/var/log/macserver-self-update.log`. It installs the next version that passes.
