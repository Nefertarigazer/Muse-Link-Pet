#!/usr/bin/env bash
# seq.sh - run a composite batch of sem/uia steps inside the PC host in ONE
# round trip (host kind "seq").
# usage: seq.sh <recipe.json> [timeout_s]
#   recipe: {"job": "name", "steps": [ <step>, ... ]}
#   a step is a sem request ({verb, app, query, action, nth, expect, surface,
#   ...}), a uia request ({"kind":"uia","op":...,"app":...,"query":{...}}),
#   {"verb":"sleep","ms":N}, or {"verb":"act_if", ..., "unless": <query>}
#   (act only when the unless query is absent). Any step may carry
#   "alternatives": [step, ...] tried in order while the result is a failure.
#   Every step that puts input on the desktop (act, act_if, uia focus/invoke)
#   waits for the user to be idle first.
# Exit: 0 batch ok, 4 unknown, 1 otherwise (including "expired": the host
# dropped the batch before running it because its deadline had passed).
set -u
DIR="$(cd "$(dirname "$0")" && pwd)"
F="${1:-}"
T="${2:-150}"
if [ -z "$F" ] || [ ! -f "$F" ]; then
  echo "usage: seq.sh <recipe.json> [timeout_s]" >&2
  exit 3
fi
JSON="$(python3 - "$F" "$T" <<'PYEOF'
import json, sys
with open(sys.argv[1], encoding="utf-8") as f:
    recipe = json.load(f)
if isinstance(recipe, list):
    recipe = {"steps": recipe}
req = {"kind": "seq", "job": recipe.get("job", "seq"),
       "timeout": float(sys.argv[2]), "steps": recipe.get("steps", [])}
print(json.dumps(req, ensure_ascii=False))
PYEOF
)"
RESP="$("$DIR/executor.sh" raw "$JSON")"
printf '%s' "$RESP" | python3 "$DIR/lib/inner-status.py" ok
