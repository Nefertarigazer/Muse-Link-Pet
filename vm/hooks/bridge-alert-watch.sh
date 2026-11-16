#!/usr/bin/env bash
# bridge-alert-watch.sh - 60-second hook detector for $ML_BRIDGE_HOME/alerts/
# (impersonation, tampered registry or grant, replay conflict, stalled
# request, a reply that could not be sent). Definition:
# vm/hooks/definitions/bridge-alert-watch.json (delivery: main chat).
#
# Delivery is confirmed: an alert counts as done only after the worker ran
# alert-ack.sh <alert_id> (the wake prompt says so, after the message to the
# user). The hook cannot see whether a wake reached anyone, so:
#   first run (no seen-list)  -> record every existing alert as done, silent
#                                "baseline": history never wakes anyone
#   an alert never woken      -> wake bridge_alert for the OLDEST such alert
#                                (one alert per wake; the rest follow on the
#                                next ticks)
#   woken, not confirmed      -> woken again every RETRY_S (600 s), up to
#                                MAX_WAKES wakes in all (3); after the last
#                                one has waited RETRY_S it is recorded as done
#                                and "gave_up" is logged (the file stays, and
#                                muselink-id.sh doctor still counts it)
#   nothing due               -> silent no_new_alerts / awaiting_ack
# Payload: {alert_id, path, text <= 600 chars, written_at, more, attempt,
# max_attempts, ack}. State in $ML_STATE_DIR: bridge-alert-watch.seen (done
# names, one per line) and bridge-alert-watch.pending.json ({name: {wakes,
# last}}), both under bridge-alert-watch.lock, shared with alert-ack.sh.
# Alerts are tracked by file name, not by mtime (a marker's mtime misses
# alerts written in the same second). Dry run: reads only, writes nothing.
# Alert files are never edited or deleted here.
# MUSELINK_ALERT_RETRY_S / MUSELINK_ALERT_MAX_WAKES override the defaults.
set -u
MUSELINK_VM_DIR="${MUSELINK_VM_DIR:-$(cd "$(dirname "$0")/.." && pwd)}"
. "$MUSELINK_VM_DIR/lib/muselink-common.sh" || exit 0
ML_HOOK_NAME="bridge-alert-watch"
. "$MUSELINK_VM_DIR/hooks/hook-lib.sh"
ml_hook_heartbeat
DIR="$ML_BRIDGE_HOME/alerts"
SEEN="$ML_STATE_DIR/bridge-alert-watch.seen"
PENDING="$ML_STATE_DIR/bridge-alert-watch.pending.json"
if [ ! -d "$DIR" ]; then
  # an empty baseline: alerts that appear later are new, not history
  if [ "$ML_HOOK_DRY" != "1" ] && [ ! -f "$SEEN" ]; then mkdir -p "$ML_STATE_DIR"; : > "$SEEN"; ml_fix_owner "$SEEN"; fi
  ml_hook_silent "no_new_alerts"
fi
if [ "$ML_HOOK_DRY" != "1" ]; then
  mkdir -p "$ML_STATE_DIR"
  exec 7>"$ML_STATE_DIR/bridge-alert-watch.lock"
  flock -w 20 7 || ml_hook_silent "lock_busy"
fi
DECISION="$(ALERTS="$DIR" SEEN="$SEEN" PENDING="$PENDING" DRY="$ML_HOOK_DRY" ACK="$MUSELINK_VM_DIR/alert-ack.sh" \
  RETRY_S="${MUSELINK_ALERT_RETRY_S:-600}" MAX_WAKES="${MUSELINK_ALERT_MAX_WAKES:-3}" python3 - <<'PYEOF'
import json, os, time
d, seen_path, pending_path = os.environ["ALERTS"], os.environ["SEEN"], os.environ["PENDING"]
dry = os.environ["DRY"] == "1"
retry, max_wakes = int(os.environ["RETRY_S"]), max(1, int(os.environ["MAX_WAKES"]))
now = int(time.time())
names = []
for n in os.listdir(d):
    p = os.path.join(d, n)
    if n.endswith(".txt") and os.path.isfile(p):
        names.append((os.path.getmtime(p), n))
