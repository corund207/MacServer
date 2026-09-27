# Troubleshooting

Start with `sudo macserver doctor` and `sudo ./install.sh --status`. Any installer
step can be repeated with `sudo ./install.sh --redo STEP`.

**The Mac will not boot the USB stick.** Set Startup Security Utility to *No
Security* and allow external boot media (README step 1), then hold ⌥ Option at power on.

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

**The public URL returns 502 or 1033.** In Cloudflare, the public hostname must point
to `http://caddy:8080`. Check `sudo docker logs macserver-gateway-cloudflared-1`.

**A Supabase service keeps restarting.** `sudo macserver logs <service>` (for
example `auth`, `db`, `storage`).

**Fans are loud or the Mac runs hot.** `systemctl status t2fanrd`. The admin page shows
the CPU temperature and fan speed. Keep the vents clear. Running with the lid open helps cooling.
