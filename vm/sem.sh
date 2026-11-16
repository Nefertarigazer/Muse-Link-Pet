#!/usr/bin/env bash
# sem.sh - semantic verbs executed inside the PC's resident host. The host
# resolves/acts/verifies locally and returns only a small digested verdict.
# usage:
#   sem.sh observe <app> [skeleton|digest|full]
#   sem.sh resolve <app> <name> [--exact]
#   sem.sh verify  <app> <name> present|absent [--exact]
#   sem.sh act     <app> <name> [action] [--exact] [--nth N] [--key K]
#                  [--expect-name <n>] [--expect-should present|absent]
#                  [--expect-text <t>]
#   shared options: [--surface <menu|sheet|focused|...>] [--role <role>]
#                   [--path-contains <s>] [--path-excludes <s>]
#                   [--find-timeout-ms <1000..50000>]  (host default 30000)
# Exit: 0 status ok/resolved, 4 status unknown (may have taken effect: look
# before retrying), 1 anything else.
set -u
DIR="$(cd "$(dirname "$0")" && pwd)"
VERB="${1:-}"; APP="${2:-}"
if [ -z "$VERB" ] || [ -z "$APP" ]; then
  echo "usage: sem.sh observe|resolve|verify|act <app> ..." >&2
  exit 3
fi
shift 2
JSON="$(python3 - "$VERB" "$APP" "$@" <<'PYEOF'
import json, sys
verb, app, args = sys.argv[1], sys.argv[2], list(sys.argv[3:])
req = {"kind": "sem", "verb": verb, "app": app, "job": "sem-" + verb, "timeout": 60}

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
expect_name = take("--expect-name")
expect_should = take("--expect-should") or "present"
expect_text = take("--expect-text")
nth = take("--nth")
key = take("--key")
surface = take("--surface")
role = take("--role")
path_contains = take("--path-contains")
path_excludes = take("--path-excludes")
find_timeout = take("--find-timeout-ms")
if find_timeout is not None:
    if not find_timeout.isdigit() or not 1000 <= int(find_timeout) <= 50000:
        raise SystemExit("--find-timeout-ms: 1000..50000")
    req["find_timeout_ms"] = int(find_timeout)
if surface:
    req["surface"] = surface
if verb == "observe":
    req["mode"] = args[0] if args else "skeleton"
else:
    q = {"name": args[0] if args else "", "exact": exact}
    if role:
        q["role"] = role
    if path_contains:
        q["path_contains"] = path_contains
    if path_excludes:
        q["path_excludes"] = path_excludes
    req["query"] = q
if verb == "verify":
    req["should"] = args[1] if len(args) > 1 else "present"
if verb == "act":
    if len(args) > 1 and not args[1].startswith("--"):
        req["action"] = args[1]
    if key:
        req["key"] = key
    expect = {}
    if expect_name:
        expect["query"] = {"name": expect_name, "exact": exact}
        expect["should"] = expect_should
    if expect_text:
        expect["text"] = expect_text
    if expect:
        req["expect"] = expect
    if nth is not None:
        req["nth"] = int(nth)
print(json.dumps(req, ensure_ascii=False))
PYEOF
)"
RESP="$("$DIR/executor.sh" raw "$JSON")"
printf '%s' "$RESP" | python3 "$DIR/lib/inner-status.py" ok resolved