names.sort()
def write(path, text):
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        f.write(text)
    os.replace(tmp, path)
if not os.path.exists(seen_path):
    if not dry:
        os.makedirs(os.path.dirname(seen_path), exist_ok=True)
        write(seen_path, "".join(n + "\n" for _m, n in names))
    print(json.dumps({"decision": "silent", "reason": "baseline", "count": len(names)}))
    raise SystemExit
with open(seen_path, encoding="utf-8") as f:
    seen = {line.strip() for line in f if line.strip()}
try:
    with open(pending_path, encoding="utf-8") as f:
        pending = json.load(f)
    if not isinstance(pending, dict):
        pending = {}
except (OSError, ValueError):
    pending = {}
present = {n for _m, n in names}
pending = {n: v for n, v in pending.items() if n in present and n not in seen and isinstance(v, dict)}
unseen = [(m, n) for m, n in names if n not in seen]
def waited(n):
    return now - int(pending[n].get("last") or 0) >= retry
gave_up = [n for _m, n in unseen if n in pending and int(pending[n].get("wakes") or 0) >= max_wakes and waited(n)]
open_ = [(m, n) for m, n in unseen if n not in gave_up]
# never woken first (oldest first), then resends (oldest first)
due = [x for x in open_ if x[1] not in pending] + [x for x in open_ if x[1] in pending and waited(x[1])]
def save():
    if dry:
        return
    for n in gave_up:
        pending.pop(n, None)
    if gave_up:
        with open(seen_path, "a", encoding="utf-8") as f:
            f.writelines(n + "\n" for n in gave_up)
    write(pending_path, json.dumps(pending, ensure_ascii=False, sort_keys=True))
if not due:
    save()
    reason = "awaiting_ack" if open_ else "no_new_alerts"
    print(json.dumps({"decision": "silent", "reason": reason, "gave_up": gave_up,
                      "payload": {"unconfirmed": len(open_)}}))
    raise SystemExit
m, n = due[0]
attempt = int(pending.get(n, {}).get("wakes") or 0) + 1
pending[n] = {"wakes": attempt, "last": now}
save()
p = os.path.join(d, n)
try:
    with open(p, encoding="utf-8", errors="replace") as f:
        text = f.read(600).strip()
except OSError:
    text = ""
print(json.dumps({"decision": "wake", "reason": "bridge_alert", "gave_up": gave_up, "payload": {
    "alert_id": n[:-4], "path": p, "text": text,
    "written_at": time.strftime("%Y-%m-%dT%H:%M:%S%z", time.localtime(m)),
    "more": len(open_) - 1, "attempt": attempt, "max_attempts": max_wakes,
    "ack": "bash " + os.environ["ACK"] + " " + n[:-4]}}, ensure_ascii=False))
PYEOF
)"
[ -n "$DECISION" ] || { ml_hook_log "alert scan failed"; ml_hook_silent "scan_failed"; }
if [ "$ML_HOOK_DRY" != "1" ]; then
  for f in "$SEEN" "$PENDING" "$ML_STATE_DIR/bridge-alert-watch.lock"; do [ -f "$f" ] && ml_fix_owner "$f"; done
fi
FIELDS="$(printf '%s' "$DECISION" | python3 -c 'import json, sys
d = json.load(sys.stdin)
print(d["decision"]); print(d["reason"]); print(" ".join(d.get("gave_up") or []))
print(json.dumps(d.get("payload", {k: v for k, v in d.items() if k == "count"}), ensure_ascii=False))' | tr -d '\r')"
{ read -r KIND; read -r REASON; read -r GAVE_UP; read -r PAYLOAD; } <<< "$FIELDS"
[ -n "$GAVE_UP" ] && [ "$ML_HOOK_DRY" != "1" ] && ml_hook_log "gave_up (no ack after the last wake): $GAVE_UP"
[ "$KIND" = "wake" ] && ml_hook_wake "$REASON" "$PAYLOAD"
ml_hook_silent "$REASON" "$PAYLOAD"
