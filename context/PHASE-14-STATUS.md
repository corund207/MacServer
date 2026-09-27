# Phase 14 — first-boot networking

Problem: stock Debian 13 lacks the Apple firmware for the 2019 Air's Broadcom Wi-Fi.
iPhone USB tethering linked up, but the hotspot's DHCP-supplied DNS did not answer,
so installer and apt downloads failed.

Delivered:
- `docs/NETWORK.md`: install network routes ranked A–D (USB-C Ethernet with RTL8153 or
  AX88179 and a power pass-through hub; an offline DVD-1 install; Android tethering;
  iPhone tethering with a DNS override in the installer shell). Optional Wi-Fi firmware
  goes through the reviewed t2linux guide, never curl-to-shell. A USB keyboard is needed
  because T2 input needs a driver stock Debian lacks.
- `scripts/network_doctor.py`: a read-only `check` covering devices by driver, default
  route, resolvers, IP and DNS probes, and missing Broadcom firmware, ending in one
  verdict. `fix-dns` requires root, a typed `yes` (or `--yes`) and a validated IP; it
  refuses a symlinked/managed resolv.conf, keeps timestamped backups and adds an
  idempotent persistent override for dhclient or dhcpcd.
- `tests/test_network_doctor.py`; GETTING-STARTED, the site build and the validator
  now include the network guide.

Not verified on the target: the actual MacBook, whether Debian 13 uses dhclient or
dhcpcd there, the installer shell DNS override, and the Wi-Fi firmware path. Python
is not installed on the development PC, so the Python tests run in CI.
