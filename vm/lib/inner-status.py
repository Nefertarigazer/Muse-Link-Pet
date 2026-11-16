#!/usr/bin/env python3
"""Print the host's inner result and map its status to an exit code.

    executor.sh raw ... | python3 inner-status.py <ok-status> [<ok-status> ...]

Exit 0 when status is one of the given ok statuses, 4 when it is "unknown"
(the action may have taken effect), 1 otherwise.
"""
import json
import sys

ok_statuses = set(sys.argv[1:]) or {"ok"}
raw = sys.stdin.read()
try:
    env = json.loads(raw)
    inner = env.get("out", "")
except Exception:
    print(raw.strip())
    sys.exit(4)
try:
    obj = json.loads(inner)
except Exception:
    print(inner)
    sys.exit(4 if env.get("status") == "unknown" else 1)
print(json.dumps(obj, ensure_ascii=False))
status = obj.get("status") if isinstance(obj, dict) else None
if status in ok_statuses:
    sys.exit(0)
sys.exit(4 if status == "unknown" else 1)
