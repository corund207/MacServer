# shellcheck shell=sh
# Show the MacServer summary when someone logs in to an interactive shell.
case $- in
  *i*)
    if [ -x /usr/local/lib/macserver/dashboard ] && [ -z "${MACSERVER_QUIET:-}" ]; then
      /usr/local/lib/macserver/dashboard --once
      printf '\n Commands: sudo macserver help     Full-screen dashboard: macserver dashboard\n\n'
    fi ;;
esac
