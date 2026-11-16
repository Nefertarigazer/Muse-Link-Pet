#!/usr/bin/env bash
# bridge-postman.sh - 60-second hook detector for the local-agent mailbox.
# Drop-in for the production hook "codex-bridge-watch": same wake contract
# (docs/protocol.en.md section 7), same definition prompt. Zero tokens while idle.
#
# Every tick:
#   1. self-heal the muse-bridge service (selfheal-lib.sh; fail-silent);
#   2. walk the PC inbox oldest first. Requests that need no worker are
#      handled in the same tick and do not use up the wake: rejected ones
#      (moved to rejected\, error reply per the verdict), duplicates,
#      enrollments (to pending\, the resident service shows the card) and
#      release requests while the resident service runs;
#   3. the first request that needs a worker: move it to claimed\ and wake
#      "codex_message" with the production payload (req_id, local_path, type,
#      created_at, text <= 400 chars, payload) plus additive identity fields
#      (agent_id, auth, decision, reason, verdict_path). One request per wake.
# Alerts are NOT this hook's business (bridge-alert-watch.sh).
# Differences from the production script, all deliberate: any valid req_id
# is accepted (production only took codex_*, which would block the queue
# behind the first kiro_* request), it stands down for publish-release
# (production knew only the retired name publish-voicebridge, which now maps
# to publish-release), a bad file no longer blocks the files behind it.
set -u
MUSELINK_VM_DIR="${MUSELINK_VM_DIR:-$(cd "$(dirname "$0")/.." && pwd)}"
. "$MUSELINK_VM_DIR/lib/muselink-common.sh" || exit 0
ML_HOOK_NAME="bridge-postman"
. "$MUSELINK_VM_DIR/hooks/hook-lib.sh"
. "$MUSELINK_VM_DIR/hooks/selfheal-lib.sh"
ml_hook_heartbeat

ml_selfheal_bridge >/dev/null 2>&1 || true

INBOX="$ML_BRIDGE_HOME/inbox"
mkdir -p "$INBOX" "$ML_BRIDGE_HOME/processed" "$ML_BRIDGE_HOME/rejected"
PC_INBOX="$PC_BRIDGE_DIR\\inbox"
IDSH="$MUSELINK_VM_DIR/muselink-id.sh"

LIST="$(ml_ps "Get-ChildItem -LiteralPath $(ml_psq "$PC_INBOX") -Filter *.json -File -ErrorAction SilentlyContinue | Sort-Object LastWriteTime | ForEach-Object { \$_.Name }; 'LIST_OK'" 2>/dev/null | tr -d '\r')"
if ! printf '%s\n' "$LIST" | grep -qx 'LIST_OK'; then
  ml_hook_log "PC inbox unreachable"
  ml_hook_silent "pc_unreachable"
fi
NAMES="$(printf '%s\n' "$LIST" | grep -vx 'LIST_OK' | grep -v '^$')"
[ -n "$NAMES" ] || ml_hook_silent "no_requests"

service_up=0
systemctl is-active --quiet muse-bridge 2>/dev/null && service_up=1
TMPD="$(mktemp -d)"; trap 'rm -rf "$TMPD"' EXIT

move_pc() {   # move_pc <name> <claimed|pending|rejected>
  ml_ps "\$d = Join-Path $(ml_psq "$PC_BRIDGE_DIR") '$2'; New-Item -ItemType Directory -Force \$d | Out-Null; Move-Item -LiteralPath (Join-Path $(ml_psq "$PC_INBOX") '$1') -Destination (Join-Path \$d '$1') -Force" >/dev/null 2>&1 \
    || ml_hook_log "move to $2 failed for $1"
}

# route_fields <route json> -> one inert value per line, "-" for empty
route_fields() {
  printf '%s' "$1" | python3 -c 'import json, re, sys
try:
    d = json.loads(sys.stdin.read().strip().splitlines()[-1])
except Exception:
    d = {}
for k in ("route", "code", "type", "decision", "reason", "reply_hint", "agent_id", "auth"):
    v = re.sub(r"[^A-Za-z0-9_.:+-]", "", str(d.get(k) or ""))[:80]
    print(v or "-")' 2>/dev/null | tr -d '\r'
}

LAST_REASON="" LAST_ID="" HANDLED=0
while IFS= read -r name; do
  id="${name%.json}"
  if ! ml_valid_id "$id"; then
    ml_hook_event "bad_name" "$(ml_json name "$name")"
    LAST_REASON="bad_name"; continue
  fi
  if [ "$ML_HOOK_DRY" = "1" ]; then
    ml_hook_event "dry_found" "$(ml_json req_id "$id")"
    ml_hook_silent "dry_run_found" "$(ml_json req_id "$id")"
  fi
  if ! ml_scp_from "$ML_PC_BRIDGE_FWD/inbox/$name" "$TMPD/$name" >/dev/null 2>&1; then
    ml_hook_event "fetch_failed" "$(ml_json req_id "$id")"
    ml_hook_silent "fetch_failed" "$(ml_json req_id "$id")"
  fi
  ROUTE="inbox" CODE="" TYPE="" DECISION="" REASON="" REPLY_HINT="" AGENT="" AUTH=""
  if [ -f "$IDSH" ]; then
    ROUTE_JSON="$(bash "$IDSH" route "$TMPD/$name" "$id" 2>>"$ML_STATE_DIR/bridge-postman.log")"; rc=$?
    if [ "$rc" -eq 30 ] || [ -z "$ROUTE_JSON" ]; then
      ml_hook_event "route_failed" "$(ml_json req_id "$id" rc "$rc")"   # PC file stays; retried next tick
      LAST_REASON="route_failed" LAST_ID="$id"; continue
    fi
    { read -r ROUTE; read -r CODE; read -r TYPE; read -r DECISION; read -r REASON
      read -r REPLY_HINT; read -r AGENT; read -r AUTH; } <<< "$(route_fields "$ROUTE_JSON")"
    for v in ROUTE CODE TYPE DECISION REASON REPLY_HINT AGENT AUTH; do
      [ "${!v:-}" = "-" ] && printf -v "$v" '%s' ""
    done
    case "$ROUTE" in
      inbox|rejected|duplicate|pending) ;;
      *)  # unreadable route output must never fall through to a wake
        ml_hook_event "route_failed" "$(ml_json req_id "$id" rc "unparsable")"
        LAST_REASON="route_failed" LAST_ID="$id"; continue ;;
    esac
  else
    # no identity module: the production checks (JSON object, req_id == name)
    if python3 -c 'import json, sys
