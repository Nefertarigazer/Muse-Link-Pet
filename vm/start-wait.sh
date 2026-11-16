#!/usr/bin/env bash
# start-wait.sh - THE standard way to wait for a LOCAL TASK or WEB AI on the
# PC. Arms the same mailbox as popup-send.sh (inflight marker with
# WAIT_TYPE=task + EXPIRES_AT; popup-pending.txt on the PC), launches a
# first-party terminal trigger on the PC, then waits with the same short-wait
# loop. The hook fallback (check-popup.ps1 -WaitType task) claims results and
# wakes Muse at the deadline.
#
# The trigger runs from a generated starter (<tool dir>\watch-start.ps1,
# parameters as PowerShell literals, UTF-8 BOM, try/catch into
# watch-crash.log) started by the fixed scheduled task "muse-watch"
# (launcher.exe, interactive session) - never from a short-lived SSH session
# (WMI launches died with it, 2026-11-03).
#
#   --kind pid  --pid <N>                 kernel handle wait on a process
#   --kind file --file <win path>         output file: lock released + size stable
#   --kind cmd  --command-file <local>    wrap a PowerShell command (text file)
#   --kind web                            web-bridge receiver (userscript notifier)
# Options: [--name "..."] [--expires-min N] [--quiet N] [--wait|--no-wait]
#          [--wait-max SECONDS] [--force]
# Exit: 0 DONE, 124 wait budget used up (fallback still delivers), 1 failure.
set -u
. "$(dirname "$0")/lib/muselink-common.sh" || exit 2

KIND="" PID_ARG="" WFILE="" CMDFILE="" NAME="" EXPIRES_MIN="" WAIT=1 FORCE=0 QUIET=5 WAIT_MAX=""
while [ $# -gt 0 ]; do
  case "$1" in
    --kind) KIND="$2"; shift 2 ;;
    --pid) PID_ARG="$2"; shift 2 ;;
    --file) WFILE="$2"; shift 2 ;;
    --command-file) CMDFILE="$2"; shift 2 ;;
    --name) NAME="$2"; shift 2 ;;
    --expires-min) EXPIRES_MIN="$2"; shift 2 ;;
    --quiet) QUIET="$2"; shift 2 ;;
    --wait) WAIT=1; shift ;;
    --no-wait) WAIT=0; shift ;;
    --wait-max) WAIT_MAX="$2"; shift 2 ;;
    --force) FORCE=1; shift ;;
    *) echo "unknown arg: $1" >&2; exit 3 ;;
  esac
done
case "$KIND" in
  pid)  [[ "$PID_ARG" =~ ^[0-9]+$ ]] || { echo "--kind pid needs --pid <number>" >&2; exit 3; } ;;
  file) [ -n "$WFILE" ] || { echo "--kind file needs --file" >&2; exit 3; } ;;
  cmd)  [ -n "$CMDFILE" ] && [ -f "$CMDFILE" ] || { echo "--kind cmd needs --command-file <existing>" >&2; exit 3; } ;;
  web)  ;;
  *) echo "usage: --kind pid|file|cmd|web" >&2; exit 3 ;;
esac
[[ "$QUIET" =~ ^[0-9]+$ ]] || { echo "--quiet must be a number" >&2; exit 3; }
if [ -z "$EXPIRES_MIN" ]; then
  if [ "$KIND" = "web" ]; then EXPIRES_MIN=30; else EXPIRES_MIN=120; fi
fi
[[ "$EXPIRES_MIN" =~ ^[0-9]+$ ]] || { echo "--expires-min must be a number" >&2; exit 3; }
[ -n "$WAIT_MAX" ] || WAIT_MAX=$(( EXPIRES_MIN * 60 ))
[ -n "$NAME" ] || NAME="$KIND-wait"
NAME="$(ml_safe_text "$NAME")"
if [ -f "$ML_INFLIGHT" ] && [ "$FORCE" -ne 1 ]; then
  echo "REFUSED: an inflight wait already exists ($ML_INFLIGHT); use --force only if it is stale" >&2
  exit 1
fi

REQ_ID="req_$(date +%Y%m%d_%H%M%S)_${RANDOM}"
TMPD="$(mktemp -d)"
trap 'rm -rf "$TMPD"' EXIT
printf '%s' "$REQ_ID" > "$TMPD/pending.txt"
printf '%s' "$REQ_ID" > "$TMPD/reqid.txt"

