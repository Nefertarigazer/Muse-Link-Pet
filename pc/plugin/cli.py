"""CLI adapter for the exact same gated tools as MCP."""
import argparse
import json
import sys

from . import tools
from ._shared import agent_id

EXIT_CODES = {"ok": 0, "fail": 1, "unknown": 4, "busy": 5, "denied": 10}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("tool")
    parser.add_argument("--agent", required=True, type=agent_id)
    parser.add_argument("--tool-dir")
    parser.add_argument("--args-file")
    args = parser.parse_args()
    sys.stdout.reconfigure(encoding="utf-8")
    try:
        if args.args_file:
            with open(args.args_file, encoding="utf-8-sig") as f:
                arguments = json.load(f)
        else:
            raw = sys.stdin.buffer.read().decode("utf-8-sig")
            arguments = json.loads(raw) if raw.strip() else {}
        result = tools.invoke(args.tool, arguments, args.agent, args.tool_dir)
    except (ValueError, OSError):
        result = {"status": "fail", "verb": args.tool, "error": "BAD_ARGUMENTS_FILE_OR_JSON"}
    print(json.dumps(result, ensure_ascii=False))
    return EXIT_CODES.get(result["status"], 1)


if __name__ == "__main__":
    sys.exit(main())
