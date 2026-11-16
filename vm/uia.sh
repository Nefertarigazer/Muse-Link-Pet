#!/usr/bin/env bash
# uia.sh - minimal native-UIA adapter verbs, executed inside the PC host's
# desktop session. Elements are re-located from semantic properties (name /
# control type / class) on every call; RuntimeId is only an intra-call
# identity check, never a stored address.
# usage:
#   uia.sh locate    <app> <name> [--exact] [--type <ControlType>] [--class <substr>]
#   uia.sh focus     <app> <name> [--exact] [--type <ControlType>] [--class <substr>]
#   uia.sh invoke    <app> <name> [--exact] [--type <ControlType>] [--class <substr>]
#   uia.sh read-text <app> <name> [--exact] [--type <ControlType>] [--class <substr>]
#   uia.sh focused   <app>
# Exit: 0 ok/resolved, 4 unknown, 1 otherwise.
set -u
DIR="$(cd "$(dirname "$0")" && pwd)"
VERB="${1:-}"; APP="${2:-}"
if [ -z "$VERB" ] || [ -z "$APP" ]; then
  echo "usage: uia.sh locate|focus|invoke|read-text|focused <app> [name] [options]" >&2
  exit 3
fi
shift 2
case "$VERB" in
  read-text) OP="read_text" ;;
  *) OP="$VERB" ;;
esac
JSON="$(python3 - "$OP" "$APP" "$@" <<'PYEOF'
import json, sys
op, app, args = sys.argv[1], sys.argv[2], list(sys.argv[3:])
req = {"kind": "uia", "op": op, "app": app, "job": "uia-" + op, "timeout": 25}

def take(flag):
    global args
    if flag in args:
        i = args.index(flag)
        v = args[i + 1] if i + 1 < len(args) else ""
        args = args[:i] + args[i + 2:]
        return v
    return None

exact = False
if "--exact" in args:
    args.remove("--exact")
    exact = True
ctype = take("--type")
cclass = take("--class")
q = {"exact": exact}
if args:
    q["name"] = args[0]
if ctype:
    q["controlType"] = ctype
if cclass:
    q["classContains"] = cclass
req["query"] = q
print(json.dumps(req, ensure_ascii=False))
PYEOF
)"
RESP="$("$DIR/executor.sh" raw "$JSON")"
printf '%s' "$RESP" | python3 "$DIR/lib/inner-status.py" ok resolved
