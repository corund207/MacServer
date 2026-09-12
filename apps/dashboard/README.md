# Local operations dashboard

Fullscreen, read-only status display for the appliance's physical console. It binds
to loopback only, has no login, accepts no mutations, and never reads credentials.
Host metrics come from the dashboard process; service, Tailscale, request, and backup
state come only from a bounded atomic JSON snapshot written by the separate collector.

```sh
npm ci --ignore-scripts
npm test
npm run test:browser
MACSERVER_DASHBOARD_BIND=127.0.0.1 \
MACSERVER_DASHBOARD_PORT=7460 \
MACSERVER_STATUS_FILE=/tmp/macserver-status.json \
npm start
```

Production paths and recovery checks are documented in `docs/DASHBOARD.md`. Missing,
invalid, or old snapshots render as unavailable/stale; the UI never synthesizes health.
