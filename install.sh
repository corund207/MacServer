#!/usr/bin/env bash
# MacServer guided installer for a 2019 MacBook Air (T2) running Debian 13.
#
#   sudo ./install.sh              run or resume the install
#   sudo ./install.sh --status     show which steps are done
#   sudo ./install.sh --redo STEP  run one finished step again
#   sudo ./install.sh --unattended take every default (used on first boot
#                                  after the MacServer USB installer)
#
# Every step is safe to re-run. Steps that change the kernel, the network,
# the firewall or public exposure ask first.
set -Eeuo pipefail

SRC=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
# shellcheck source=installer/lib.sh
source "$SRC/installer/lib.sh"
for f in "$SRC"/installer/steps/*.sh; do
  # shellcheck source=/dev/null
  source "$f"
done

STEPS=(preflight network base t2 host reboot wifi tailscale firewall docker supabase admin claude autoupdate public finish)

declare -A TITLE=(
  [preflight]="Check this Mac and Debian"
  [network]="Check the Internet connection and DNS"
  [base]="Install base packages and security updates"
  [t2]="Install the T2 kernel (keyboard, trackpad, Wi-Fi, fans)"
  [host]="Configure the laptop to run as a server"
  [reboot]="Boot into the T2 kernel"
  [wifi]="Set up Wi-Fi (optional)"
  [tailscale]="Join your Tailscale network"
  [firewall]="Turn on the firewall"
  [docker]="Install Docker"
  [supabase]="Install Supabase"
  [admin]="Install the private admin page"
  [claude]="Install Claude Code with the MacServer skill"
  [autoupdate]="Keep MacServer up to date from GitHub (after CI passes)"
  [public]="Public API route through Cloudflare (optional)"
  [finish]="Summary"
)

usage() { sed -n '2,11p' "$0" | sed 's/^# \{0,1\}//'; }

show_status() {
  local s
  for s in "${STEPS[@]}"; do
    if state_has "$s"; then printf '  [done] %-10s %s\n' "$s" "${TITLE[$s]}"
    else printf '  [    ] %-10s %s\n' "$s" "${TITLE[$s]}"; fi
  done
}

run_step() {
  local s=$1 rc=0
  head1 "${TITLE[$s]}"
  "step_$s" || rc=$?
  case $rc in
    0) state_add "$s" ;;
    10) exit 0 ;;   # step asked the operator to reboot or act, then re-run
    *) die "step '$s' failed (exit $rc). Fix the problem above, then run: sudo ./install.sh" ;;
  esac
}

main() {
  case ${1:-} in
    -h|--help) usage; exit 0 ;;
    --status) show_status; exit 0 ;;
  esac
  [[ $EUID -eq 0 ]] || die "run with sudo: sudo ./install.sh"
  if [[ ${1:-} == --unattended ]]; then export MACSERVER_UNATTENDED=1; shift; fi
  if [[ ${1:-} == --redo ]]; then
    [[ -n ${2:-} && -n ${TITLE[${2}]:-} ]] || die "unknown step '${2:-}'. Steps: ${STEPS[*]}"
    state_del "$2"; run_step "$2"; exit 0
  fi
  [[ -z ${1:-} ]] || { usage; exit 2; }

  say "${C_B}MacServer installer${C_0}  (progress is saved; re-run any time to continue)"
  local s
  for s in "${STEPS[@]}"; do
    state_has "$s" || run_step "$s"
  done
}

main "$@"
