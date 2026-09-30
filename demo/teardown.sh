#!/bin/bash
# Tear down the RhubarbTart capability demo (#112): stop the link and service, remove the
# engagement's clones, and close the herdr workspace arm created. Safe to run repeatedly.
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"
# shellcheck source=scripts/env.sh
source "$ROOT/scripts/env.sh"

ENGAGEMENT=juiceshop-lab
RUN=/private/tmp/rbt-demo
log() { echo "[teardown] $*"; }

# 1. Stop the network link and the control-plane service.
for name in connect service; do
  if [[ -f "$RUN/$name.pid" ]]; then
    kill "$(cat "$RUN/$name.pid")" 2>/dev/null || true
    rm -f "$RUN/$name.pid"
  fi
done
pkill -f "engagement connect $ENGAGEMENT" 2>/dev/null || true
pkill -f "rhubarb_cli.py serve" 2>/dev/null || true
log "stopped the network link and control-plane service"

# 2. Close the herdr workspace arm created (labelled rbt-<engagement>), if herdr is present.
HERDR="$(command -v herdr || echo "$HOME/.local/bin/herdr")"
if [[ -x "$HERDR" ]]; then
  ids="$("$HERDR" workspace list 2>/dev/null \
    | /usr/bin/python3 -c 'import sys,json
try:
    d=json.load(sys.stdin)
except Exception:
    sys.exit()
for w in d.get("result",{}).get("workspaces",[]):
    if w.get("label")=="rbt-'"$ENGAGEMENT"'": print(w.get("workspace_id"))' 2>/dev/null || true)"
  for id in $ids; do
    "$HERDR" workspace close "$id" >/dev/null 2>&1 && log "closed herdr workspace $id"
  done
fi

# 3. Remove the engagement's clones (collects evidence first).
log "tearing down engagement $ENGAGEMENT"
./rhubarb engagement teardown "$ENGAGEMENT" --yes 2>&1 | sed 's/^/  /' || true

log "done. (Evidence + any sealed vault are kept; clear with: rm -rf ~/Library/Application\\ Support/RhubarbTart/evidence/$ENGAGEMENT)"
