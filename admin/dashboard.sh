#!/usr/bin/env bash
# MacServer console dashboard: health and connection details on the Mac's screen.
#
#   dashboard            full screen, refreshes every 5 seconds; q quits
#   dashboard --once     print it once (shown at login)
#   dashboard --kiosk    always-on mode for tty1: ignores every key, never exits
#
# Reads /run/macserver/status.json (written by macserver-status.timer; it holds
# no secrets), so it runs as an unprivileged user.
set -uo pipefail

STATUS=${MACSERVER_STATUS:-/run/macserver/status.json}
STUDIO_PORT=8443
DOCS=https://github.com/corund207/MacServer

B=$'\e[1m' G=$'\e[32m' Y=$'\e[33m' R=$'\e[31m' D=$'\e[2m' Z=$'\e[0m'

setup_state() {  # finished | running | stopped | waiting
  local now
  if [[ -f $STATUS ]] && [[ $(jq -r '.setup_done // false' "$STATUS" 2>/dev/null) == true ]]; then
    echo finished; return
  fi
  now=$(systemctl show -p ActiveState --value macserver-firstboot.service 2>/dev/null)
  case $now in
    activating) echo running ;;
    failed) echo stopped ;;
    *) if [[ -f $STATUS ]]; then echo waiting; else echo stopped; fi ;;
  esac
}

render() {
  local name age setup
  printf '%s MacServer%s  %s%s%s\n' "$B" "$Z" "$D" "$(date '+%a %d %b %Y  %H:%M')" "$Z"
  printf '%s\n' "$D----------------------------------------------------------------------------$Z"
  setup=$(setup_state)
  case $setup in
    finished) ;;
    running) printf '%s Setup is running.%s It continues by itself; this can take 15 minutes.\n\n' "$Y" "$Z" ;;
    *)
      printf '%s Setup has not finished.%s Log in (see below) and run:\n' "$Y" "$Z"
      printf '     sudo /opt/macserver-src/install.sh\n'
      printf ' It continues where it stopped and tells you what is needed.\n\n' ;;
  esac
  if [[ ! -f $STATUS ]]; then
    printf ' No health data yet.\n'
  else
    age=$(( $(date +%s) - $(jq -r '.generated_at // 0' "$STATUS") ))
    name=$(jq -r '.tailscale.name // ""' "$STATUS")
    jq -r --arg G "$G" --arg Y "$Y" --arg R "$R" --arg Z "$Z" --arg studio "$STUDIO_PORT" --arg name "$name" '
      def gb: . / 1073741824 | floor;
      def mark(c): if c then "\($G)ok\($Z)" else "\($R)!!\($Z)" end;
      (.containers // []) as $c | ($c | map(select(.state == "running")) | length) as $up |
      " Health",
      "   \(mark(.host.t2_kernel)) Mac        \(.host.model), kernel \(.host.kernel)",
      "   \(mark(.tailscale.state == "Running")) Tailscale  \(.tailscale.state)\(if $name != "" then "  (\($name))" else "" end)",
      "   \(mark(($c | length) > 0 and $up == ($c | length))) Supabase   \($up) of \($c | length) containers running",
      ($c[] | select(.state != "running") | "        \($R)stopped\($Z)  \(.name)  \(.status)"),
      "   \(mark((.host.disk.total - .host.disk.used) > 5368709120)) Disk       \(.host.disk.used | gb) of \(.host.disk.total | gb) GB used",
      "      Battery    \(if .host.battery then "\(.host.battery.percent)% \(.host.battery.status)" else "none" end)   CPU \(.host.sensors.cpu_temp_c // "?") C   load \(.host.load[0])",
      (if .host.updates_pending > 0 or .host.reboot_required then
        "   \($Y)!!\($Z) Updates    \(.host.updates_pending) pending\(if .host.reboot_required then ", restart needed" else "" end): sudo macserver update"
       else empty end),
      "",
      " Connect your apps and devices (from devices on your Tailscale network)",
      (if $name == "" then "   Tailscale is not connected yet."
       else
        "   Admin page      https://\($name)/",
        "   Supabase Studio https://\($name):\($studio)/",
        "   API URL         https://\($name):\($studio)"
       end),
      "   Public API URL  \(if .public_domain != "" then "https://\(.public_domain)" else "off  (sudo macserver public setup)" end)",
      "   App keys        sudo macserver keys"
    ' "$STATUS"
    (( age > 120 )) && printf '\n %s!! health data is %d minutes old%s\n' "$Y" $(( age / 60 )) "$Z"
  fi
  printf '\n Guides for connecting apps: %s\n' "$DOCS"
}

footer() {  # footer MODE
  printf '%s\n' "$D----------------------------------------------------------------------------$Z"
  if [[ $1 == kiosk ]]; then
    printf ' To log in: press %sCtrl + Option + F2%s (hold fn too if the brightness changes).\n' "$B" "$Z"
    printf ' Ctrl + Option + F1 comes back to this screen.\n'
  else
    printf ' q quits to the shell. Commands: sudo macserver help\n'
  fi
}

loop() {  # loop MODE
  local key buf
  trap 'printf "\e[?25h"; stty echo icanon 2>/dev/null; exit 0' TERM HUP
  if [[ $1 == kiosk ]]; then trap '' INT QUIT TSTP; fi
  # Keep the screen on: no blanking, no power-down.
  setterm --blank 0 --powerdown 0 2>/dev/null || true
  stty -echo -icanon 2>/dev/null || true
  printf '\e[?25l'
  while :; do
    buf=$(render; footer "$1")
    printf '\e[H\e[2J%s' "$buf"
    key=''
    read -rsn1 -t 5 key || true
    if [[ $1 != kiosk && $key == [qQ] ]]; then
      printf '\e[?25h\n'; stty echo icanon 2>/dev/null || true; return 0
    fi
  done
}

case ${1:-} in
  --once) render ;;
  --kiosk) loop kiosk ;;
  '') loop view ;;
  *) sed -n '4,6p' "$0" | sed 's/^# //'; exit 2 ;;
esac
