#!/usr/bin/env bash
# test-vm-lib.sh - unit checks for vm/lib/muselink-common.sh helpers (pure
# bash, no network). Run with bash on Linux or Git Bash on Windows.
set -u
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
T="$(mktemp -d)"; trap 'rm -rf "$T"' EXIT
mkdir -p "$T/.config/muselink"
cat > "$T/.config/muselink/muselink.env" <<'EOF'
PC_HOST=100.64.0.9
PC_USER=winuser
PC_TOOL_DIR='C:\Users\winuser\muselink'
EOF
export MUSELINK_HOME="$T"
# shellcheck disable=SC1091
. "$ROOT/vm/lib/muselink-common.sh" || { echo "FAIL: lib did not load"; exit 1; }
fail=0
check() { if [ "$2" = "$3" ]; then echo "PASS $1"; else echo "FAIL $1: got [$2] want [$3]"; fail=$((fail+1)); fi; }

check "home"            "$ML_VM_HOME" "$T"
check "fwd path"        "$ML_PC_FWD" "C:/Users/winuser/muselink"
check "bridge dir"      "$PC_BRIDGE_DIR" 'C:\Users\winuser\muselink\muse-bridge'
check "pc path"         "$(ml_pc_path wait-popup.ps1)" 'C:\Users\winuser\muselink\wait-popup.ps1'
check "psq plain"       "$(ml_psq 'abc')" "'abc'"
check "psq quote"       "$(ml_psq "it's")" "'it''s'"
check "psq injection"   "$(ml_psq "x'; calc; '")" "'x''; calc; '''"
ml_valid_id "codex_20261112_101500_ab12cd" && r=ok || r=bad; check "id ok" "$r" ok
ml_valid_id "x'; calc" && r=ok || r=bad;                       check "id quote" "$r" bad
ml_valid_id "../etc" && r=ok || r=bad;                         check "id traversal" "$r" bad
ml_valid_id "a" && r=ok || r=bad;                              check "id too short" "$r" bad
check "safe text"       "$(ml_safe_text 'a$(rm -rf ~) `b` "c"; d|e')" 'a(rm -rf ~) b c de'
# Use a separate shell: an if/|| around the function would suppress errexit.
bash -e -s -- "$ROOT" "$T" >"$T/fix-owner.out" 2>"$T/fix-owner.err" <<'EOF'
. "$1/vm/lib/muselink-common.sh"
regression_dir="$2"
id() { if [ "$1" = "-u" ]; then echo 0; else command id "$@"; fi; }
chown() {
  printf '%s\n' "$@" > "$regression_dir/chown.args"
  echo 'chown: Operation not permitted' >&2
  return 1
}
ml_fix_owner "$regression_dir/owner target" "$regression_dir/second target"
printf 'status=%s\n' "$?"
echo continued
EOF
r=$?
check "fix owner errexit exit" "$r" 0
check "fix owner status and continuation" "$(cat "$T/fix-owner.out")" $'status=0\ncontinued'
check "fix owner chown arguments" "$(cat "$T/chown.args" 2>/dev/null)" \
  "$(printf '%s\n' "--reference=$ML_VM_HOME" "$T/owner target" "$T/second target")"
check "fix owner silent failure" "$(cat "$T/fix-owner.err")" ""
ml_inflight_write "REQ_ID=req_1_2" "SUMMARY=hello world" "MODE=info"
check "inflight get"    "$(ml_inflight_get SUMMARY)" "hello world"
check "inflight mode"   "$(ml_inflight_get MODE)" "info"
check "inflight absent" "$(ml_inflight_get NOPE)" ""
case " ${ML_SSH_OPTS[*]} " in *"StrictHostKeyChecking=yes"*) r=ok ;; *) r=bad ;; esac; check "host key pinned" "$r" ok
case " ${ML_SSH_OPTS[*]} " in *"ControlMaster=auto"*) r=ok ;; *) r=bad ;; esac;     check "mux on" "$r" ok
case " ${ML_SSH_BASE[*]} " in *"ControlMaster"*) r=bad ;; *) r=ok ;; esac;          check "base has no mux" "$r" ok
[ "$fail" -eq 0 ] && echo "ALL PASS" || { echo "FAILED $fail"; exit 1; }
