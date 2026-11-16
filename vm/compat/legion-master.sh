#!/usr/bin/env bash
# Compatibility shim for the pre-2026-11-12 name. Use pc-master.sh.
exec "${MUSELINK_VM_DIR:-$(cd "$(dirname "$0")/.." && pwd)}/pc-master.sh" "$@"
