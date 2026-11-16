"""Bounded, body-free per-agent history for the future experience library."""
import json
from datetime import datetime, timezone

from ._shared import agent_id, atomic, mutex, tool_dir


def record(base, agent, tool, app, query, status):
    base = tool_dir(base)
    path = base / "plugin" / "trace" / (agent_id(agent) + ".jsonl")
    # Only structural selectors; never expect.text, typed text or readback.
    safe = {k: query[k] for k in ("name", "exact", "role", "controlType", "classContains") if k in query}
    row = dict(at=datetime.now(timezone.utc).isoformat(), tool=tool, app=app, query=safe, status=status)
    with mutex(base, "plugin-trace"):
        lines = path.read_text(encoding="utf-8").splitlines() if path.exists() else []
        atomic(path, "\n".join((lines + [json.dumps(row, ensure_ascii=False)])[-50:]) + "\n")
