#!/bin/bash
# Stage the RhubarbTart capability demo (#112): the agent customizes + builds a guest image, then a
# clone of it is run under an engagement and sealed into a signed evidence vault.
#
#   ./demo/preflight.sh          # start the control-plane service; clean slate for a live take
#   ./demo/preflight.sh --reset  # also tear down any prior demo clones and remove the demo profile
#
# Prints READY. Run it BEFORE recording, then follow demo/run-of-show.md. Clean up with
# ./demo/teardown.sh. The slow part (the build) happens on camera in Scene 3 — speed it up in post.
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"
# shellcheck source=scripts/env.sh
source "$ROOT/scripts/env.sh"

ENGAGEMENT=nixos-demo-build
RUN=/private/tmp/rbt-demo
mkdir -p "$RUN"
log() { echo "[preflight] $*"; }
die() { echo "[preflight] FAILED: $*" >&2; exit 1; }

if [[ "${1:-}" == "--reset" ]]; then
  log "resetting: tearing down prior demo state"
  "$ROOT/demo/teardown.sh" --purge || true
fi

# 1. Clean slate so the agent creates the profile/lock live on camera.
if [[ -e profiles/nixos-demo.json || -e locks/nixos-demo.lock.json ]]; then
  rm -f profiles/nixos-demo.json locks/nixos-demo.lock.json
  log "removed a leftover nixos-demo profile/lock (the agent recreates them in Scene 2)"
fi

# 2. Confirm the NixOS inputs are cached, so Scene 3's build is fast (no big downloads on camera).
ISO="$(uv run --quiet tools/resolve.py plan juiceshop-target 2>/dev/null \
  | /usr/bin/python3 -c 'import sys,json
try: print(json.load(sys.stdin)["base"]["image"]["sha256"])
except Exception: print("")' || true)"
if [[ -n "$ISO" && -d "cache/artifacts/$ISO" ]]; then
  log "NixOS base image is cached"
else
  log "WARNING: the NixOS base image may not be cached; Scene 3's build could download it"
fi

# 3. Start the control-plane service on its default socket (used by evidence in Scene 5).
SOCK="$(cd "$ROOT/tools" && uv run --quiet python -c \
  'from rhubarb.service import default_socket_path; print(default_socket_path())')"
if [[ -S "$SOCK" ]]; then
  log "control-plane service already listening ($SOCK)"
else
  log "starting the control-plane service"
  nohup ./rhubarb serve >"$RUN/service.log" 2>&1 &
  echo $! >"$RUN/service.pid"
  for _ in $(seq 1 40); do [[ -S "$SOCK" ]] && break; sleep 0.5; done
  [[ -S "$SOCK" ]] || die "the service did not start (see $RUN/service.log)"
fi

echo
log "READY. Environment staged:"
echo "  service    : $SOCK"
echo "  engagement : $ENGAGEMENT (range: profile nixos-demo — built by the agent in Scene 3)"
echo "  slate      : nixos-demo profile/lock absent (the agent creates them on camera)"
echo
echo "  Next: start recording, open herdr, and follow demo/run-of-show.md."
echo "  When done: ./demo/teardown.sh"
