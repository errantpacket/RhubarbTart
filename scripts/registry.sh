#!/bin/bash
# Localhost-only OCI registry (zot, pinned in .toolchain) for publishing verified images (#32).
#
#   ./scripts/registry.sh start|stop|status
#
# Listens on 127.0.0.1 only (not the LAN, not the VMs). Storage lives under the git-ignored
# cache dir. RHUBARB_REGISTRY_PORT overrides the port (5000 is macOS's AirPlay Receiver).
# A remote registry is used by setting RHUBARB_REGISTRY in publish.sh; this script only
# manages the local one.

set -euo pipefail
# shellcheck source=scripts/env.sh
source "$(dirname "$0")/env.sh"

PORT="${RHUBARB_REGISTRY_PORT:-5780}"
[[ "$PORT" =~ ^[0-9]{2,5}$ ]] || { echo "[registry] bad RHUBARB_REGISTRY_PORT" >&2; exit 1; }
DIR="${RHUBARB_CACHE:-$RHUBARB_ROOT/cache}/registry"
PIDFILE="$DIR/zot.pid"
log() { echo "[registry] $*"; }
die() { echo "[registry] FAILED: $*" >&2; exit 1; }

running() { [[ -f "$PIDFILE" ]] && kill -0 "$(cat "$PIDFILE")" 2>/dev/null; }
healthy() { curl --silent --fail --max-time 3 "http://127.0.0.1:$PORT/v2/" >/dev/null; }

case "${1:-}" in
  start)
    if running; then log "already running (pid $(cat "$PIDFILE")) on 127.0.0.1:$PORT"; exit 0; fi
    mkdir -p "$DIR/storage"
    chmod 700 "$DIR"
    cat > "$DIR/config.json" <<EOF
{
  "distSpecVersion": "1.1.1",
  "storage": { "rootDirectory": "$DIR/storage", "dedupe": true, "gc": true },
  "http": { "address": "127.0.0.1", "port": "$PORT" },
  "log": { "level": "warn", "output": "$DIR/zot.log" }
}
EOF
    zot verify "$DIR/config.json" >/dev/null 2>&1 || die "zot rejected $DIR/config.json"
    nohup zot serve "$DIR/config.json" >>"$DIR/zot.log" 2>&1 &
    echo $! > "$PIDFILE"
    for _ in $(seq 1 30); do healthy && break; sleep 0.5; done
    healthy || die "zot did not come up on 127.0.0.1:$PORT (see $DIR/zot.log)"
    log "running (pid $(cat "$PIDFILE")) on 127.0.0.1:$PORT, storage $DIR/storage"
    ;;
  stop)
    if running; then kill "$(cat "$PIDFILE")"; log "stopped"; else log "not running"; fi
    rm -f "$PIDFILE"
    ;;
  status)
    if running && healthy; then log "running (pid $(cat "$PIDFILE")) on 127.0.0.1:$PORT"
    elif running; then die "process up but not answering on 127.0.0.1:$PORT"
    else log "not running"; exit 3; fi
    ;;
  *) echo "usage: $0 start|stop|status" >&2; exit 2 ;;
esac
