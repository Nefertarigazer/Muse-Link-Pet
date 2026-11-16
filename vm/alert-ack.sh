#!/usr/bin/env bash
# alert-ack.sh - confirm that a bridge alert has been reported to the user.
#   alert-ack.sh <alert_id> [<alert_id> ...]
#   alert-ack.sh --list        alerts not confirmed yet, with their wake count
# bridge-alert-watch.sh wakes the worker for an alert until it is confirmed
# here (hooks/bridge-alert-watch.sh: every 600 s, at most 3 wakes). The alert
# file itself is never changed. Output: one JSON line; exit 0 when every id
# was confirmed (or already was), 2 for an id that names no alert.
set -u
DIR="$(cd "$(dirname "$0")" && pwd)"
. "$DIR/lib/muselink-common.sh" || exit 2
[ $# -ge 1 ] || { echo "usage: alert-ack.sh <alert_id> [...] | --list" >&2; exit 2; }
mkdir -p "$ML_STATE_DIR"
exec 7>"$ML_STATE_DIR/bridge-alert-watch.lock"
flock -w 20 7 || { echo '{"ok": false, "error": "lock_busy"}'; exit 1; }
ALERTS="$ML_BRIDGE_HOME/alerts" SEEN="$ML_STATE_DIR/bridge-alert-watch.seen" \
PENDING="$ML_STATE_DIR/bridge-alert-watch.pending.json" python3 - "$@" <<'PYEOF'
import json, os, re, sys
d, seen_path, pending_path = os.environ["ALERTS"], os.environ["SEEN"], os.environ["PENDING"]
args = sys.argv[1:]
names = sorted(n for n in (os.listdir(d) if os.path.isdir(d) else []) if n.endswith(".txt"))
def write(path, text):
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        f.write(text)
    os.replace(tmp, path)
if not os.path.exists(seen_path):
    # same baseline as the hook's first run, so history does not wake later
    write(seen_path, "".join(n + "\n" for n in names))
with open(seen_path, encoding="utf-8") as f:
    seen = [line.strip() for line in f if line.strip()]
try:
    with open(pending_path, encoding="utf-8") as f:
        pending = json.load(f)
    if not isinstance(pending, dict):
        pending = {}
except (OSError, ValueError):
    pending = {}
if args == ["--list"]:
    out = [{"alert_id": n[:-4], "wakes": int((pending.get(n) or {}).get("wakes") or 0),
            "last_wake": (pending.get(n) or {}).get("last")} for n in names if n not in seen]
    print(json.dumps({"ok": True, "unconfirmed": out}, ensure_ascii=False))
    raise SystemExit(0)
acked, already, unknown = [], [], []
for a in args:
    n = a + ".txt"
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]{0,120}", a) or n not in names:
        unknown.append(a)
    elif n in seen:
        already.append(a)
    else:
        acked.append(a)
        seen.append(n)
        pending.pop(n, None)
if acked:
    with open(seen_path, "a", encoding="utf-8") as f:
        f.writelines(a + ".txt\n" for a in acked)
    write(pending_path, json.dumps(pending, ensure_ascii=False, sort_keys=True))
print(json.dumps({"ok": not unknown, "acked": acked, "already": already, "unknown": unknown}, ensure_ascii=False))
raise SystemExit(2 if unknown else 0)
PYEOF
rc=$?
for f in "$ML_STATE_DIR/bridge-alert-watch.seen" "$ML_STATE_DIR/bridge-alert-watch.pending.json" \
         "$ML_STATE_DIR/bridge-alert-watch.lock"; do
  [ -f "$f" ] && ml_fix_owner "$f"
done
exit "$rc"
