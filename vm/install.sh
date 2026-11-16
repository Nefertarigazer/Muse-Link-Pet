#!/usr/bin/env bash
# install.sh - make a freshly copied vm/ tree the one that runs (package way).
# Copy the whole vm/ folder of ONE verified release to the VM first, e.g.
#   ~/workspace/tools/muselink/   (this script then lives there)
# and run, as the agent user:
#   bash ~/workspace/tools/muselink/install.sh            # check + plan only
#   bash ~/workspace/tools/muselink/install.sh --apply    # do it
# What it does (nothing outside the lines below; never deletes data):
#   1. verifies every file against RELEASE-MANIFEST.sha256 (if present);
#   2. removes __pycache__, sets the executable bits;
#   3. site configuration: with the site file (~/.config/muselink/
#      muselink.site.json or --site FILE; template muselink.site.example.json)
#      it generates ~/.config/muselink/{muselink.env,policy.json,publish.env}
#      through site-config.py: missing files are written, matching ones left
#      alone, differing ones are a FAIL unless --replace-config (old copy
#      kept). Without a site file it only checks that those files exist.
#      Then it reports the identity store and the bridge home (never touched);
#   4. writes the hook wrappers ~/hooks/scripts/ml-<detector>.sh;
#   5. renders the systemd unit to <tree>/muse-bridge.service.rendered.
# Root-only and platform steps are PRINTED, not run: installing the unit
# (sudo) and pointing each hook definition's script_path at its wrapper.
#   options: --apply  --site FILE  --replace-config
set -u
DIR="$(cd "$(dirname "$0")" && pwd)"
APPLY=0 FORCE=0 REPLACE="" SITE=""
while [ $# -gt 0 ]; do
  case "$1" in
    --apply) APPLY=1 ;;
    --force) FORCE=1 ;;
    --replace-config) REPLACE="--replace-config" ;;
    --site) SITE="${2:-}"; shift ;;
    *) echo "usage: install.sh [--apply] [--force] [--site FILE] [--replace-config]" >&2; exit 2 ;;
  esac
  shift
done
# One helper process reads the PC locale once for both runtime document paths.
DOC_PATHS="$(PYTHONPATH="$DIR${PYTHONPATH:+:$PYTHONPATH}" python3 - "$DIR" <<'PY'
from pathlib import Path
import sys
from muselink_id.i18n import doc_path
root = Path(sys.argv[1])
print(doc_path(root / 'bridge/PROTOCOL'))
base = root / 'standing-orders.example'
if not doc_path(base).is_file() and (root.parent / 'config').is_dir():
    base = root.parent / 'config/standing-orders.example'
print(doc_path(base))
PY
)"
PROTOCOL_DOC="${DOC_PATHS%%$'\n'*}"
STANDING_DOC="${DOC_PATHS#*$'\n'}"
CFG="${MUSELINK_HOME:-$HOME}/.config/muselink"
[ -n "$SITE" ] || SITE="$CFG/muselink.site.json"
BRIDGE_HOME="${ML_BRIDGE_HOME:-$HOME/workspace/muse-bridge}"
HOOKS="$HOME/hooks/scripts"
fail=0
say() { printf '%s\n' "$*"; }
bad() { printf 'FAIL %s\n' "$*"; fail=1; }

say "== tree: $DIR"
if [ -f "$DIR/RELEASE-MANIFEST.sha256" ]; then
  if (cd "$DIR" && sha256sum --quiet -c RELEASE-MANIFEST.sha256); then
    say "ok   every file matches RELEASE-MANIFEST.sha256 ($(wc -l < "$DIR/RELEASE-MANIFEST.sha256") files)"
  else
    bad "files differ from RELEASE-MANIFEST.sha256 (copy the release again)"
  fi
else
  say "warn no RELEASE-MANIFEST.sha256: copy from a tagged release to get one"
fi

say "== site configuration (from the site file; never shipped)"
SITE_MODE=0
if [ -f "$SITE" ]; then
  SITE_MODE=1
  python3 "$DIR/site-config.py" check --site "$SITE" $REPLACE || fail=1
else
  say "warn no site file $SITE: the existing files are used as they are"
  say "     (template: muselink.site.example.json in the release; see docs/install.en.md section 2)"
  for f in muselink.env policy.json; do
    [ -f "$CFG/$f" ] && say "ok   $CFG/$f" || bad "$CFG/$f missing (write the site file, then rerun)"
  done
  [ -f "$CFG/publish.env" ] && say "ok   $CFG/publish.env" || say "warn $CFG/publish.env missing: releases cannot run"
