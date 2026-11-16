"""capture - draft an entry from the plugin trace.

Source: <tool dir>\\plugin\\trace\\<agent>.jsonl, written by plugin/trace.py
(tool, app, structural query, status; no typed text, no readback). Muse's
VM-side work does not pass the plugin, so Muse records with an explicit
method instead (cli record --method-file).

Requirements
  1. plugin/trace.py gains an "at" field (UTC ISO) on every row; that is
     the only plugin change capture needs. Rows without it are still read.
  2. draft_from_trace(agent, app, goal, since=None): take the most recent
     contiguous run of rows for `app` (optionally only rows at/after
     `since`). Drop a failed/unknown attempt when a later row repeats the
     same tool on the same app (it was replaced); merge consecutive
     observe rows into one; the final verify/uia_read_text rows become the
     entry's `verify` text, the rest become `method` steps with tool+args.
  3. Result is a schema-valid entry with status "draft", source "local",
     agent = caller; nothing is written here (cli/tools call store.put).
  4. Nothing in a trace row is copied that is not in the row: no guessing
     of text the agent typed.
"""


from datetime import date, datetime, timezone
import json

from plugin._shared import agent_id, tool_dir
from . import schema


def read_trace(base, agent):
    path = tool_dir(base) / "plugin" / "trace" / (agent_id(agent) + ".jsonl")
    try:
        lines = path.read_text(encoding="utf-8").splitlines()[-50:]
    except FileNotFoundError:
        return []
    rows = []
    for line in lines:
        try:
            row = json.loads(line)
            if isinstance(row, dict):
                rows.append(row)
        except ValueError:
            continue
    return rows


def _stamp(value):
    at = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if at.tzinfo is None:
        raise ValueError("since: timezone required")
    return at.astimezone(timezone.utc)


def draft_from_trace(base, agent, app, goal, since=None, today=None):
    app = schema.normalize_app(app)
    rows = read_trace(base, agent)
    # exp_* calls have no desktop effect and must not break the previous run.
    rows = [r for r in rows if not str(r.get("tool", "")).startswith("exp_")]
    run = []
    for row in reversed(rows):
        apps = row.get("app", [])
        if isinstance(apps, str):
            apps = [apps]
        try:
            same = app in [schema.normalize_app(a) for a in apps]
        except ValueError:
            same = False
        if not same:
            if run:
                break
            continue
        run.append(row)
    run.reverse()
    if since:
        start = _stamp(since)
        run = [r for r in run if r.get("at") and _stamp(r["at"]) >= start]
    kept = []
    for i, row in enumerate(run):
        tool = row.get("tool", "")
        if row.get("status") != "ok":
            if any(r.get("tool") == tool for r in run[i + 1:]):
                continue
            raise ValueError("trace: unresolved failed/unknown attempt")
        if tool == "observe" and kept and kept[-1].get("tool") == "observe":
            kept[-1] = row
        else:
            kept.append(row)
    verification = []
    while kept and kept[-1].get("tool") in ("verify", "uia_read_text"):
        verification.insert(0, kept.pop())
    if not verification or not kept:
        raise ValueError("trace: successful method and final verification required")

    def step(row):
        tool = row["tool"]
        query = {k: v for k, v in row.get("query", {}).items()
                 if k in ("name", "exact", "role", "controlType", "classContains")}
        args = {"app": app}
        if query:
            args["query"] = query
        label = tool + (" " + str(query["name"]) if query.get("name") else "")
        return dict(step=label, tool=tool, args=args)

    today = today or date.today().isoformat()
    entry = dict(id=schema.new_id(), schema=1, app=app, goal=goal,
                 method=[step(r) for r in kept], verify="; ".join(step(r)["step"] for r in verification),
                 status="draft", created=today, last_verified=today, works=0, fails=0,
                 source="local", agent=agent, tags=[])
    return schema.validate(entry)
