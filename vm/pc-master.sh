#!/usr/bin/env bash
# pc-master.sh - keep ONE persistent SSH master connection to the PC
# (formerly legion-master.sh). The connection itself cannot be stored; this
# script is the recipe that rebuilds it on demand. Measured 2026-11-08: a
# reused master costs P50 1.11 s / P95 1.20 s per command round trip.
#   pc-master.sh ensure|check|down|socket
# Every other script also sets ControlMaster=auto on the same socket, so a
# missing master is recreated by the next call even without `ensure`.
set -u
. "$(dirname "$0")/lib/muselink-common.sh" || exit 2
OPTS=("${ML_SSH_BASE[@]}" -o "ControlPath=$MUX_SOCK")
case "${1:-ensure}" in
  socket) echo "$MUX_SOCK" ;;
  check)  ssh "${OPTS[@]}" -O check "$ML_TARGET" 2>/dev/null ;;
  down)   ssh "${OPTS[@]}" -O exit "$ML_TARGET" 2>/dev/null; echo "master down" ;;
  ensure)
    if ssh "${OPTS[@]}" -O check "$ML_TARGET" 2>/dev/null; then
      echo "master up"; exit 0
    fi
    rm -f "$MUX_SOCK"
    ssh "${OPTS[@]}" -o ControlMaster=yes -o "ControlPersist=$MUX_PERSIST" -f -N "$ML_TARGET"
    if ssh "${OPTS[@]}" -O check "$ML_TARGET" 2>/dev/null; then
      echo "master started"
    else
      echo "master FAILED to start" >&2; exit 1
    fi
    ;;
  *) echo "usage: pc-master.sh ensure|check|down|socket" >&2; exit 3 ;;
esac
