#!/usr/bin/env bash
# Compatibility shim for the pre-2026-11-12 name. Use pc.sh.
# MUSELINK_VM_DIR points at the deployed vm/ directory when this shim is
# installed somewhere else (e.g. directly in ~/workspace/tools).
exec "${MUSELINK_VM_DIR:-$(cd "$(dirname "$0")/.." && pwd)}/pc.sh" "$@"
