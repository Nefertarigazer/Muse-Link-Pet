#!/usr/bin/env bash
# bridge-reply-status.sh - answer "did the other side receive my replies?"
# for bridge requests, reply by reply.
#
# History: the 2026-11-09 handler checked only the outbox and reported two
# delivered replies as lost; the v1 version of this tool then checked the
# archive first and reported a request as consumed as soon as its ACK had been
# read, while the final reply sat unread (17 such requests on 2026-11-12).
# This version reports every reply (local ledger seq -> PC outbox / archive)
# and gives a verdict about the FINAL one.
#
# Usage: bridge-reply-status.sh <req_id> [req_id ...]
# Verdicts:
#   NOT_SENT                 no reply in the local ledger
#   FINAL_READ               a final reply is in the PC archive
#   FINAL_DELIVERED_UNREAD   a final reply waits in the PC outbox
#   INTERIM_ONLY             only interim replies were sent so far
#   FINAL_MISSING_ON_PC      the ledger has a final reply, the PC has neither copy
#   PC_PROBE_FAILED          could not ask the PC: conclude nothing
set -uo pipefail
DIR="$(cd "$(dirname "$0")/.." && pwd)"
. "$DIR/lib/muselink-common.sh" || exit 2
ARCH="$ML_BRIDGE_HOME/replies"
[ $# -ge 1 ] || { echo "usage: $0 <req_id> [req_id ...]" >&2; exit 2; }
for id in "$@"; do ml_valid_id "$id" || { echo "bad req_id: $id" >&2; exit 2; }; done

# One PC probe for all ids: "<file name>=<outbox|archive>" per reply file.
IDS_PS="$(printf "%s," "$@" | sed 's/,$//' | sed "s/[^,]\+/'&'/g")"
PC_OUT="$(ml_ps "
\$root = $(ml_psq "$PC_BRIDGE_DIR")
foreach (\$id in @($IDS_PS)) {
  foreach (\$d in 'outbox','archive') {
    Get-ChildItem -LiteralPath (Join-Path \$root \$d) -File -Filter (\$id + '*.reply.json') -ErrorAction SilentlyContinue |
      ForEach-Object { \$_.Name + '=' + \$d }
  }
}
'PROBE_OK'
" 2>/dev/null | tr -d '\r')"
PROBE_OK=0
printf '%s\n' "$PC_OUT" | grep -qx 'PROBE_OK' && PROBE_OK=1

for id in "$@"; do
  echo "== $id"
  any=0; any_final=0; final_read=0; final_outbox=0
  for f in "$ARCH/$id".[0-9][0-9][0-9].reply.json "$ARCH/$id.reply.json"; do
    [ -e "$f" ] || continue
    any=1
    read -r seq final status < <(python3 -c 'import json,sys
d = json.load(open(sys.argv[1], encoding="utf-8"))
fin = d.get("final", d.get("status") != "info")
print(d.get("seq", 0), "final" if fin else "interim", d.get("status", "?"))' "$f")
    if [ "$seq" = "0" ]; then names=("$id.reply.json"); else names=("$(printf '%s.%03d.reply.json' "$id" "$seq")" "$id.reply.json"); fi
    where="none"
    for n in "${names[@]}"; do
      w="$(printf '%s\n' "$PC_OUT" | grep -F "$n=" | head -1 | cut -d= -f2)"
      [ -n "$w" ] && { where="$w"; break; }
    done
    echo "   seq=$seq $final status=$status pc=$where"
    if [ "$final" = "final" ]; then
      any_final=1
      [ "$where" = "archive" ] && final_read=1
      [ "$where" = "outbox" ] && final_outbox=1
    fi
  done
  if [ "$any" = "0" ]; then verdict="NOT_SENT"
  elif [ "$PROBE_OK" = "0" ]; then verdict="PC_PROBE_FAILED (conclude nothing)"
  elif [ "$final_read" = "1" ]; then verdict="FINAL_READ"
  elif [ "$final_outbox" = "1" ]; then verdict="FINAL_DELIVERED_UNREAD"
  elif [ "$any_final" = "1" ]; then verdict="FINAL_MISSING_ON_PC"
  else verdict="INTERIM_ONLY"
  fi
  echo "   => $verdict"
done
