#!/usr/bin/env bash
# muselink-id.sh - run the identity CLI (vm/muselink_id/cli.py) with the
# MuseLink config loaded.
#   muselink-id.sh <subcommand> ...      (see vm/muselink_id/cli.py)
#
# Requirements
#   * load lib/muselink-common.sh (PC_TOOL_DIR, PC_BRIDGE_DIR, ML_BRIDGE_HOME,
#     ssh helpers) and export what the Python side needs: ML_VM_DIR,
#     ML_BRIDGE_HOME, PC_TOOL_DIR, PC_BRIDGE_DIR, MUSELINK_ID_HOME (default
#     $ML_VM_HOME/.config/muselink/identity), MUSELINK_POLICY (default
#     $ML_VM_HOME/.config/muselink/policy.json);
#   * umask 077 before anything runs (new state files must be private);
#   * PYTHONPATH="$ML_VM_DIR" and exec python3 -m muselink_id.cli "$@";
#   * pass the exit code through unchanged (codes are part of the interface).
set -u
MUSELINK_VM_DIR="${MUSELINK_VM_DIR:-$(cd "$(dirname "$0")" && pwd)}"
. "$MUSELINK_VM_DIR/lib/muselink-common.sh" || exit 30
umask 077
export ML_VM_DIR ML_BRIDGE_HOME PC_TOOL_DIR PC_BRIDGE_DIR
export MUSELINK_ID_HOME="${MUSELINK_ID_HOME:-$ML_VM_HOME/.config/muselink/identity}"
export MUSELINK_POLICY="${MUSELINK_POLICY:-$ML_VM_HOME/.config/muselink/policy.json}"
export PYTHONPATH="$ML_VM_DIR${PYTHONPATH:+:$PYTHONPATH}"
exec python3 -m muselink_id.cli "$@"
