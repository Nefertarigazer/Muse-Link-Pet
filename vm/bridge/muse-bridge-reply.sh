#!/usr/bin/env bash
# muse-bridge-reply.sh - deliver one reply to the local agent that asked.
#   muse-bridge-reply.sh <req_id> <ok|error|needs-user|info> <text-file> [--interim|--final]
#
# Reply protocol v2 (docs/protocol.en.md section 2): every reply is its
# own numbered message. "info" is interim by default (the ACK, progress);
# ok / error / needs-user are final. The local ledger keeps EVERY reply as
# replies/<req_id>.<seq>.reply.json. On the PC the file is
# outbox\<req_id>.<seq>.reply.json when the request declared
# "reply_protocol": 2, else the v1 name outbox\<req_id>.reply.json (old
# clients only know that name). Shipping: scp to a .tmp name, then an atomic
# rename, so a waiting client only ever sees complete replies.
#
# Identity protocol v3 (implemented 2026-11-12) (docs/protocol.en.md section 3):
# when the request's reply_protocol is 3, build the inner reply exactly as
# below plus "agent_id" (from verdicts/<id>.json), with "reply_protocol": 3,
# then run `muselink-id.sh sign-reply <inner.json> <envelope.json>` and ship
# the ENVELOPE (same v2 file name <req_id>.<seq3>.reply.json). sign-reply
# exit 20 (request not verified) -> ship the unsigned v2 reply as today. Any
# other failure -> do not ship an unsigned reply to a v3 client (its client
# would quarantine it anyway); exit non-zero so the caller retries. The
# ledger keeps the shipped bytes. The seq lock must cover signing too (the
# seq is inside the signature).
set -euo pipefail
DIR="$(cd "$(dirname "$0")/.." && pwd)"
. "$DIR/lib/muselink-common.sh" || exit 2

REQ_ID="${1:-}"; STATUS="${2:-}"; TEXTFILE="${3:-}"; MODE="${4:-}"
ml_valid_id "$REQ_ID" || { echo "bad req_id: $REQ_ID" >&2; exit 2; }
case "$STATUS" in ok|error|needs-user|info) ;; *) echo "bad status: $STATUS" >&2; exit 2 ;; esac
[ -f "$TEXTFILE" ] || { echo "reply text file not found: $TEXTFILE" >&2; exit 2; }
FINAL=true
[ "$STATUS" = "info" ] && FINAL=false
case "$MODE" in --interim) FINAL=false ;; --final) FINAL=true ;; "") ;; *) echo "bad flag: $MODE" >&2; exit 2 ;; esac

ARCH="$ML_BRIDGE_HOME/replies"
mkdir -p "$ARCH"
TMPD="$(mktemp -d)"; trap 'rm -rf "$TMPD"' EXIT

PROTO=1
# Where the request lives decides its shape (review 2026-11-12: only the
# worker shape was understood, so replies to rejected v3 requests and to
# enrollments - pending/processed hold {"inner","envelope"}, rejected/ and
# envelopes/ hold the raw envelope - went out as unsigned v1 files that a
# v3 client quarantines):
#   worker request          reply_protocol field
#   {"inner", "envelope"}   inner.reply_protocol
#   raw v3 envelope         "v": 3  -> 3
for d in inbox working processed pending rejected envelopes; do
  f="$ML_BRIDGE_HOME/$d/$REQ_ID.json"
  if [ -f "$f" ]; then
    PROTO="$(python3 -c 'import json,sys
try:
    d = json.load(open(sys.argv[1], encoding="utf-8-sig"))
    if isinstance(d.get("inner"), dict):
        p = d["inner"].get("reply_protocol", 1)
    elif d.get("v") == 3:
        p = 3
    else:
        p = d.get("reply_protocol", 1)
    print(int(p) if isinstance(p, int) and not isinstance(p, bool) else 1)
except Exception:
    print(1)' "$f")"
    break
  fi
done

# allocate the next sequence number under a lock (two senders can race:
# the bridge worker and the release pipeline)
exec 9>"$ARCH/.seq.lock"
flock 9
SEQ=1
for f in "$ARCH/$REQ_ID".[0-9][0-9][0-9].reply.json; do
  [ -e "$f" ] || continue
  n="${f##*/$REQ_ID.}"; n="${n%%.reply.json}"; n=$((10#$n))
  [ "$n" -ge "$SEQ" ] && SEQ=$((n + 1))
done
SEQ3="$(printf '%03d' "$SEQ")"

REQ_ID="$REQ_ID" STATUS="$STATUS" TEXTFILE="$TEXTFILE" SEQ="$SEQ" FINAL="$FINAL" PROTO="$PROTO" \
VERDICT="$ML_BRIDGE_HOME/verdicts/$REQ_ID.json" \
python3 - "$TMPD/reply.json" <<'PYEOF'
import json, os, sys, datetime
out = sys.argv[1]
text = open(os.environ["TEXTFILE"], encoding="utf-8").read().strip()
proto = int(os.environ.get("PROTO", "1"))
reply = {
    "req_id": os.environ["REQ_ID"],
    "seq": int(os.environ["SEQ"]),
    "final": os.environ["FINAL"] == "true",
    "status": os.environ["STATUS"],
    "replied_at": datetime.datetime.now(datetime.timezone.utc).isoformat(),
    "from": "muse",
    "reply_protocol": 3 if proto >= 3 else 2,
    "text": text,
}
if proto >= 3:
    try:
        with open(os.environ["VERDICT"], encoding="utf-8") as f:
            reply["agent_id"] = str(json.load(f).get("agent_id", ""))
    except Exception:
        reply["agent_id"] = ""
with open(out, "w", encoding="utf-8") as f:
    json.dump(reply, f, ensure_ascii=False, indent=2)
PYEOF
SHIP="$TMPD/reply.json"
if [ "$PROTO" -ge 3 ]; then
  if bash "$DIR/muselink-id.sh" sign-reply "$TMPD/reply.json" "$TMPD/envelope.json" >/dev/null 2>&1; then
    SHIP="$TMPD/envelope.json"
  else
    rc=$?
    if [ "$rc" -ne 20 ]; then
      echo "sign-reply failed rc=$rc; not shipping an unsigned reply to a v3 client" >&2
      flock -u 9
      exit 1
    fi
  fi
fi
cp "$SHIP" "$ARCH/$REQ_ID.$SEQ3.reply.json"
ml_fix_owner "$ARCH/$REQ_ID.$SEQ3.reply.json"
flock -u 9

if [ "$PROTO" -ge 2 ]; then NAME="$REQ_ID.$SEQ3.reply.json"; else NAME="$REQ_ID.reply.json"; fi
ml_scp_to "$SHIP" "$ML_PC_BRIDGE_FWD/outbox/$NAME.tmp"
ml_ps "Move-Item -LiteralPath $(ml_psq "$PC_BRIDGE_DIR\\outbox\\$NAME.tmp") -Destination $(ml_psq "$PC_BRIDGE_DIR\\outbox\\$NAME") -Force" >/dev/null
echo "REPLY_SENT req=$REQ_ID seq=$SEQ status=$STATUS final=$FINAL name=$NAME"
