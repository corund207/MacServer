# RustDesk server (remote desktop)

MacServer can run your own RustDesk server, so a PC you want to control (the host) and
the computer you control it from (the controller) never depend on RustDesk's public
servers, and need no Tailscale. It is two containers, `hbbs` and `hbbr`, from
`rustdesk/compose.yml`, in `/opt/macserver/rustdesk`:

- **hbbs** (ID / rendezvous server) introduces the two clients to each other so they
  connect **directly**. All a normal session needs from the Mac is this handshake.
- **hbbr** (relay) carries a session only when the clients cannot reach each other.
  Relaying is never forced.

This is the one service that is reachable from the Internet without Tailscale or the
Cloudflare tunnel. The firewall admits **TCP 21115, 21116, 21117 and UDP 21116** and
nothing else new. The web-client ports (21118, 21119) stay closed. The private key
never leaves `/opt/macserver/rustdesk/data`.

## Set up

```sh
sudo macserver rustdesk setup     # asks for the public name, starts hbbs + hbbr, shows the key
sudo macserver rustdesk key       # show the public key again
```

(`setup` is also the optional `rustdesk` step of `install.sh`.) It needs three things
outside the Mac:

1. **A public name.** A free dynamic DNS name such as `yourname.duckdns.org`
   (duckdns.org: sign in, pick a subdomain, point it at your home's public IP, and run
   their updater so it follows IP changes). The bare public IP works until it changes.
2. **Router forwards** to the Mac's LAN address: TCP 21115-21117 and UDP 21116. Give the
   Mac a fixed address (DHCP reservation) so the forwards keep working.
3. **A real public IP.** Compare `curl ifconfig.me` on the Mac with the router's WAN IP.
   If they differ, the provider uses CGNAT and forwarding cannot work.

## Client settings (both RustDesk apps)

Settings > Network > ID/Relay server (unlock first):

| Field | Value |
| --- | --- |
| ID server | `<public name>` |
| Relay server | `<public name>` |
| API server | empty |
| Key | the output of `sudo macserver rustdesk key` |

From a computer on the controller's network: `nc -zv <public name> 21116`.

The host PC should have RustDesk **installed** (not the portable build) so it runs as a
service, with a permanent password set. Privacy mode is in the session menu.

## Day to day

```sh
sudo macserver rustdesk off       # stop (data kept)
sudo macserver rustdesk on        # start
cd /opt/macserver/rustdesk && sudo docker compose logs -f
```

Containers restart by themselves (`restart: unless-stopped`).

### Update the image

Images are pinned by version and digest. To update, put the new `image:` line (version
and the multi-arch digest from Docker Hub) in `rustdesk/compose.yml`, let the change
pass CI, then on the Mac, once it has been installed by the self-updater:

```sh
sudo macserver rustdesk update    # refreshes compose.yml, pulls, recreates
```

### Back up the data

`/opt/macserver/rustdesk/data` holds the server's keypair (`id_ed25519` is the private
key) and the device database. If the key is lost, every client needs the new public
key. Copy the backup off the Mac and treat it like a password:

```sh
cd /opt/macserver/rustdesk && sudo docker compose stop
sudo tar czf ~/rustdesk-data-$(date +%F).tgz -C /opt/macserver/rustdesk data
sudo docker compose start
```

Restore: stop, unpack the archive into `/opt/macserver/rustdesk/`, start.
