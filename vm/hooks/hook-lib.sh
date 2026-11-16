#!/usr/bin/env bash
# hook-lib.sh - the decision interface shared by the MuseLink hook detectors.
#
# Contract v2 (2026-11-12, aligned with the production hooks; see docs/protocol.en.md section 7):
# a detector ends with exactly ONE terminal decision, each carrying a reason
# string and a JSON object:
#   ml_hook_silent <reason> [json]    stay silent (zero tokens)
#   ml_hook_wake   <reason> <json>    wake the worker; the hook definition's
#                                     prompt reads the JSON fields
#   ml_hook_event  <event>  [json]    platform log line, not terminal
#   ml_hook_log    <text>             local log file, not terminal
#
# Runtime: when the platform provides $HATCH_HOOK_RUNTIME (it defines
# silent / wake / log, as used by the production hooks), the calls map onto
# it. Without it (manual runs, tests) they print
#   "SILENT: <reason> <json>" / "WAKE: <reason> <json>"
# and exit 0. A site adapter ($MUSELINK_HOOK_ADAPTER, default
# ~/.config/muselink/hook-adapter.sh) is sourced last and may redefine the
# functions, but only if it declares ML_HOOK_ADAPTER_CONTRACT=2: an adapter
# written for the old one-string contract would drop every payload field.
#
# Dry runs: MUSELINK_HOOK_DRY_RUN=1 or the platform's HATCH_HOOK_DRY_RUN=1.
# A dry run probes and reports the decision it WOULD take, but writes no
# state, claims, moves, relaunches, expires or deletes nothing.

ML_HOOK_DRY="${MUSELINK_HOOK_DRY_RUN:-${HATCH_HOOK_DRY_RUN:-0}}"

_ml_hook_runtime=0
if [ -n "${HATCH_HOOK_RUNTIME:-}" ] && [ -f "$HATCH_HOOK_RUNTIME" ]; then
  # shellcheck disable=SC1090
  . "$HATCH_HOOK_RUNTIME" && _ml_hook_runtime=1
fi

if [ "$_ml_hook_runtime" = "1" ]; then
  ml_hook_silent() { silent "$1" "${2:-{\}}"; exit 0; }
  ml_hook_wake()   { wake "$1" "${2:-{\}}"; exit 0; }
  ml_hook_event()  { log "$1" "${2:-{\}}" 2>/dev/null || true; }
else
  ml_hook_silent() { printf 'SILENT: %s %s\n' "$1" "${2:-{\}}"; exit 0; }
  ml_hook_wake()   { printf 'WAKE: %s %s\n' "$1" "${2:-{\}}"; exit 0; }
  ml_hook_event()  { printf 'EVENT: %s %s\n' "$1" "${2:-{\}}" >&2; }
fi
unset _ml_hook_runtime

_ml_adapter="${MUSELINK_HOOK_ADAPTER:-${HOME:-/root}/.config/muselink/hook-adapter.sh}"
if [ -f "$_ml_adapter" ]; then
  if grep -q '^ML_HOOK_ADAPTER_CONTRACT=2' "$_ml_adapter" 2>/dev/null; then
    # shellcheck disable=SC1090
    . "$_ml_adapter"
  else
    printf '%s hook-lib: ignoring %s (not declared ML_HOOK_ADAPTER_CONTRACT=2)\n' \
      "$(date -Iseconds)" "$_ml_adapter" >> "${ML_STATE_DIR:-/tmp}/hook-adapter.log" 2>/dev/null || true
  fi
fi
unset _ml_adapter

ml_hook_log() {
  mkdir -p "$ML_STATE_DIR" 2>/dev/null || return 0
  printf '%s %s\n' "$(date -Iseconds)" "$*" >> "$ML_STATE_DIR/${ML_HOOK_NAME:-hook}.log"
}

ml_hook_heartbeat() {
  [ "$ML_HOOK_DRY" = "1" ] && return 0
  mkdir -p "$ML_STATE_DIR"
  date +%s > "$ML_STATE_DIR/${ML_HOOK_NAME:-hook}-heartbeat"
  ml_fix_owner "$ML_STATE_DIR/${ML_HOOK_NAME:-hook}-heartbeat"
}

# ml_json k1 v1 [k2 v2 ...] -> a JSON object of string values (safe for any
# text: values travel through the environment, never through the source).
ml_json() {
  local i=0 args=()
  while [ $# -ge 2 ]; do args+=("ML_J_K$i=$1" "ML_J_V$i=$2"); shift 2; i=$((i + 1)); done
  env "${args[@]}" ML_J_N="$i" python3 -c 'import json, os
n = int(os.environ["ML_J_N"])
print(json.dumps({os.environ["ML_J_K%d" % i]: os.environ["ML_J_V%d" % i] for i in range(n)}, ensure_ascii=False))'
}
