#!/usr/bin/env bash
# selfheal-lib.sh - keep the muse-bridge systemd service alive across VM
# replacement (which wipes /etc). Sourced by bridge-postman.sh, which runs it
# on every tick before looking at the inbox - exactly where the production
# postman (codex-bridge-watch) has had it since 2026-11-10 - and by the
# standalone bridge-selfheal.sh for installs that register it separately.
#
# ml_selfheal_bridge -> 0 active or healed, 1 heal failed, 2 skipped (dry run
# or not root), 3 rate limited / another heal running. Never exits the caller.
# Rules (production behaviour kept): a missing unit file (the VM-replacement
# signature) is healed at once; otherwise at most one attempt per 120 s; one
# healer at a time (flock); every attempt is logged to bridge-selfheal.log.
# The unit is rendered from the template that ships with the code
# (bridge/muse-bridge.service) instead of a hand-kept master copy.

ml_selfheal_bridge() {
  systemctl is-active --quiet muse-bridge 2>/dev/null && return 0
  [ "${ML_HOOK_DRY:-0}" = "1" ] && return 2
  [ "$(id -u)" = "0" ] || return 2
  local unit=/etc/systemd/system/muse-bridge.service
  local stamp="$ML_STATE_DIR/bridge-selfheal.last" log="$ML_STATE_DIR/bridge-selfheal.log"
  local now last user rc=1
  mkdir -p "$ML_STATE_DIR"
  now=$(date +%s); last=$(cat "$stamp" 2>/dev/null || echo 0)
  if [ -f "$unit" ] && [ $(( now - last )) -lt 120 ]; then return 3; fi
  exec 8>"$ML_STATE_DIR/bridge-selfheal.lock"
  flock -n 8 || { exec 8>&-; return 3; }
  echo "$now" > "$stamp"
  user="$(stat -c %U "$ML_VM_HOME")"
  # the home and config this hook resolved (lib/muselink-common.sh) are the
  # ones the service must read
  if bash "$MUSELINK_VM_DIR/bridge/render-unit.sh" "$user" "$MUSELINK_VM_DIR" "$ML_VM_HOME" "$ML_CONFIG" \
       > "$unit.tmp" 2>>"$log" && mv -f "$unit.tmp" "$unit"; then
    chmod +x "$MUSELINK_VM_DIR/bridge/bridge-service.sh" 2>/dev/null
    systemctl daemon-reload >/dev/null 2>&1
    systemctl enable muse-bridge >/dev/null 2>&1
    systemctl reset-failed muse-bridge >/dev/null 2>&1
    systemctl start muse-bridge >/dev/null 2>&1
    sleep 2
    if systemctl is-active --quiet muse-bridge; then
      printf '%s healed: unit rendered and started (user %s)\n' "$(date -Iseconds)" "$user" >> "$log"
      rc=0
    fi
  fi
  [ "$rc" = "0" ] || printf '%s heal FAILED: %s\n' "$(date -Iseconds)" \
    "$(systemctl status muse-bridge --no-pager 2>&1 | tail -3 | tr '\n' ' ')" >> "$log"
  exec 8>&-
  return "$rc"
}
