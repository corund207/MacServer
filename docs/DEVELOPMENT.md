# Development

## Fast local loop (WSL2 on Windows, or any Debian 13 machine with KVM)

One-time setup on Windows: `wsl --install -d Debian`, reboot, then in the checkout:

```powershell
wsl -d Debian -u root -- bash -c "apt-get update && apt-get install -y qemu-system-x86 qemu-utils ovmf mtools dosfstools xorriso python3 rsync curl gpg"
```

Then, from the checkout:

```powershell
wsl -d Debian -u root -- bash tests/local.sh all     # disk + image + vm
wsl -d Debian -u root -- bash tests/local.sh disk    # only the disk installer (~3-8 min)
wsl -d Debian -u root -- bash tests/local.sh vm      # only the VM test (reuses the last image)
```

| Stage | What it proves | Time |
| --- | --- | --- |
| `disk` | `macserver-setup` installs onto a loop device; 22 checks on the result | 3-8 min |
| `image` | the USB image assembles (the base system is built once and cached) | 1st ~5 min, then <1 min |
| `vm` | the image installs itself in QEMU/KVM, and the result boots under UEFI, unlocks and starts first-boot setup | ~3 min |

`FULL_UEFI=1` makes the `vm` stage boot through the image's own UEFI menu, like the Mac does.
Logs land in `/var/tmp/macserver-local/repo/build/` inside WSL.

## Checks before every commit

`bash tests/run.sh` (shell syntax, ShellCheck, library and Python tests). CI runs the
same plus the gateway and installer-image workflows.

## Personal image with Wi-Fi from the first screen

Apple's Wi-Fi firmware may not be redistributed, so published images do not contain
it. For your own Mac you can build an image that does:

```powershell
wsl -d Debian -u root -- bash iso/fetch-wifi-firmware.sh /var/tmp/macserver-wifi-firmware.tar
wsl -d Debian -u root -- bash -c "MACSERVER_WIFI_FIRMWARE=/var/tmp/macserver-wifi-firmware.tar bash tests/local.sh image"
```

This downloads macOS Sonoma Recovery from Apple, extracts `/usr/share/firmware`, and
renames it with t2linux's renamer. The result is `build/macserver-local-personal.iso`.
It loads the firmware at startup and opens the Wi-Fi picker straight away. Never
share or publish it; CI never builds one (`tests/test_repo.py` checks this).

`iso\make-personal-usb.ps1` wraps both commands for Windows and copies the result to
`Downloads\macserver-personal-wifi.iso` with a checksum check. `iso/build.sh`
rebuilds the cached base whenever its inputs change (`build/base/inputs.sum`, the same
inputs as CI's cache key).

## Dashboard previews

The Mac's screen dashboard (`admin/tui.py`) can be drawn without the Mac:

```sh
python3 tests/tui_preview.py dashboard.png healthy        # or: setup, trouble
python3 tests/tui_preview.py small.png healthy 100 30     # other screen sizes
```

It renders sample data with the Mac's console font (Lat15-Terminus 16x32, from
`console-setup-linux`) and the 16 console colours, so the PNG shows what the Retina
screen will show. `docs/images/dashboard.png` comes from it. Only characters in
`tui.GLYPHS` may be drawn; `tests/test_dashboard.py` checks them against the font.
