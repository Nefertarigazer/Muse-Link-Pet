"""tools - the three plugin tools (registered into plugin.tools).

Requirements
  1. exp_lookup(app, goal?, ui?, tags?) -> {"status": "ok", "hits": [...]}
     from store.lookup(). Tier "read".
  2. exp_record(goal, app, from_trace? (default true), since?, ui?, verify?,
     method?, tags?) -> a draft (capture or explicit method) and its id;
     exp_record(confirm=<id>, edits?) turns that draft valid. Tier "local".
  3. exp_feedback(id, worked: bool) -> updated status/works/fails.
     Tier "local".
  4. Tier "local" is new in plugin/tools.py + plugin/gate.py: registered
     agents only, no consent card, never touches the executor. Unregistered
     callers get "denied" like other non-read tools.
  5. Registration happens on import of plugin.tools (plugin/tools.py ends
     with a guarded `import explib.tools`), so MCP and CLI both list them.
     Audit/trace rows as for every tool; goal/method text is not audited.
"""


import copy
from datetime import date

from . import capture, schema
from .store import Store


def handle(base, agent, name, args):
    store = Store(base)
    today = date.today().isoformat()
    if name == "exp_lookup":
        return {"status": "ok", "verb": name, "hits": store.lookup(**args)}
    if name == "exp_feedback":
        entry = store.feedback(args["id"], args["worked"], today)
    elif args.get("confirm"):
        def edit(entry):
            if entry.get("agent") != agent:
                raise ValueError("agent: draft belongs to another agent")
            edits = args.get("edits", {})
            if not isinstance(edits, dict) or edits.keys() - {"goal", "ui", "app_version", "method", "verify", "tags"}:
                raise ValueError("edits: only method/content fields may change")
            entry.update(copy.deepcopy(edits))
            return schema.confirm(entry, today)
        if args.keys() - {"confirm", "edits"}:
            raise ValueError("confirm: unexpected record arguments")
        entry = store.update(args["confirm"], edit)
    else:
        if "app" not in args or "goal" not in args or "edits" in args:
            raise ValueError("record: app and goal required")
        app = schema.normalize_app(args["app"])
        if args.get("from_trace", True):
            if "method" in args:
                raise ValueError("method: use from_trace=false")
            entry = capture.draft_from_trace(base, agent, app, args["goal"], args.get("since"), today)
        else:
            entry = dict(id=schema.new_id(), schema=1, app=app, goal=args["goal"],
                         method=copy.deepcopy(args.get("method", [])), verify=args.get("verify", ""),
                         status="draft", created=today, last_verified=today, works=0, fails=0,
                         source="local", agent=agent, tags=[])
        for key in ("ui", "app_version", "verify", "tags"):
            if key in args:
                entry[key] = copy.deepcopy(args[key])
        entry = store.put(entry)
    return {"status": "ok", "verb": name, "id": entry["id"], "entry": entry}


def register_all():
    from plugin import tools as registry
    text = {"type": "string"}
    tags = {"type": "array", "items": {"type": "string", "minLength": 1, "maxLength": 24}, "maxItems": 8}
    lookup = registry.obj(dict(app=registry.APP, goal=text, ui=text, tags=tags), ("app",))
    record = registry.obj(dict(app=registry.APP, goal=text, from_trace={"type": "boolean"}, since=text,
                               ui=text, app_version=text, verify=text, tags=tags,
                               method={"type": "array", "minItems": 1, "maxItems": 12},
                               confirm=text, edits={"type": "object"}))
    feedback = registry.obj(dict(id=text, worked={"type": "boolean"}), ("id", "worked"))
    for name, tier, args, description in (
            ("exp_lookup", "read", lookup, "Look up at most three methods; verify before use."),
            ("exp_record", "local", record, "Draft one verified new experience, or confirm an edited draft."),
            ("exp_feedback", "local", feedback, "Report whether an experience worked; never executes its method.")):
        if name not in registry.TOOLS:
            registry.register(registry.Tool(name, tier, args, handle, description))
