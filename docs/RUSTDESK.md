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

## If the connection is behind CGNAT (jonahchang.com setup)

When `curl ifconfig.me` differs from the router's WAN IP, port forwarding is impossible.
MacServer then exposes the server's **web-client ports only, through a Cloudflare Tunnel**
(outbound-only, like the Supabase route; no ports in, nothing new on the firewall):

- hbbs and hbbr run the same way (`macserver-rustdesk`, host networking, data in
  `/opt/macserver/rustdesk/data`), but with `RUSTDESK_RELAY=<your domain>` in its `.env`.
- A second, tiny compose project on the Mac (`~/rustdesk-ws`, run as the owner) runs only
  cloudflared, the same pinned image as MacServer's own gateway, with the tunnel token in
  `~/rustdesk-ws/.env` (0600). It publishes nothing; it only dials out.
- The tunnel's ingress (set in the Cloudflare Zero Trust dashboard) is exactly two routes:
  `ws://<domain>/ws/id -> 127.0.0.1:21118` and `ws://<domain>/ws/relay -> 127.0.0.1:21119`.
  Every other path on the hostname gets a 404. The gateways are Cloudflare itself (WSS), so
  X-Real-IP is set by Cloudflare and the spoofing warning on the web-client ports applies
  to nobody else: the firewall does not admit 21118/21119 from the LAN or the Internet at
  all, so only this tunnel reaches them.

**Web client settings** (the browser-based RustDesk client, `rustdesk.com/web`, or a
self-hosted copy): ID server `wss://<domain>/ws/id`, relay `wss://<domain>/ws/relay`,
key `sudo macserver rustdesk key`. Only the web client speaks websocket.

**RustDesk apps on devices inside your tailnet** can skip all of that: with Tailscale on,
set ID/relay server to the Mac (`<macserver host>.<tailnet>.ts.net`) — the firewall admits
every thing arriving over `tailscale0`, so ports 21115-21117 and UDP 21116 work directly,
even installed (non-web) apps. Direct sessions will connect straight through hbbs.

## Check the public route is alive

On the Mac, `sudo docker compose logs -f` in `~/rustdesk-ws` shows the tunnel state, and
an HTTP/1.1 WebSocket handshake must return `101 Switching Protocols` (a plain HTTP probe
correctly gets a 502 / dropped connection):

```
curl -sk -i --http1.1 \
  -H 'Connection: Upgrade' -H 'Upgrade: websocket' \
  -H 'Sec-WebSocket-Version: 13' -H 'Sec-WebSocket-Key: dGhlIHNhbXBsZSBub25jZQ==' \
  https://<domain>/ws/id
```

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
