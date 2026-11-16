#!/usr/bin/env bash
# popup-fallback.sh - 60-second hook detector backing the popup / wait main
# path. Drop-in for the production hook "popup-wake-fallback": same wake
# contract (docs/protocol.en.md section 7), same definition prompt. Zero tokens idle.
#
#   no marker                 -> silent no_inflight
#   bad request id in marker  -> drop marker, silent bad_inflight
#   armed < 90 s ago          -> silent grace_period (main path just armed)
#   past EXPIRES_AT           -> -ForceExpire on the PC; wake popup_expired
#                                {req_id, mode, summary}; an answer recovered
#                                at that moment wakes popup_recovered instead;
#                                PC unreachable -> silent probe_failed, retry
#   STATE:DONE                -> drop marker, wake popup_recovered
#                                {req_id, mode, summary, kind, done_at, result}
#   NO_PENDING                -> silent already_consumed; the marker is kept
#                                for 3 rounds so check-popup.ps1 can recover an
#                                answer the main path claimed but never
#                                delivered (needs the done file >= 60 s old,
#                                2026-11-05 post-mortem), then dropped
#   MISMATCH / ALREADY_CLAIMED -> drop marker, silent already_consumed
#   STILL_OPEN / RELAUNCHED / DEAD / STILL_WAITING -> silent popup_pending
#   anything else             -> silent probe_failed
# Dry run: -ProbeOnly, no marker changes, no PC call at expiry.
# Send-side rule: before sending a popup, check that
# $ML_STATE_DIR/popup-hook-heartbeat is younger than 5 minutes.
set -u
MUSELINK_VM_DIR="${MUSELINK_VM_DIR:-$(cd "$(dirname "$0")/.." && pwd)}"
. "$MUSELINK_VM_DIR/lib/muselink-common.sh" || exit 0
ML_HOOK_NAME="popup-hook"
. "$MUSELINK_VM_DIR/hooks/hook-lib.sh"

ml_hook_heartbeat
[ -f "$ML_INFLIGHT" ] || ml_hook_silent "no_inflight"

REQ_ID="$(ml_inflight_get REQ_ID)"
WAIT_TYPE="$(ml_inflight_get WAIT_TYPE)"; case "$WAIT_TYPE" in popup|task) ;; *) WAIT_TYPE=popup ;; esac
CREATED_AT="$(ml_inflight_get CREATED_AT)"; [[ "$CREATED_AT" =~ ^[0-9]+$ ]] || CREATED_AT=0
EXPIRES_AT="$(ml_inflight_get EXPIRES_AT)"; [[ "$EXPIRES_AT" =~ ^[0-9]+$ ]] || EXPIRES_AT=$(( CREATED_AT + 43200 ))
MODE="$(ml_safe_text "$(ml_inflight_get MODE)")"
SUMMARY="$(ml_safe_text "$(ml_inflight_get SUMMARY)")"
QUIET_FILE="$ML_STATE_DIR/popup-hook-nopending"
drop_marker() { [ "$ML_HOOK_DRY" = "1" ] || rm -f "$ML_INFLIGHT" "$QUIET_FILE"; }

if ! ml_valid_id "$REQ_ID"; then
  # the production hook kept such a marker, which then blocked every new
  # popup (popup-send refuses while a marker exists); drop it, stay silent
  ml_hook_event "bad_inflight"
  drop_marker
  ml_hook_silent "bad_inflight"
fi
NOW=$(date +%s)
[ $(( NOW - CREATED_AT )) -lt 90 ] && ml_hook_silent "grace_period" "$(ml_json req_id "$REQ_ID")"
# A card owned by a program (popup-send.sh --owner, e.g. the enrollment card
# of bridge-service) is waited for by that program, across its restarts: its
# answer must not be claimed and handed to Muse. Only a marker past its own
# short expiry is cleaned up, silently.
OWNER="$(ml_inflight_get OWNER)"
if [ -n "$OWNER" ]; then
  if [ "$NOW" -ge "$EXPIRES_AT" ] && [ "$ML_HOOK_DRY" != "1" ]; then
    ml_ps_file check-popup.ps1 -ReqId "$REQ_ID" -WaitType "$WAIT_TYPE" -ForceExpire >/dev/null 2>&1
    drop_marker
    ml_hook_silent "owned_expired" "$(ml_json req_id "$REQ_ID" owner "$OWNER")"
  fi
  ml_hook_silent "owned" "$(ml_json req_id "$REQ_ID" owner "$OWNER")"