fi
[ -f "$CFG/standing-orders.md" ] || say "warn no $CFG/standing-orders.md: fill $STANDING_DOC (repository: config/standing-orders.example.en.md; docs/install.en.md section 5)"
[ -d "$CFG/identity" ] && say "ok   identity store $CFG/identity" || say "warn no identity store yet (created on first use)"
[ -d "$BRIDGE_HOME" ] && say "ok   bridge home $BRIDGE_HOME" || say "warn bridge home $BRIDGE_HOME missing (created on first start)"

say "== plan"
[ "$SITE_MODE" -eq 1 ] && say "     write the configuration files listed as new/repl above"
say "     remove __pycache__ under the tree; chmod +x every *.sh, bridge/bridge-service.py, site-config.py"
# Compare the exact rendered bytes without writing a temporary file in preview.
render_file() {
  local dest="$1" content="$2" state=new
  if [ -f "$dest" ]; then
    if [ "$FORCE" -eq 0 ] && cmp -s "$dest" <(printf '%s\n' "$content"); then
      say "skip $dest: already installed (rendered content matches)"
      return
    fi
    state=change
  fi
  say "$state $dest"
  if [ "$APPLY" -eq 1 ] && [ "$fail" -eq 0 ]; then
    printf '%s\n' "$content" > "$dest.tmp" && mv -f "$dest.tmp" "$dest" || { bad "could not write $dest"; return; }
  fi
}
render_targets() {
  local d content w
  for d in bridge-postman popup-fallback bridge-alert-watch; do
    w="$HOOKS/ml-$d.sh"
    content="$(sed -e "s|^DETECTOR=.*|DETECTOR=$d.sh|" \
        -e "s|^export MUSELINK_VM_DIR=.*|export MUSELINK_VM_DIR=\"\${MUSELINK_VM_DIR:-$DIR}\"|" \
        "$DIR/hooks/wrapper.sh.example")" || { bad "could not render $w"; continue; }
    render_file "$w" "$content"
    if [ "$APPLY" -eq 1 ] && [ "$fail" -eq 0 ]; then chmod +x "$w"; fi
  done
  content="$(bash "$DIR/bridge/render-unit.sh" "$(id -un)" "$DIR" "${MUSELINK_HOME:-$HOME}" "$CFG/muselink.env")" || { bad 'could not render service'; return; }
  render_file "$DIR/muse-bridge.service.rendered" "$content"
}

if [ "$APPLY" -eq 1 ] && [ "$fail" -eq 0 ]; then
  if [ "$SITE_MODE" -eq 1 ]; then
    python3 "$DIR/site-config.py" write --site "$SITE" $REPLACE || { say "== NOT applied: configuration could not be written"; exit 1; }
  fi
  find "$DIR" -name __pycache__ -type d -prune -exec rm -rf {} +
  find "$DIR" -name '*.sh' -exec chmod +x {} +
  chmod +x "$DIR/bridge/bridge-service.py" "$DIR/site-config.py"
  mkdir -p "$HOOKS"
  render_targets
  [ "$fail" -eq 0 ] || exit 1
  say "== applied"
elif [ "$APPLY" -eq 1 ]; then
  say "== NOT applied: fix the FAIL lines first"; exit 1
else
  render_targets
  say "== check only; rerun with --apply"
fi

say "== hook prompt protocol: $PROTOCOL_DOC"
say "== then, by hand (root / platform):"
say "     sudo install -m 644 $DIR/muse-bridge.service.rendered /etc/systemd/system/muse-bridge.service"
say "     sudo systemctl daemon-reload && sudo systemctl enable --now muse-bridge && sudo systemctl restart muse-bridge"
say "     hook popup-wake-fallback  script_path -> $HOOKS/ml-popup-fallback.sh"
say "     hook codex-bridge-watch   script_path -> $HOOKS/ml-bridge-postman.sh"
say "     hook bridge-alert-watch   register from $DIR/hooks/definitions/bridge-alert-watch.json -> $HOOKS/ml-bridge-alert-watch.sh"
say "     tailnet policy (first install only): python3 $DIR/site-config.py acl --site $SITE  -> paste in the Tailscale admin console"
exit "$fail"
