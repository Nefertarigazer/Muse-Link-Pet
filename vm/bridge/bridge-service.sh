#!/usr/bin/env bash
# bridge-service.sh - systemd entry point for bridge-service.py. Loads the
# MuseLink config (lib/muselink-common.sh) and hands the resolved values to
# the Python service through the environment.
set -u
DIR="$(cd "$(dirname "$0")/.." && pwd)"
. "$DIR/lib/muselink-common.sh" || exit 2
export ML_VM_DIR ML_VM_HOME ML_BRIDGE_HOME ML_STATE_DIR PC_BRIDGE_DIR PC_TOOL_DIR
exec python3 "$DIR/bridge/bridge-service.py"
