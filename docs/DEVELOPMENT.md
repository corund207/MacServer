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
