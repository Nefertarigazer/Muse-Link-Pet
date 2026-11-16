#!/usr/bin/env bash
# exp.sh - Muse's entry to the experience library on the PC.
#   exp.sh lookup <app> [goal]
#   exp.sh record <app> <goal> <method-file> [verify-text]
#   exp.sh confirm <id>          (a draft from record becomes valid)
#   exp.sh feedback <id> worked|failed
# Requirements (same pattern as sem.sh):
#  1. Build the JSON arguments locally (python3), write them to a temp file,
#     ml_scp_to it into <tool dir>\explib-args-<random>.json, then run
#     python -m explib <command> --agent muse --args-file <that file>
#     through ml_ps; delete the remote file afterwards. Card or user text is
#     never spliced into a command line.
#  2. Print the CLI's JSON line; exit with its exit code.
set -u
DIR="$(cd "$(dirname "$0")" && pwd)"
. "$DIR/lib/muselink-common.sh" || exit 2
VERB="${1:-}"
case "$VERB" in
  lookup|record|confirm|feedback) shift ;;
  *) echo "usage: exp.sh lookup <app> [goal] | record <app> <goal> <method-file> [verify] | confirm <id> | feedback <id> worked|failed" >&2; exit 2 ;;
esac
ARGS="$(mktemp)" || exit 1
trap 'rm -f "$ARGS"' EXIT
python3 - "$VERB" "$@" > "$ARGS" <<'PYEOF'
import json, sys
verb, args = sys.argv[1], sys.argv[2:]
if verb == "lookup" and 1 <= len(args) <= 2:
    req = {"app": args[0], "goal": args[1] if len(args) > 1 else ""}
elif verb == "record" and 3 <= len(args) <= 4:
    with open(args[2], encoding="utf-8-sig") as f:
        method = json.load(f)
    req = {"app": args[0], "goal": args[1], "method": method, "from_trace": False,
           "verify": args[3] if len(args) == 4 else "Verify the stated goal before confirming this draft."}
elif verb == "confirm" and len(args) == 1:
    req = {"id": args[0]}
elif verb == "feedback" and len(args) == 2 and args[1] in ("worked", "failed"):
    req = {"id": args[0], "worked": args[1] == "worked"}
else:
    raise SystemExit("invalid exp.sh arguments")
print(json.dumps(req, ensure_ascii=False))
PYEOF
rc=$?
[ "$rc" -eq 0 ] || exit "$rc"
NAME="explib-args-$(python3 -c 'import secrets; print(secrets.token_hex(12))').json"
REMOTE="$(ml_pc_path "$NAME")"
ml_scp_to "$ARGS" "$(ml_pc_fwd "$NAME")" || exit $?
# Only configuration paths and the validated command enter PowerShell code.
# Goal/method/user text travels exclusively in the UTF-8 argument file.
ml_ps "\$env:PYTHONUTF8='1'; Set-Location -LiteralPath $(ml_psq "$PC_TOOL_DIR"); try { & $(ml_psq "$PC_PYTHON") -m explib $VERB --agent muse --tool-dir $(ml_psq "$PC_TOOL_DIR") --args-file $(ml_psq "$REMOTE"); \$expRc=\$LASTEXITCODE } finally { Remove-Item -LiteralPath $(ml_psq "$REMOTE") -Force -ErrorAction SilentlyContinue }; exit \$expRc"
exit $?
