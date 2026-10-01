# shellcheck shell=bash
# Step: doomsday (lockdown on intrusion-like behaviour, on by default).
#
# Installs the detector (admin/doomsday.py), the root orchestrator
# (host/macserver-doomsday.sh), the lockdown firewall, the minute timer and
# the console banner. Detection is scoring + hysteresis over the collector's
# debug data, so one odd connection never isolates the Mac. The alert goes
# out through a GitHub Action BEFORE the network is cut. Unlocking and
# restoring need a Google Authenticator (TOTP) code.

step_doomsday() {
  install_doomsday
  cat <<'EOF'
Doomsday lockdown: when connections or processes look like an intrusion
(score over DOOMSDAY_SCORE for DOOMSDAY_CONSECUTIVE checks), the Mac sends
an email through a GitHub Action, then cuts the network (Tailscale, tunnel,
containers), stops every non-essential service, locks extra encrypted
volumes and shows a lockdown screen. Emergency SSH stays on the LAN only.
A Google Authenticator code moves it to DEBUG (still isolated) and back to
normal with everything auto-restored. Tailscale SSH, the tailnet and the
allowlisted AI-agent processes never score.
EOF
  if unattended; then conf_set DOOMSDAY_MODE "$(conf_get DOOMSDAY_MODE on)"
  elif confirm "Enable doomsday lockdown? (recommended)" y; then conf_set DOOMSDAY_MODE on
  else conf_set DOOMSDAY_MODE off; fi
  if [[ $(conf_get DOOMSDAY_MODE on) == off ]]; then ok "doomsday detection off (turn on: sudo macserver doomsday on)"; return 0; fi
  say "Two one-time setups (both can wait until later):"
  say "  sudo macserver doomsday setup-gh [owner/repo]   where the alert email goes"
  say "  sudo macserver doomsday setup-totp              Google Authenticator code for unlock/restore"
  ok "doomsday lockdown $(conf_get DOOMSDAY_MODE on) (manage: sudo macserver doomsday status)"
}

install_doomsday() {
  install -D -m 0755 "$SRC/admin/doomsday.py" /usr/local/lib/macserver/doomsday.py
  install -D -m 0755 "$SRC/host/macserver-doomsday.sh" /usr/local/lib/macserver/doomsday
  install -d -m 0755 /usr/local/share/macserver/host
  install -m 0644 "$SRC/host/nftables-doomsday.conf" /usr/local/share/macserver/host/nftables-doomsday.conf
  install -m 0644 "$SRC/host/doomsday-issue" /usr/local/share/macserver/host/doomsday-issue
  [[ -n $(conf_get DOOMSDAY_MODE) ]] || conf_set DOOMSDAY_MODE on
  [[ -n $(conf_get DOOMSDAY_SCORE) ]] || conf_set DOOMSDAY_SCORE 6
  [[ -n $(conf_get DOOMSDAY_CONSECUTIVE) ]] || conf_set DOOMSDAY_CONSECUTIVE 2
  [[ -n $(conf_get DOOMSDAY_GH_REPO) ]] || conf_set DOOMSDAY_GH_REPO "corund207/MacServer"
  install -d -m 0700 /var/lib/macserver
  if [[ ! -f /var/lib/macserver/doomsday.json ]]; then
    jq -n --arg mode "$(conf_get DOOMSDAY_MODE on)" '{mode: $mode, state: "normal"}' \
      > /var/lib/macserver/doomsday.json && chmod 0600 /var/lib/macserver/doomsday.json
  fi
  put_file "$SRC/host/macserver-doomsday.service" /etc/systemd/system/macserver-doomsday.service 0644
  put_file "$SRC/host/macserver-doomsday.timer" /etc/systemd/system/macserver-doomsday.timer 0644
  systemctl daemon-reload
  systemctl enable --now macserver-doomsday.timer >/dev/null
  ok "doomsday monitor installed (checks every minute; isolates only on repeated findings)"
}
