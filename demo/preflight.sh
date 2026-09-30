#!/bin/bash
# Stage the RhubarbTart capability demo so the recording has no dead air (#112).
#
#   ./demo/preflight.sh          # provision + boot the lab, start the service + network link
#   ./demo/preflight.sh --reset  # tear the lab down first, then stage fresh
#
# Prints READY when the engagement is up, the control-plane service is listening, and the
# attacker can reach the Juice Shop target over the link. Run it BEFORE you hit record; then
# follow demo/run-of-show.md. Clean up afterwards with ./demo/teardown.sh.
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"
# shellcheck source=scripts/env.sh
source "$ROOT/scripts/env.sh"

ENGAGEMENT=juiceshop-lab
ATTACKER=jsl-attacker
TARGET=jsl-target
RUN=/private/tmp/rbt-demo
mkdir -p "$RUN"

log() { echo "[preflight] $*"; }
die() { echo "[preflight] FAILED: $*" >&2; exit 1; }

if [[ "${1:-}" == "--reset" ]]; then
  log "resetting: tearing the lab down first"
  "$ROOT/demo/teardown.sh" || true
fi

# 1. Provision the engagement's ranges from their verified images (idempotent: skips existing).
log "provisioning engagement $ENGAGEMENT (from verified images)"
./rhubarb engagement provision "$ENGAGEMENT" 2>&1 | sed 's/^/  /'

# 2. Boot both clones headless.
for vm in "$TARGET" "$ATTACKER"; do
  if ./rhubarb list 2>/dev/null | awk -v v="$vm" '$1==v && $0 ~ /running/{f=1} END{exit !f}'; then
    log "$vm already running"
  else
    log "booting $vm"
    ./rhubarb run "$vm" --headless --detach 2>&1 | sed 's/^/  /'
  fi
done

# 3. Wait until the attacker answers SSH and the target's Juice Shop answers on :3000.
log "waiting for $ATTACKER to accept SSH"
for _ in $(seq 1 60); do ./rhubarb ssh "$ATTACKER" -- true >/dev/null 2>&1 && break; sleep 3; done
./rhubarb ssh "$ATTACKER" -- true >/dev/null 2>&1 || die "$ATTACKER never became reachable"

TARGET_IP="$(tart ip "$TARGET" 2>/dev/null || true)"
[[ -n "$TARGET_IP" ]] || die "no IP for $TARGET"
log "waiting for Juice Shop on $TARGET ($TARGET_IP:3000)"
for _ in $(seq 1 60); do curl -sf -o /dev/null -m 3 "http://$TARGET_IP:3000/" && break; sleep 3; done
curl -sf -o /dev/null -m 3 "http://$TARGET_IP:3000/" || die "Juice Shop never answered on $TARGET"

# 4. Start the control-plane service on its default socket (what `rhubarb herdr arm` uses).
SOCK="$(cd "$ROOT/tools" && uv run --quiet python -c \
  'from rhubarb.service import default_socket_path; print(default_socket_path())')"
[[ -n "$SOCK" ]] || die "could not resolve the default service socket path"
if [[ -S "$SOCK" ]]; then
  log "control-plane service already listening ($SOCK)"
else
  log "starting the control-plane service"
  nohup ./rhubarb serve >"$RUN/service.log" 2>&1 &
  echo $! >"$RUN/service.pid"
  for _ in $(seq 1 40); do [[ -S "$SOCK" ]] && break; sleep 0.5; done
  [[ -S "$SOCK" ]] || die "the service did not start (see $RUN/service.log)"
fi

# 5. Open the network link so the attacker reaches the target's :3000 on its own loopback.
if pgrep -f "engagement connect $ENGAGEMENT" >/dev/null 2>&1; then
  log "network link already open"
else
  log "opening the engagement network link (attacker 127.0.0.1:3000 -> target)"
  nohup ./rhubarb engagement connect "$ENGAGEMENT" >"$RUN/connect.log" 2>&1 &
  echo $! >"$RUN/connect.pid"
  for _ in $(seq 1 20); do grep -q "127.0.0.1:3000" "$RUN/connect.log" 2>/dev/null && break; sleep 0.5; done
fi
# Confirm the link end to end from inside the attacker.
if ./rhubarb ssh "$ATTACKER" -- 'curl -sf -o /dev/null -m 5 http://127.0.0.1:3000/' >/dev/null 2>&1; then
  log "verified: attacker reaches Juice Shop at 127.0.0.1:3000 over the link"
else
  log "WARNING: the attacker could not reach 127.0.0.1:3000 yet; give the link a moment"
fi

echo
log "READY. Environment staged:"
echo "  engagement : $ENGAGEMENT  (attacker=$ATTACKER, target=$TARGET)"
echo "  service    : $SOCK"
echo "  link       : attacker 127.0.0.1:3000 -> Juice Shop on $TARGET"
echo
echo "  Next: start recording, open herdr, and follow demo/run-of-show.md."
echo "  When done: ./demo/teardown.sh"
