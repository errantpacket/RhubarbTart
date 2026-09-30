#!/bin/bash
# Tear down the RhubarbTart capability demo (#112): stop the control-plane service, close the
# herdr workspace, and remove the engagement's clones. With --purge, also remove the agent-created
# profile/lock and the demo evidence for a fully fresh take. Safe to run repeatedly.
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"
# shellcheck source=scripts/env.sh
source "$ROOT/scripts/env.sh"

ENGAGEMENT=nixos-demo-build
RUN=/private/tmp/rbt-demo
log() { echo "[teardown] $*"; }

# 1. Stop the control-plane service.
if [[ -f "$RUN/service.pid" ]]; then kill "$(cat "$RUN/service.pid")" 2>/dev/null || true; rm -f "$RUN/service.pid"; fi
pkill -f "rhubarb_cli.py serve" 2>/dev/null || true
log "stopped the control-plane service"

# 2. Close the herdr workspace the demo agent ran in.
HERDR="$(command -v herdr || echo "$HOME/.local/bin/herdr")"
if [[ -x "$HERDR" ]]; then
  ids="$("$HERDR" workspace list 2>/dev/null | /usr/bin/python3 -c 'import sys,json
try: d=json.load(sys.stdin)
except Exception: sys.exit()
for w in d.get("result",{}).get("workspaces",[]):
    if w.get("label")=="rbt-build-demo": print(w.get("workspace_id"))' 2>/dev/null || true)"
  for id in $ids; do "$HERDR" workspace close "$id" >/dev/null 2>&1 && log "closed herdr workspace $id"; done
fi

# 3. Remove the engagement's clones (collects evidence first).
./rhubarb engagement teardown "$ENGAGEMENT" --yes 2>&1 | sed 's/^/  /' || true

if [[ "${1:-}" == "--purge" ]]; then
  rm -f profiles/nixos-demo.json locks/nixos-demo.lock.json
  rm -rf "$HOME/Library/Application Support/RhubarbTart/evidence/$ENGAGEMENT"
  log "purged the agent-created profile/lock and the demo evidence"
fi
log "done."
