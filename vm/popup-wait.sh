#!/usr/bin/env bash
# popup-wait.sh - wait for the answer to a card that is ALREADY on screen
# (staged earlier with popup-send.sh --no-wait [--owner NAME]).
#   popup-wait.sh <popup req id> [budget seconds, default 3600]
# A program that may restart while its card is up (bridge-service running an
# enrollment) stages once, remembers the id, and calls this again after a
# restart: an answer given while nobody was waiting is still on the PC and is
# picked up here.
# Output: wait-popup's block (STATUS/REQ_ID/DONE_AT/KIND/---RESULT---/answer)
# followed by ONE status line (DELIVERED / EXPIRED / WAITER ...), same words
# as popup-send.sh. Exit: 0 answered (or expired), 1 failed, 124 budget used.
set -u
. "$(dirname "$0")/lib/muselink-common.sh" || exit 2
REQ_ID="${1:-}" BUDGET="${2:-3600}"
ml_valid_id "$REQ_ID" || { echo "usage: popup-wait.sh <req id> [budget]" >&2; exit 3; }
case "$BUDGET" in ''|*[!0-9]*) echo "bad budget: $BUDGET" >&2; exit 3 ;; esac

clear_marker() {   # only our own card's marker
  [ "$(ml_inflight_get REQ_ID)" = "$REQ_ID" ] && rm -f "$ML_INFLIGHT"
  return 0
}

WOUT="$(ml_wait_loop "$REQ_ID" "$BUDGET")"; WRC=$?
if [ "$WRC" -eq 3 ]; then
  # Claimed but never delivered: an earlier waiter of ours took the answer
  # and died before printing it. check-popup.ps1 recovers it from the files
  # the PC keeps (needs the done file to be >= 60 s old).
  for _ in 1 2 3 4 5 6; do
    ROUT="$(ml_ps_file check-popup.ps1 -ReqId "$REQ_ID" 2>&1 | tr -d '\r')"
    if [ "$(printf '%s\n' "$ROUT" | head -n 1)" = "STATE:DONE" ]; then WOUT="$ROUT"; WRC=0; break; fi
    sleep 15
  done
fi
printf '%s\n' "$WOUT"
if printf '%s\n' "$WOUT" | grep -qx 'KIND:expired'; then
  clear_marker; echo "EXPIRED req=$REQ_ID"; exit 0
fi
case $WRC in
  0) clear_marker; echo "DELIVERED req=$REQ_ID (inflight cleared)"; exit 0 ;;
  124)
    # The card should have closed itself long before (owners stage expiring
    # cards), so it is gone or the PC is unreachable: retire it, or every
    # later card of the owner would be refused until the marker expires.
    if [ "$(ml_inflight_get REQ_ID)" = "$REQ_ID" ]; then
      ml_ps_file check-popup.ps1 -ReqId "$REQ_ID" -ForceExpire >/dev/null 2>&1
      rm -f "$ML_INFLIGHT"
    fi
    echo "WAITER BUDGET USED UP req=$REQ_ID (card retired)" >&2; exit 124 ;;
  *) echo "WAITER ENDED WITHOUT DONE req=$REQ_ID" >&2; exit 1 ;;
esac
