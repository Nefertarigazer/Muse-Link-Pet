#!/usr/bin/env bash
# executor.sh - send one request to the resident executor host on the PC.
#   executor.sh ping | info
#   executor.sh ad     [--job N] [--timeout S] <agent-desktop args...>
#   executor.sh script [--job N] [--timeout S] [--input] <tool-dir script.ps1> [args...]
#   executor.sh shell  [--job N] [--timeout S] [--input] <argv...>   (needs executor.allow_shell)
#   executor.sh raw    <request JSON>
# Prints the host's JSON response line.
# Exit: 0 ok, 1 host-reported failure, 2 host unreachable after one heal,
#       3 usage, 4 outcome UNKNOWN (timed out somewhere: the PC may still be
#       executing it - check state before any retry).
set -u
. "$(dirname "$0")/lib/muselink-common.sh" || exit 2
"$ML_VM_DIR/pc-master.sh" ensure >/dev/null || { echo '{"ok":false,"status":"unknown","out":"no ssh master"}'; exit 2; }

KIND="${1:-}"
[ -n "$KIND" ] || { echo "usage: executor.sh ping|info|ad|script|shell|raw ..." >&2; exit 3; }
shift

if [ "$KIND" = "raw" ]; then
  JSON="${1:-}"
else
  JOB="" TIMEOUT="" INPUT=0
  while [ $# -gt 0 ]; do
    case "$1" in
      --job) JOB="${2:-}"; shift 2 ;;
      --timeout) TIMEOUT="${2:-}"; shift 2 ;;
      --input) INPUT=1; shift ;;
      *) break ;;
    esac
  done
  JSON="$(python3 - "$KIND" "$JOB" "$TIMEOUT" "$INPUT" "$@" <<'PYEOF'
import json, sys
kind, job, timeout, inp, rest = sys.argv[1], sys.argv[2], sys.argv[3], sys.argv[4], sys.argv[5:]
req = {"kind": kind, "job": job}
if timeout:
    req["timeout"] = float(timeout)
if inp == "1":
    req["input"] = True
if kind == "script":
    req["name"] = rest[0] if rest else ""
    req["args"] = rest[1:]
elif kind in ("ad", "shell"):
    req["argv"] = rest
print(json.dumps(req, ensure_ascii=False))
PYEOF
)"
fi

# Wall clock for the whole SSH call = the request's own budget + slack, so the
# VM never kills a call the PC host is still allowed to finish.
REQ_TIMEOUT="$(printf '%s' "$JSON" | python3 -c 'import json,sys
try: print(int(float(json.load(sys.stdin).get("timeout", 60))))
except Exception: print(60)')"
VM_TIMEOUT=$(( REQ_TIMEOUT + 40 ))

B64="$(printf '%s' "$JSON" | base64 -w0)"
CALL="$PC_PYTHON \"$(ml_pc_path executor-call.py)\""

run() {
  # cmd.exe caps a command line at 8191 chars: small requests ride in argv
  # (the proven path), large ones go through stdin.
  if [ "${#B64}" -le 7000 ]; then
    timeout "$VM_TIMEOUT" ssh "${ML_SSH_OPTS[@]}" "$ML_TARGET" "$CALL $B64" </dev/null
  else
    printf '%s' "$JSON" | timeout "$VM_TIMEOUT" ssh "${ML_SSH_OPTS[@]}" "$ML_TARGET" "$CALL -"
  fi
}

OUT="$(run)"; RC=$?
if [ $RC -eq 2 ]; then
  # host unreachable: restart it through its scheduled task, wait, retry once
  ml_ssh "schtasks /Run /TN executor-host" </dev/null >/dev/null 2>&1
  sleep 4
  OUT="$(run)"; RC=$?
fi
if [ $RC -eq 124 ]; then
  OUT='{"ok":false,"status":"unknown","out":"{\"status\":\"unknown\",\"error\":\"VM_TIMEOUT\"}"}'
  RC=4
elif [ $RC -ne 0 ] && [ $RC -ne 1 ] && [ $RC -ne 2 ] && [ $RC -ne 3 ] && [ $RC -ne 4 ]; then
  # ssh itself failed (255 etc.): we cannot know whether the request ran
  OUT="${OUT:-}"
  [ -n "$OUT" ] || OUT='{"ok":false,"status":"unknown","out":"{\"status\":\"unknown\",\"error\":\"TRANSPORT\"}"}'
  RC=4
fi
printf '%s\n' "$OUT"
exit $RC
