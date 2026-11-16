#!/usr/bin/env bash
# popup-send.sh - THE standard way to put a card in front of the user.
#   popup-send.sh --mode info   --text-file msg.txt [--no-wait]
#   popup-send.sh --mode choice --text-file msg.txt --options-file opts.txt [--summary "..."]
#   popup-send.sh --mode input  --text-file msg.txt [--wait-max SECONDS]
#   ... [--owner NAME] --no-wait   a program waits itself (popup-wait.sh)
# One call does the whole proven flow:
#   stage text/mode/options by direct transfer (scp) with a SHA-256 manifest
#   -> write the inflight marker -> PC-side popup-stage.ps1 verifies every
#   hash BEFORE showing anything (mismatch = no card), closes any stale card,
#   binds the request id -> optionally wait for the answer with a series of
#   short waits (ml_wait_loop); the hook fallback (vm/hooks/popup-fallback.sh)
#   covers a broken or exhausted main path.
# Hand-built encoded staging is retired: it corrupted text bytes once
# (2026-11-03).
# Exit: 0 staged / answered, 1 refused or failed, 3 usage, 124 wait budget
# used up (the hook fallback still delivers).
#
# Identity protocol v3 (implemented 2026-11-12) (delay cards): new option --expires-in SECONDS
# (integer 10..3600). Stage popup-expires-in.txt with that number and list it
# in the hash manifest; the card closes itself that long after it is shown and
# answers with kind "expired" (pc/popup.ps1). When the option is given, the
# wait budget defaults to SECONDS + 60 and an "expired" answer is reported as
# its own line "EXPIRED req=<id>" so muselink_id/ui.py can map it to timeout.
# popup-ask.sh passes the option through unchanged.
set -u
. "$(dirname "$0")/lib/muselink-common.sh" || exit 2

MODE="" TEXT_FILE="" OPTIONS_FILE="" SUMMARY="" WAIT=1 FORCE=0 WAIT_MAX="${POPUP_WAIT_MAX:-3600}" EXPIRES="" OWNER=""
while [ $# -gt 0 ]; do
  case "$1" in
    --mode) MODE="$2"; shift 2 ;;
    --text-file) TEXT_FILE="$2"; shift 2 ;;
    --options-file) OPTIONS_FILE="$2"; shift 2 ;;
    --summary) SUMMARY="$2"; shift 2 ;;
    --wait) WAIT=1; shift ;;
    --no-wait) WAIT=0; shift ;;
    --wait-max) WAIT_MAX="$2"; shift 2 ;;
    --expires-in) EXPIRES="$2"; shift 2 ;;
    --owner) OWNER="$2"; shift 2 ;;
    --force) FORCE=1; shift ;;
    *) echo "unknown arg: $1" >&2; exit 3 ;;
  esac
done
[ -n "$MODE" ] && [ -n "$TEXT_FILE" ] && [ -f "$TEXT_FILE" ] || { echo "usage: --mode <info|choice|input> --text-file <path>" >&2; exit 3; }
case "$MODE" in info|choice|input) ;; *) echo "bad mode: $MODE" >&2; exit 3 ;; esac
if [ -n "$EXPIRES" ]; then
  case "$EXPIRES" in ''|*[!0-9]*) echo "bad --expires-in: $EXPIRES" >&2; exit 3 ;; esac
  [ "$EXPIRES" -ge 10 ] && [ "$EXPIRES" -le 3600 ] || { echo "bad --expires-in: $EXPIRES" >&2; exit 3; }
  if [ -z "${POPUP_WAIT_MAX:-}" ] && [ "$WAIT_MAX" = "3600" ]; then WAIT_MAX=$((EXPIRES + 60)); fi
fi
if [ -f "$ML_INFLIGHT" ] && [ "$FORCE" -ne 1 ]; then
  echo "REFUSED: an inflight wait already exists ($ML_INFLIGHT); use --force only if it is stale" >&2
  exit 1
fi

REQ_ID="req_$(date +%Y%m%d_%H%M%S)_${RANDOM}"
TMPD="$(mktemp -d)"
trap 'rm -rf "$TMPD"' EXIT

