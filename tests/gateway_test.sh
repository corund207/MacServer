#!/usr/bin/env bash
# CI: run the real Caddy filter against a stub Supabase gateway and check which
# paths reach it. Needs Docker. Uses only local containers.
set -euo pipefail
ROOT=$(cd "$(dirname "$0")/.." && pwd)
image=$(awk '/image: caddy/ { print $2 }' "$ROOT/gateway/compose.yml")
net=macserver-gw-test
cleanup() { docker rm -f gw-filter gw-stub >/dev/null 2>&1 || true; docker network rm "$net" >/dev/null 2>&1 || true; }
trap cleanup EXIT
cleanup
docker network create "$net" >/dev/null
docker run -d --name gw-stub --network "$net" --network-alias api-gw "$image" \
  caddy respond --listen :8000 --body upstream >/dev/null
docker run -d --name gw-filter --network "$net" -p 127.0.0.1:18080:8080 \
  -v "$ROOT/gateway/Caddyfile:/etc/caddy/Caddyfile:ro" "$image" >/dev/null
for _ in $(seq 30); do curl -fs http://127.0.0.1:18080/macserver-health >/dev/null && break; sleep 1; done

fails=0
expect() {  # expect CODE PATH
  local got
  got=$(curl -s -o /dev/null -w '%{http_code}' --path-as-is "http://127.0.0.1:18080$2")
  if [[ $got == "$1" ]]; then echo "ok   $1 $2"; else echo "FAIL $2: got $got, want $1"; fails=$((fails + 1)); fi
}
for p in /auth/v1/health /auth/v1/token /rest/v1/todos /storage/v1/object/public/a.png \
         /realtime/v1/websocket /functions/v1/hello /graphql/v1; do expect 200 "$p"; done
for p in / /project/default /pg/tables /pg /auth/v1/admin/users /auth/v1/admin \
         /AUTH/V1/ADMIN/users //auth/v1/admin/users /auth/v1/%61dmin/users \
         /realtime/v1/api/tenants /mcp /api/mcp /analytics/v1/x /rest/v2/x; do expect 404 "$p"; done
exit "$fails"
