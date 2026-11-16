"""cli - python -m explib <command> [--agent ID] [--args-file F]

Commands: lookup, record, confirm, feedback, drafts, share-check, sync,
import-recipes. Arguments as a JSON object from --args-file (UTF-8) or
stdin; one JSON line out. Exit: 0 ok, 1 fail, 2 usage, 5 busy, 10 refused.

Requirements
  1. Same functions as tools.py (no second implementation).
  2. share-check is what the daily scheduled task runs:
     install\\register-tasks.ps1 -IncludeExplib adds a Limited/Interactive
     task "explib-share" at 12:30 that runs it through launcher.exe.
  3. record from Muse (vm/exp.sh) passes the method explicitly; agent is
     "muse".
"""


import argparse
import json
from pathlib import Path
import sys

from plugin._shared import agent_id, tool_dir
from . import import_recipes, public, share
from .store import Store


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=("lookup", "record", "confirm", "feedback", "drafts", "share-check", "sync", "import-recipes"))
    parser.add_argument("--agent", default="codex", type=agent_id)
    parser.add_argument("--tool-dir")
    parser.add_argument("--args-file")
    parser.add_argument("--method-file")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args(argv)
    base = tool_dir(args.tool_dir)
    try:
        if args.args_file:
            data = json.loads(Path(args.args_file).read_text(encoding="utf-8-sig"))
        elif args.command in ("drafts", "share-check", "sync"):
            data = {}
        else:
            data = json.load(sys.stdin)
        if not isinstance(data, dict):
            raise ValueError("args: object required")
        if args.method_file:
            if args.command != "record":
                raise ValueError("method-file: only for record")
            data["method"] = json.loads(Path(args.method_file).read_text(encoding="utf-8-sig"))
            data["from_trace"] = False
        if args.command in ("lookup", "record", "confirm", "feedback"):
            from plugin import tools
            if args.command == "confirm":
                if "id" in data:
                    data["confirm"] = data.pop("id")
                if not data.get("confirm"):
                    raise ValueError("confirm: id required")
            name = "exp_" + ("record" if args.command == "confirm" else args.command)
            if args.agent == "muse":
                # Muse reaches this CLI over SSH (vm/exp.sh) and is never a
                # locally enrolled agent ("muse" is a reserved id), so the
                # plugin's registered-agent check would deny every record /
                # feedback (Kiro review). Same validation and handler, same
                # audit row; the trust boundary is the one GUIDE describes.
                from plugin import gate
                tool = tools.TOOLS[name]
                tools.validate(data, tool.schema)
                answer = tool.handler(base, "muse", name, data)
                gate.audit(base, "muse", name, [data.get("app", "")] if data.get("app") else [], "",
                           "allow", answer, 0)
            else:
                answer = tools.invoke(name, data, args.agent, base)
        elif args.command == "drafts":
            answer = dict(status="ok", drafts=Store(base).drafts(args.agent))
        elif args.command == "share-check":
            answer = share.check_once(base)
        elif args.command == "sync":
            answer = dict(status="ok", **public.sync(base))
        else:
            answer = dict(status="ok", **import_recipes.run(data["path"], base, args.dry_run or data.get("dry_run", False)))
    except (OSError, ValueError, TypeError, KeyError, RecursionError):
        answer = dict(status="fail", error="INVALID_ARGUMENTS_OR_LOCAL_IO")
    print(json.dumps(answer, ensure_ascii=False))
    return {"ok": 0, "outboxed": 0, "shared": 0, "busy": 5, "denied": 10, "refused": 10}.get(answer["status"], 1)


if __name__ == "__main__":
    raise SystemExit(main())
