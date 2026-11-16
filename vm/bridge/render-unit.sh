#!/usr/bin/env bash
# render-unit.sh - print the muse-bridge systemd unit for this install.
# The one renderer of bridge/muse-bridge.service, used by install.sh (the
# .rendered file) and by the self-heal hook (hooks/selfheal-lib.sh), so a
# healed unit is always the unit the installer produced.
#   render-unit.sh USER VM_DIR HOME CONFIG
# HOME and CONFIG tell the service where its configuration is: systemd gives
# the service the passwd home of USER, and on the Muse VM that is not the home
# holding .config/muselink/muselink.env (lib/muselink-common.sh).
set -u
[ $# -eq 4 ] || { echo "usage: render-unit.sh USER VM_DIR HOME CONFIG" >&2; exit 2; }
for v in "$@"; do
  # sed delimiter, sed replacement metacharacters, systemd quoting
  case "$v" in
    ''|*'|'*|*'&'*|*'\'*|*'"'*|*$'\n'*) echo "render-unit: unsupported value: $v" >&2; exit 2 ;;
  esac
done
spec() { printf '%s' "${1//%/%%}"; }   # systemd specifier escape
sed -e "s|__USER__|$1|g" -e "s|__VM_DIR__|$(spec "$2")|g" \
    -e "s|__HOME__|$(spec "$3")|g" -e "s|__CONFIG__|$(spec "$4")|g" \
    "$(cd "$(dirname "$0")" && pwd)/muse-bridge.service"
