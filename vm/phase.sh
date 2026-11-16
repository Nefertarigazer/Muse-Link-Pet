#!/usr/bin/env bash
# phase.sh - tell the desktop pet what Muse is doing now, in a few words.
#   phase.sh "<short phrase>"      e.g. phase.sh "正在整理周报"
# The resident host appends one "phase" event to executor-events.jsonl
# (under its own write lock and rotation); the pet shows the newest phrase in
# its bubble while the PC is working (job events in the last 45 s), clipped
# to 60 characters. The phrase is shown on screen: no secrets, no user text.
# Exit: as executor.sh (0 ok, 1 refused, 2 host unreachable, 3 usage).
set -u
DIR="$(cd "$(dirname "$0")" && pwd)"
[ $# -eq 1 ] && [ -n "$1" ] || { echo 'usage: phase.sh "<short phrase>"' >&2; exit 3; }
JSON="$(python3 -c 'import json, sys; print(json.dumps({"kind": "phase", "text": sys.argv[1], "timeout": 10}, ensure_ascii=False))' "$1")" || exit 3
exec "$DIR/executor.sh" raw "$JSON"
