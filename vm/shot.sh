#!/usr/bin/env bash
# shot.sh - the ONLY screenshot entry point for the PC.
# Backed by muse-shot.ps1 in the PC tool dir (run through executor kind
# "script"; no general shell access needed).
#   shot.sh window <App name>   one app's window (restores a minimized window
#                               for the capture, then minimizes it again)
#   shot.sh screen              the full primary display
# Rule baked into muse-shot.ps1: PNG <= 400 KB is sent as-is; larger shots
# become JPEG (quality 80, down to 50 if still too big) with the same pixel
# size. The native PNG stays on the PC at <tool dir>\shots\last-<mode>.png.
# Prints the JSON metadata, then the local saved image path on the last line.
set -u
DIR="$(cd "$(dirname "$0")" && pwd)"
. "$DIR/lib/muselink-common.sh" || exit 2
MODE="${1:-}"
[ $# -ge 1 ] && shift
APP="$*"
case "$MODE" in
  window) [ -n "$APP" ] || { echo "usage: shot.sh window <App name>" >&2; exit 3; } ;;
  screen) ;;
  *) echo "usage: shot.sh window <App name> | shot.sh screen" >&2; exit 3 ;;
esac
OUTDIR="$ML_VM_HOME/workspace/shots"
mkdir -p "$OUTDIR"

call() {
  if [ "$MODE" = "window" ]; then
    "$DIR/executor.sh" script --job shot muse-shot.ps1 -Mode window -App "$APP"
  else
    "$DIR/executor.sh" script --job shot muse-shot.ps1 -Mode screen
  fi
}

RESP="$(call)"
RESTORED=0
if printf '%s' "$RESP" | grep -qi "minimized" && [ "$MODE" = "window" ]; then
  # restore/minimize are input actions: the host serializes them and lets
  # the user finish typing first
  "$DIR/executor.sh" ad restore --app "$APP" >/dev/null 2>&1
  RESTORED=1
  RESP="$(call)"
fi
if [ "$RESTORED" = "1" ]; then
  "$DIR/executor.sh" ad minimize --app "$APP" >/dev/null 2>&1
fi

TMP="$(mktemp)"
trap 'rm -f "$TMP"' EXIT
printf '%s' "$RESP" > "$TMP"
python3 - "$TMP" "$OUTDIR" "$MODE" <<'PYEOF'
import sys, json, base64, datetime, os
raw = open(sys.argv[1], encoding="utf-8", errors="replace").read()
outdir, mode = sys.argv[2], sys.argv[3]
try:
    env = json.loads(raw)
    out = env.get("out", "")
except Exception:
    out = raw
lines = [ln.strip() for ln in out.splitlines() if ln.strip()]
meta = None
b64 = ""
for ln in lines:
    if ln.startswith("{") and '"fmt"' in ln:
        try:
            meta = json.loads(ln)
        except Exception:
            pass
    elif len(ln) > len(b64):
        b64 = ln
if not meta or meta.get("fmt") == "error" or not b64:
    print(json.dumps({"ok": False, "meta": meta, "raw_tail": out[-300:]}, ensure_ascii=False))
    sys.exit(1)
data = base64.b64decode(b64)
ext = "jpg" if meta["fmt"] == "jpeg" else "png"
stamp = datetime.datetime.now().strftime("%Y%m%d-%H%M%S")
path = os.path.join(outdir, f"{stamp}-{mode}.{ext}")
with open(path, "wb") as f:
    f.write(data)
meta["saved"] = path
meta["saved_bytes"] = len(data)
print(json.dumps(meta, ensure_ascii=False))
print(path)
PYEOF
