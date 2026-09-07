# Admin App Agent Instructions

The admin app is a private control plane, not a public product. It must bind only to localhost or the Tailscale interface and require Tailscale identity plus application authorization. Do not implement a browser terminal; terminal access remains SSH-only. All mutations need explicit confirmation, audit events, CSRF protection where applicable, strict input validation, and least-privilege service boundaries.
