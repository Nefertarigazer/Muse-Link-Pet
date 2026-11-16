#!/usr/bin/env bash
# popup-ask.sh - FIXED popup entry point (user request 2026-11-08). Muse only
# supplies the words and the options; this script builds the staging files
# and drives popup-send.sh end to end. Never hand-stage popup files.
#   popup-ask.sh "text"                       -> info card (OK button)
#   popup-ask.sh "text" "opt 1" "opt 2" [..]  -> choice card (max 4 options)
#   popup-ask.sh --input "text"               -> input card (free text reply)
#   popup-ask.sh --no-wait "text" ...         -> fire and forget (hook fallback delivers)
# Output: popup-send.sh output verbatim; the user's answer block is in it.
# Exit code: popup-send.sh's (0 = answered/delivered).
set -u
SEND="$(cd "$(dirname "$0")" && pwd)/popup-send.sh"

WAIT="--wait" INPUT=0 EXPIRES="" OWNER=""
ARGS=()
while [ $# -gt 0 ]; do
  case "$1" in
    --no-wait) WAIT="--no-wait"; shift ;;
    --input) INPUT=1; shift ;;
    --expires-in) EXPIRES="$2"; shift 2 ;;
    --owner) OWNER="$2"; shift 2 ;;
    --) shift; while [ $# -gt 0 ]; do ARGS+=("$1"); shift; done ;;
    *) ARGS+=("$1"); shift ;;
  esac
done

[ "${#ARGS[@]}" -ge 1 ] && [ -n "${ARGS[0]}" ] || { echo "usage: popup-ask.sh [--input|--no-wait] \"text\" [\"option 1\" ...]" >&2; exit 3; }
TEXT="${ARGS[0]}"
OPTS=("${ARGS[@]:1}")
[ "${#OPTS[@]}" -le 4 ] || { echo "too many options (max 4)" >&2; exit 3; }

if [ "$INPUT" -eq 1 ]; then MODE="input"
elif [ "${#OPTS[@]}" -ge 1 ]; then MODE="choice"
else MODE="info"; fi

TMPD="$(mktemp -d)"
# no exec below: exec would skip this trap and leave the card text in /tmp
trap 'rm -rf "$TMPD"' EXIT
printf '%s' "$TEXT" > "$TMPD/text.txt"

EXTRA=()
if [ -n "$EXPIRES" ]; then EXTRA=(--expires-in "$EXPIRES"); fi
if [ -n "$OWNER" ]; then EXTRA+=(--owner "$OWNER"); fi
if [ "$MODE" = "choice" ]; then
  printf '%s\n' "${OPTS[@]}" > "$TMPD/options.txt"
  "$SEND" --mode choice --text-file "$TMPD/text.txt" --options-file "$TMPD/options.txt" "${EXTRA[@]}" "$WAIT"
else
  "$SEND" --mode "$MODE" --text-file "$TMPD/text.txt" "${EXTRA[@]}" "$WAIT"
fi
