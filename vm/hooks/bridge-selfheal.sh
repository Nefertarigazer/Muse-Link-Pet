#!/usr/bin/env bash
# bridge-selfheal.sh - standalone 60-second detector for the muse-bridge
# systemd service. Production does NOT register this: the postman
# (bridge-postman.sh) runs the same heal on every tick, as the production
# postman always has. Register this only on installs without the postman.
# Silent unless healing fails. Needs root (writes /etc/systemd).
set -u
MUSELINK_VM_DIR="${MUSELINK_VM_DIR:-$(cd "$(dirname "$0")/.." && pwd)}"
. "$MUSELINK_VM_DIR/lib/muselink-common.sh" || exit 0
ML_HOOK_NAME="bridge-selfheal"
. "$MUSELINK_VM_DIR/hooks/hook-lib.sh"
. "$MUSELINK_VM_DIR/hooks/selfheal-lib.sh"
ml_hook_heartbeat

ml_selfheal_bridge; rc=$?
case "$rc" in
  0) ml_hook_silent "service_active" ;;
  2) ml_hook_silent "selfheal_skipped" "$(ml_json why "dry run or not root")" ;;
  3) ml_hook_silent "selfheal_rate_limited" ;;
esac
ml_hook_wake "selfheal_failed" "$(ml_json log "$ML_STATE_DIR/bridge-selfheal.log")"