fi

recovered() {   # recovered <check-popup output> -> wake popup_recovered
  local payload
  payload="$(OUT="$1" REQ_ID="$REQ_ID" MODE="$MODE" SUMMARY="$SUMMARY" python3 -c '
import json, os
kind = done = ""
res, collecting = [], False
for ln in os.environ.get("OUT", "").splitlines():
    if collecting:
        res.append(ln)
    elif ln.startswith("KIND:"):
        kind = ln[5:].strip()
    elif ln.startswith("DONE_AT:"):
        done = ln[8:].strip()
    elif ln.strip() == "---RESULT---":
        collecting = True
print(json.dumps({"req_id": os.environ["REQ_ID"], "mode": os.environ["MODE"],
                  "summary": os.environ["SUMMARY"], "kind": kind, "done_at": done,
                  "result": "\n".join(res).strip()}, ensure_ascii=False))')"
  drop_marker
  ml_hook_wake "popup_recovered" "$payload"
}
expired() {
  drop_marker
  ml_hook_wake "popup_expired" "$(ml_json req_id "$REQ_ID" mode "$MODE" summary "$SUMMARY")"
}

if [ "$NOW" -ge "$EXPIRES_AT" ]; then
  [ "$ML_HOOK_DRY" = "1" ] && expired          # production: no PC call in a dry run
  OUT="$(ml_ps_file check-popup.ps1 -ReqId "$REQ_ID" -WaitType "$WAIT_TYPE" -ForceExpire 2>&1 | tr -d '\r')"
  case "$(printf '%s\n' "$OUT" | head -n 1)" in
    STATE:DONE) recovered "$OUT" ;;
    STATE:EXPIRED_CLEANED|STATE:NO_PENDING|STATE:MISMATCH*|STATE:ALREADY_CLAIMED) expired ;;
    *) ml_hook_event "probe_failed" "$(ml_json req_id "$REQ_ID" at "expiry")"
       ml_hook_silent "probe_failed" "$(ml_json req_id "$REQ_ID")" ;;
  esac
fi

FLAGS=(-ReqId "$REQ_ID" -WaitType "$WAIT_TYPE")
[ "$ML_HOOK_DRY" = "1" ] && FLAGS+=(-ProbeOnly)
OUT="$(ml_ps_file check-popup.ps1 "${FLAGS[@]}" 2>&1 | tr -d '\r')"
FIRST="$(printf '%s\n' "$OUT" | head -n 1)"
case "$FIRST" in
  STATE:DONE) recovered "$OUT" ;;
  STATE:NO_PENDING)
    n=$(( $(cat "$QUIET_FILE" 2>/dev/null || echo 0) + 1 ))
    if [ "$ML_HOOK_DRY" != "1" ]; then
      if [ "$n" -ge 3 ]; then rm -f "$ML_INFLIGHT" "$QUIET_FILE"
      else echo "$n" > "$QUIET_FILE"; ml_fix_owner "$QUIET_FILE"; fi
    fi
    ml_hook_event "already_consumed" "$(ml_json req_id "$REQ_ID" probe "$FIRST" round "$n")"
    ml_hook_silent "already_consumed" "$(ml_json req_id "$REQ_ID")" ;;
  STATE:MISMATCH*|STATE:ALREADY_CLAIMED)
    drop_marker
    ml_hook_event "already_consumed" "$(ml_json req_id "$REQ_ID" probe "$FIRST")"
    ml_hook_silent "already_consumed" "$(ml_json req_id "$REQ_ID")" ;;
  STATE:STILL_OPEN|STATE:RELAUNCHED|STATE:DEAD|STATE:STILL_WAITING)
    [ "$ML_HOOK_DRY" = "1" ] || rm -f "$QUIET_FILE"
    ml_hook_event "popup_pending" "$(ml_json req_id "$REQ_ID" probe "$FIRST")"
    ml_hook_silent "popup_pending" "$(ml_json req_id "$REQ_ID" probe "$FIRST")" ;;
  *)
    ml_hook_event "probe_failed" "$(ml_json req_id "$REQ_ID")"
    ml_hook_silent "probe_failed" "$(ml_json req_id "$REQ_ID")" ;;
esac
