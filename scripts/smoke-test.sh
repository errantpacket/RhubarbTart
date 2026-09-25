#!/bin/bash
# Prove the hardening of a built image from the OUTSIDE, on a throwaway clone
# (the image itself is never booted, so it stays exactly as built).
#
#   RHUBARB_SSH_ENABLED=1 ./scripts/smoke-test.sh <vm>
#
# With SSH enabled, the private key for one of the authorized keys must be
# available to ssh (agent or default identity).

set -euo pipefail
# shellcheck source=scripts/env.sh
source "$(dirname "$0")/env.sh"

VM="${1:?vm name}"
USER_NAME="${RHUBARB_USER:-admin}"
SSH_ENABLED="${RHUBARB_SSH_ENABLED:?set to 1 or 0}"
SMOKE="$VM-smoke-$$"
WORK="$(mktemp -d)"
log() { echo "[smoke] $*"; }
fail() { echo "[smoke] FAILED: $*" >&2; exit 1; }

cleanup() {
  tart stop "$SMOKE" >/dev/null 2>&1 || true
  [[ -n "${RUN_PID:-}" ]] && wait "$RUN_PID" 2>/dev/null || true
  tart delete "$SMOKE" >/dev/null 2>&1 || true
  rm -rf "$WORK"
}
trap cleanup EXIT

tart clone "$VM" "$SMOKE"
tart run --no-graphics "$SMOKE" >"$WORK/run.log" 2>&1 &
RUN_PID=$!
IP="$(tart ip --wait 180 "$SMOKE")" || fail "no IP within 180s"
log "clone $SMOKE booted at $IP"

port_open() { nc -z -G 3 "$IP" "$1" >/dev/null 2>&1; }

if [[ "$SSH_ENABLED" == 1 ]]; then
  for _ in $(seq 1 40); do port_open 22 && break; sleep 3; done
  port_open 22 || fail "SSH port not reachable"

  # Pin this clone's host key for the rest of the test (fresh per clone).
  ssh-keyscan -t ed25519 "$IP" 2>/dev/null > "$WORK/known_hosts"
  [[ -s "$WORK/known_hosts" ]] || fail "no ed25519 host key offered (host keys not regenerated?)"
  log "host key: $(ssh-keygen -lf "$WORK/known_hosts")"
  SSH_OPTS=(-o BatchMode=yes -o ConnectTimeout=10 -o StrictHostKeyChecking=yes
            -o UserKnownHostsFile="$WORK/known_hosts")
  SSH=(ssh "${SSH_OPTS[@]}" "$USER_NAME@$IP")

  # 1. Password / keyboard-interactive must be refused; only publickey offered.
  if out="$(ssh "${SSH_OPTS[@]}" -o PubkeyAuthentication=no \
            -o PreferredAuthentications=password,keyboard-interactive "$USER_NAME@$IP" true 2>&1)"; then
    fail "login succeeded without a key"
  fi
  grep -q 'Permission denied (publickey)' <<<"$out" || fail "unexpected auth methods: $out"
  log "ok: password auth refused, server offers publickey only"

  # 2. Key login works (also proves from= matches the host address).
  "${SSH[@]}" true || fail "key login failed (key in agent? RHUBARB_SSH_FROM correct?)"
  log "ok: key login"

  # 3. In-guest posture (no sudo needed; sudo -n must fail: no NOPASSWD).
  "${SSH[@]}" 'bash -s' <<'EOF' || fail "in-guest posture checks failed"
set -e
# (`! cmd` never trips set -e, so negative checks use explicit ifs)
if sudo -n true 2>/dev/null; then echo "passwordless sudo works" >&2; exit 1; fi
if defaults read /Library/Preferences/com.apple.loginwindow autoLoginUser >/dev/null 2>&1; then
  echo "auto-login configured" >&2; exit 1
fi
csrutil status | grep -q 'status: enabled\.'
spctl --status | grep -qx 'assessments enabled'
/usr/libexec/ApplicationFirewall/socketfilterfw --getglobalstate | grep -q 'State = [12]'
/usr/libexec/ApplicationFirewall/socketfilterfw --getstealthmode | grep -Eiq 'stealth mode (is )?(on|enabled)'
test ! -e /etc/kcpassword
test -f /Library/RhubarbTart/sources.lock.json
cat /Library/RhubarbTart/installed.txt
EOF
  log "ok: SIP, Gatekeeper, firewall on; no auto-login; no passwordless sudo"
else
  sleep 60  # let launchd settle so a closed port means disabled, not "not yet up"
  port_open 22 && fail "SSH reachable but should be disabled"
  log "ok: SSH disabled"
fi

port_open 5900 && fail "Screen Sharing (5900) reachable"
log "ok: Screen Sharing not reachable"
log "PASSED"