cp "$TEXT_FILE" "$TMPD/popup-text.txt"
printf '%s' "$MODE" > "$TMPD/popup-mode.txt"
if [ -n "$OPTIONS_FILE" ]; then cp "$OPTIONS_FILE" "$TMPD/popup-options.txt"; else : > "$TMPD/popup-options.txt"; fi
MANIFEST="popup-text.txt popup-mode.txt popup-options.txt"
if [ -n "$EXPIRES" ]; then printf '%s' "$EXPIRES" > "$TMPD/popup-expires-in.txt"; MANIFEST="$MANIFEST popup-expires-in.txt"; fi
( cd "$TMPD" && for f in $MANIFEST; do
    printf '%s %s\n' "$f" "$(sha256sum "$f" | awk '{print $1}')"
  done ) > "$TMPD/popup-expected-hash.txt"
[ -n "$SUMMARY" ] || SUMMARY="$(head -c 120 "$TEXT_FILE" | tr '\n' ' ')"
SUMMARY="$(ml_safe_text "$SUMMARY")"

for f in $MANIFEST popup-expected-hash.txt; do
  ml_scp_to "$TMPD/$f" "$(ml_pc_fwd "$f")" || { echo "scp $f failed" >&2; exit 1; }
done

NOW=$(date +%s)
# --owner NAME: a program (not Muse) waits for this card, possibly across its
# own restarts (popup-wait.sh). The hook fallback leaves owned cards alone
# instead of handing their answer to Muse, and the marker lives only as long
# as the card can (wait budget + 10 min), not 12 hours.
OWNER="$(printf '%s' "$OWNER" | tr -cd 'A-Za-z0-9_-' | cut -c1-32)"
if [ -n "$OWNER" ]; then
  ml_inflight_write "REQ_ID=$REQ_ID" "WAIT_TYPE=popup" "CREATED_AT=$NOW" \
    "EXPIRES_AT=$(( NOW + WAIT_MAX + 600 ))" "MODE=$MODE" "SUMMARY=$SUMMARY" "OWNER=$OWNER"
else
  ml_inflight_write "REQ_ID=$REQ_ID" "WAIT_TYPE=popup" "CREATED_AT=$NOW" \
    "EXPIRES_AT=$(( NOW + 43200 ))" "MODE=$MODE" "SUMMARY=$SUMMARY"
fi

OUT="$(ml_ps_file popup-stage.ps1 -ReqId "$REQ_ID" 2>&1 | tr -d '\r' | grep -E 'STAGED|STAGE_FAIL' | head -1)"
case "$OUT" in
  STAGED*) echo "$OUT" ;;
  *) echo "STAGING FAILED: ${OUT:-no response} (inflight removed)" >&2; rm -f "$ML_INFLIGHT"; exit 1 ;;
esac

if [ "$WAIT" -ne 1 ]; then
  echo "ARMED req=$REQ_ID (no wait; hook fallback will deliver)"
  exit 0
fi

WOUT="$(ml_wait_loop "$REQ_ID" "$WAIT_MAX")"; WRC=$?
printf '%s\n' "$WOUT"
if [ -n "$EXPIRES" ] && printf '%s\n' "$WOUT" | grep -qx 'KIND:expired'; then
  rm -f "$ML_INFLIGHT"
  echo "EXPIRED req=$REQ_ID"
  exit 0
fi
case $WRC in
  0) rm -f "$ML_INFLIGHT"; echo "DELIVERED req=$REQ_ID (inflight cleared)"; exit 0 ;;
  3) echo "TAKEN BY FALLBACK OR MISMATCH req=$REQ_ID (the hook reports it)"; exit 0 ;;
  124) echo "WAITER BUDGET USED UP req=$REQ_ID (inflight kept; hook fallback still armed)" >&2; exit 124 ;;
  *) echo "WAITER ENDED WITHOUT DONE req=$REQ_ID (inflight kept; hook fallback still armed)" >&2; exit 1 ;;
esac