WATCH_PS="$(ml_psq "$(ml_pc_path Watch-LocalTask.ps1)")"
BRIDGE_PS="$(ml_psq "$(ml_pc_path web-bridge.ps1)")"
CMD_PS="$(ml_psq "$(ml_pc_path watch-cmd.ps1)")"
CRASH_LOG="$(ml_psq "$(ml_pc_path watch-crash.log)")"
{
  printf '\xef\xbb\xbf'          # UTF-8 BOM: PS 5.1 must not read the literals as GBK
  printf 'try {\n  '
  case "$KIND" in
    pid)  printf '& %s -TargetPid %s -TaskName %s -ReqId %s\n' "$WATCH_PS" "$PID_ARG" "$(ml_psq "$NAME")" "$(ml_psq "$REQ_ID")" ;;
    file) printf '& %s -OutputFile %s -QuietSeconds %s -TaskName %s -ReqId %s\n' "$WATCH_PS" "$(ml_psq "$WFILE")" "$QUIET" "$(ml_psq "$NAME")" "$(ml_psq "$REQ_ID")" ;;
    cmd)  printf '& %s -CommandFile %s -TaskName %s -ReqId %s\n' "$WATCH_PS" "$CMD_PS" "$(ml_psq "$NAME")" "$(ml_psq "$REQ_ID")" ;;
    web)  printf '& %s -ReqId %s -MaxWaitSeconds %s\n' "$BRIDGE_PS" "$(ml_psq "$REQ_ID")" "$(( EXPIRES_MIN * 60 ))" ;;
  esac
  printf '} catch {\n  $_ | Out-File %s -Encoding utf8\n}\n' "$CRASH_LOG"
} > "$TMPD/watch-start.ps1"

# 1. clear stale mailbox outputs, then stage pending + reqid + starter (+ command)
ml_ps "foreach (\$n in 'popup-done.txt','popup-result.txt','popup-kind.txt') { Remove-Item -LiteralPath (Join-Path $(ml_psq "$PC_TOOL_DIR") \$n) -Force -ErrorAction SilentlyContinue }" >/dev/null 2>&1 || true
ml_scp_to "$TMPD/reqid.txt"       "$(ml_pc_fwd popup-reqid.txt)"   || { echo "scp reqid failed" >&2; exit 1; }
ml_scp_to "$TMPD/pending.txt"     "$(ml_pc_fwd popup-pending.txt)" || { echo "scp pending failed" >&2; exit 1; }
ml_scp_to "$TMPD/watch-start.ps1" "$(ml_pc_fwd watch-start.ps1)"   || { echo "scp starter failed" >&2; exit 1; }
if [ "$KIND" = "cmd" ]; then
  ml_scp_to "$CMDFILE" "$(ml_pc_fwd watch-cmd.ps1)" || { echo "scp cmd failed" >&2; exit 1; }
fi

# 2. inflight marker (epoch seconds; WAIT_TYPE=task)
NOW=$(date +%s)
ml_inflight_write "REQ_ID=$REQ_ID" "WAIT_TYPE=task" "CREATED_AT=$NOW" \
  "EXPIRES_AT=$(( NOW + EXPIRES_MIN * 60 ))" "MODE=$KIND" "SUMMARY=$NAME"

# 3. start the trigger through the fixed task, then wait
TOUT="$(ml_ssh "schtasks /run /tn muse-watch" </dev/null 2>&1 | tr -d '\r')"; TRC=$?
if [ $TRC -ne 0 ]; then
  echo "TRIGGER LAUNCH FAILED: $TOUT (inflight removed)" >&2
  rm -f "$ML_INFLIGHT"
  exit 1
fi
echo "TRIGGERED req=$REQ_ID kind=$KIND"
if [ "$WAIT" -ne 1 ]; then
  echo "ARMED req=$REQ_ID (no wait; hook fallback will deliver)"
  exit 0
fi

WOUT="$(ml_wait_loop "$REQ_ID" "$WAIT_MAX")"; WRC=$?
printf '%s\n' "$WOUT"
case $WRC in
  0) rm -f "$ML_INFLIGHT"; echo "DELIVERED req=$REQ_ID (inflight cleared)"; exit 0 ;;
  3) echo "TAKEN BY FALLBACK OR MISMATCH req=$REQ_ID"; exit 0 ;;
  124) echo "WAITER BUDGET USED UP req=$REQ_ID (inflight kept; hook fallback still armed)" >&2; exit 124 ;;
  *) echo "WAITER ENDED WITHOUT DONE req=$REQ_ID (inflight kept; hook fallback still armed)" >&2; exit 1 ;;
esac