d = json.load(open(sys.argv[1], encoding="utf-8-sig"))
sys.exit(0 if isinstance(d, dict) and d.get("req_id") == sys.argv[2] else 1)' "$TMPD/$name" "$id" 2>/dev/null; then
      cp "$TMPD/$name" "$INBOX/$name"; ml_fix_owner "$INBOX/$name"
    else
      ROUTE="rejected" CODE="unparsable"
      cp "$TMPD/$name" "$ML_BRIDGE_HOME/rejected/$name"; ml_fix_owner "$ML_BRIDGE_HOME/rejected/$name"
    fi
    TYPE="$(python3 -c 'import json, re, sys
try:
    t = re.sub(r"[^a-z0-9_-]", "", str(json.load(open(sys.argv[1], encoding="utf-8-sig")).get("type", "")))[:40]
except Exception:
    t = ""
try:   # retired type names -> current (same table as inbound routing)
    sys.path.insert(0, sys.argv[2])
    from muselink_id import current_type
    t = current_type(t)
except Exception:
    pass
print(t)' "$TMPD/$name" "$MUSELINK_VM_DIR" 2>/dev/null | tr -d '\r')"
  fi
  case "$ROUTE" in
    rejected)
      move_pc "$name" rejected
      if [ "$REPLY_HINT" = "signed_error" ] || [ "$REPLY_HINT" = "unsigned_error" ]; then
        printf 'request rejected: %s\n' "${CODE:-rejected}" > "$TMPD/reply-$id.txt"
        bash "$MUSELINK_VM_DIR/bridge/muse-bridge-reply.sh" "$id" error "$TMPD/reply-$id.txt" --final \
          >>"$ML_STATE_DIR/bridge-postman.log" 2>&1 || ml_hook_log "error reply failed for $id"
      fi
      ml_hook_event "rejected" "$(ml_json req_id "$id" code "$CODE")"
      LAST_REASON="rejected" LAST_ID="$id" HANDLED=$((HANDLED + 1)); continue ;;
    duplicate)
      move_pc "$name" claimed
      LAST_REASON="duplicate" LAST_ID="$id" HANDLED=$((HANDLED + 1)); continue ;;
    pending)
      move_pc "$name" pending     # muse-call -Check: STATE:PENDING_ENROLL
      LAST_REASON="pending_enroll" LAST_ID="$id" HANDLED=$((HANDLED + 1)); continue ;;
  esac
  move_pc "$name" claimed
  case "$TYPE" in
    publish-release)   # TYPE is already the current name (retired ones mapped above)
      if [ "$service_up" = "1" ]; then
        ml_hook_event "pipeline_owned" "$(ml_json req_id "$id")"
        LAST_REASON="pipeline_owned" LAST_ID="$id" HANDLED=$((HANDLED + 1)); continue
      fi ;;
  esac
  PAYLOAD="$(REQ_ID="$id" LOCAL_PATH="$INBOX/$name" AGENT="$AGENT" AUTH="$AUTH" DECISION="$DECISION" \
    REASON="$REASON" VERDICT="$ML_BRIDGE_HOME/verdicts/$id.json" HAVE_ID="$([ -f "$IDSH" ] && echo 1 || echo 0)" \
    python3 - <<'PYEOF'
import json, os
p = {"req_id": os.environ["REQ_ID"], "local_path": os.environ["LOCAL_PATH"]}
try:
    with open(os.environ["LOCAL_PATH"], encoding="utf-8-sig") as f:
        req = json.load(f)
    p["type"] = req.get("type", "")
    p["created_at"] = req.get("created_at", "")
    p["text"] = (req.get("text") or "")[:400]
    p["payload"] = req.get("payload") or {}
except Exception as e:
    p["parse_error"] = str(e)
if os.environ["HAVE_ID"] == "1":   # additive fields; the production prompt ignores them
    for k, env in (("agent_id", "AGENT"), ("auth", "AUTH"), ("decision", "DECISION"), ("reason", "REASON")):
        p[k] = os.environ[env]
    p["verdict_path"] = os.environ["VERDICT"]
print(json.dumps(p, ensure_ascii=False))
PYEOF
)"
  ml_hook_wake "codex_message" "$PAYLOAD"
done <<< "$NAMES"

[ -n "$LAST_REASON" ] || LAST_REASON="no_requests"
ml_hook_silent "$LAST_REASON" "$(ml_json req_id "$LAST_ID" handled "$HANDLED")"
