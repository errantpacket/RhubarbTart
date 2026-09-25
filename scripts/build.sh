#!/bin/bash
# Build the RhubarbTart image from sources.lock.json.
#
#   RHUBARB_SSH_PUBKEYS=~/.ssh/id_ed25519.pub ./scripts/build.sh
#   REBUILD_VANILLA=1 ./scripts/build.sh       force a fresh install from the IPSW
#
# Environment:
#   RHUBARB_USER          guest admin account name (default: admin)
#   RHUBARB_SSH_PUBKEYS   file of public keys allowed into the final image.
#                           Unset => Remote Login is disabled in the image.
#   RHUBARB_SSH_FROM      authorized_keys from= pattern (default: 192.168.64.1,
#                           Tart's shared-NAT host address); set empty to omit.
#   RHUBARB_CACHE         artifact cache (default: ./cache)
#
# The guest password is generated per vanilla install and kept only in the
# host's login keychain (service "RhubarbTart", account = VM name):
#   security find-generic-password -s RhubarbTart -a <vm> -w

set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"
# shellcheck source=scripts/env.sh
source "$ROOT/scripts/env.sh"
log() { echo "[build] $*"; }
die() { echo "[build] FAILED: $*" >&2; exit 1; }
KEYCHAIN_SERVICE=RhubarbTart

# --- preflight --------------------------------------------------------------
[[ "$(uname -s)" == Darwin && "$(uname -m)" == arm64 ]] || die "Tart requires macOS on Apple silicon"
for bin in tart packer uv ssh-keygen security; do command -v "$bin" >/dev/null || die "$bin not found"; done
[[ -f sources.lock.json ]] || die "no sources.lock.json; run: uv run tools/resolve.py resolve"
uv run --quiet tools/resolve.py preflight
if [[ -n "$(git status --porcelain 2>/dev/null)" ]]; then
  log "WARNING: working tree is dirty; provenance will record git_dirty=true"
fi

py() { uv run --quiet --no-project python -c "$@"; }
json_field() { plutil -extract "$1" raw -o - - <<<"$2"; }   # macOS plutil reads JSON
vm_exists() { tart get "$1" >/dev/null 2>&1; }

WORK="$(mktemp -d)"
trap 'rm -rf "$WORK"' EXIT
chmod 700 "$WORK"

# --- SSH authorized keys (validated on the host) -------------------------------
AUTH_KEYS="$WORK/authorized_keys"
: > "$AUTH_KEYS"
if [[ -n "${RHUBARB_SSH_PUBKEYS:-}" ]]; then
  [[ -f "$RHUBARB_SSH_PUBKEYS" ]] || die "RHUBARB_SSH_PUBKEYS not a file: $RHUBARB_SSH_PUBKEYS"
  while IFS= read -r line; do
    [[ "$line" =~ ^[[:space:]]*(#|$) ]] && continue
    case "$line" in
      ssh-ed25519\ *|sk-ssh-ed25519@openssh.com\ *|ecdsa-sha2-nistp256\ *|sk-ecdsa-sha2-nistp256@openssh.com\ *) ;;
      *) die "unsupported key type (use ed25519/ecdsa, optionally -sk): ${line:0:40}…" ;;
    esac
    ssh-keygen -lf /dev/stdin <<<"$line" >/dev/null || die "invalid public key: ${line:0:40}…"
    echo "$line" >> "$AUTH_KEYS"
  done < "$RHUBARB_SSH_PUBKEYS"
  [[ -s "$AUTH_KEYS" ]] || die "no usable keys in $RHUBARB_SSH_PUBKEYS"
  log "SSH: $(wc -l < "$AUTH_KEYS" | tr -d ' ') key(s) authorized"
else
  log "SSH: RHUBARB_SSH_PUBKEYS unset -> Remote Login will be DISABLED in the image"
fi

SSH_FROM="${RHUBARB_SSH_FROM-192.168.64.1}"
[[ "$SSH_FROM" =~ ^[0-9A-Fa-f.:/*?,!]*$ ]] || die "RHUBARB_SSH_FROM has unexpected characters"

export PKR_VAR_username="${RHUBARB_USER:-admin}"
export PKR_VAR_authorized_keys_path="$AUTH_KEYS"
export PKR_VAR_ssh_from="$SSH_FROM"

# --- verify inputs & stage ----------------------------------------------------
log "verifying cached inputs against sources.lock.json"
info="$(uv run --quiet tools/resolve.py verify)"
IPSW="$(json_field ipsw_path "$info")"
STAGE="$(json_field stage_dir "$info")"
BUILD="$(json_field macos_build "$info")"
INPUTS_SHA="$(json_field inputs_sha256 "$info")"

BASE_VM="rbt-tahoe-${BUILD}-vanilla"
VM="rbt-tahoe-${BUILD}-${INPUTS_SHA:0:12}"   # same artifacts => same name
CANDIDATE="$VM-unverified"                  # renamed to $VM only after the smoke test passes

# --- password handling (keychain only; env, never argv, for packer) -----------------
keychain_get() { security find-generic-password -s "$KEYCHAIN_SERVICE" -a "$1" -w 2>/dev/null; }
keychain_put() { # <account> <password>; `security -i` keeps the secret out of argv
  printf 'add-generic-password -U -s %s -a %s -w %s\n' "$KEYCHAIN_SERVICE" "$1" "$2" | security -i >/dev/null
  [[ "$(keychain_get "$1")" == "$2" ]] || die "could not store password in keychain for $1"
}

# --- stage 1: vanilla from IPSW -----------------------------------------------
if [[ "${REBUILD_VANILLA:-0}" == 1 ]] && vm_exists "$BASE_VM"; then
  tart delete "$BASE_VM"
fi
if vm_exists "$BASE_VM"; then
  PKR_VAR_password="$(keychain_get "$BASE_VM")" \
    || die "$BASE_VM exists but its password is not in the keychain; rerun with REBUILD_VANILLA=1"
  export PKR_VAR_password
  log "reusing $BASE_VM (set REBUILD_VANILLA=1 to reinstall from IPSW)"
else
  PKR_VAR_password="$(py 'import secrets,string; a=string.ascii_letters+string.digits; print("".join(secrets.choice(a) for _ in range(32)))')"
  export PKR_VAR_password
  keychain_put "$BASE_VM" "$PKR_VAR_password"
  log "installing macOS from $IPSW -> $BASE_VM (password stored in keychain)"
  packer build -var "ipsw_path=$IPSW" -var "vm_name=$BASE_VM" packer/01-vanilla.pkr.hcl
fi

# --- stage 2: apps + hardening ---------------------------------------------------
if vm_exists "$CANDIDATE"; then tart delete "$CANDIDATE"; fi
log "building $CANDIDATE from $BASE_VM"
packer build -var "base_vm=$BASE_VM" -var "vm_name=$CANDIDATE" -var "stage_dir=$STAGE" \
  packer/02-apps.pkr.hcl

# --- prove the hardening on a throwaway clone; only then give it the real name --------
RHUBARB_SSH_ENABLED="$([[ -s "$AUTH_KEYS" ]] && echo 1 || echo 0)" \
  ./scripts/smoke-test.sh "$CANDIDATE" \
  || die "smoke test failed; unverified image left as $CANDIDATE for inspection"
if vm_exists "$VM"; then tart delete "$VM"; fi
tart rename "$CANDIDATE" "$VM"
keychain_put "$VM" "$PKR_VAR_password"
unset PKR_VAR_password

# --- provenance ----------------------------------------------------------------
record="$(uv run --quiet tools/resolve.py provenance "$VM")"
log "done: $VM"
log "provenance: $record"
