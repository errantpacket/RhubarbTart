#!/bin/bash
# The cosign key pair that signs published images (#32). Private key + passphrase live only in
# the host login keychain (service RhubarbTart-signing); the public key is committed at
# config/keys/rhubarb-cosign.pub so anyone can verify.
#
#   ./scripts/signing-key.sh init     # once per publisher; refuses to overwrite
#   ./scripts/signing-key.sh status
#
# Secrets never reach argv or a lasting file: the passphrase goes to cosign via env, the key is
# written by cosign into a 0700 temp dir, stored hex-encoded via `security -i` (stdin), and the
# temp files are removed with rm -P.

set -euo pipefail
# shellcheck source=scripts/env.sh
source "$(dirname "$0")/env.sh"

SERVICE=RhubarbTart-signing
PUB="$RHUBARB_ROOT/config/keys/rhubarb-cosign.pub"
log() { echo "[signing-key] $*"; }
die() { echo "[signing-key] FAILED: $*" >&2; exit 1; }
kc_has() { security find-generic-password -s "$SERVICE" -a "$1" >/dev/null 2>&1; }
kc_put() { # <account> <alnum value>, via stdin so the secret is never in argv
  [[ "$2" =~ ^[A-Za-z0-9]+$ ]] || die "refusing non-alphanumeric keychain value for $1"
  printf 'add-generic-password -U -s %s -a %s -w %s\n' "$SERVICE" "$1" "$2" | security -i >/dev/null
  kc_has "$1" || die "could not store $1 in the keychain"
}

case "${1:-}" in
  init)
    if kc_has cosign-key || [[ -e "$PUB" ]]; then
      die "a signing key already exists (keychain $SERVICE and/or $PUB); not overwriting"
    fi
    WORK="$(mktemp -d)"; chmod 700 "$WORK"
    trap 'rm -P "$WORK"/* 2>/dev/null || true; rm -rf "$WORK"' EXIT
    pass="$(xxd -l 20 -p /dev/urandom)"   # 160 random bits as 40 hex chars; no pipe (SIGPIPE under pipefail)
    COSIGN_PASSWORD="$pass" cosign generate-key-pair --output-key-prefix "$WORK/rhubarb" >/dev/null 2>&1 \
      || die "cosign generate-key-pair failed"
    kc_put cosign-password "$pass"
    kc_put cosign-key "$(xxd -p "$WORK/rhubarb.key" | tr -d '\n')"
    install -m 644 "$WORK/rhubarb.pub" "$PUB"
    log "key pair created; private key + passphrase in keychain service $SERVICE"
    log "public key: ${PUB#"$RHUBARB_ROOT"/} (commit it)"
    ;;
  status)
    if kc_has cosign-key && kc_has cosign-password; then log "private key: in keychain ($SERVICE)"
    else log "private key: MISSING (run: $0 init)"; exit 3; fi
    if [[ -f "$PUB" ]]; then log "public key: ${PUB#"$RHUBARB_ROOT"/} sha256=$(shasum -a 256 "$PUB" | cut -c1-16)…"
    else log "public key: MISSING"; exit 3; fi
    ;;
  *) echo "usage: $0 init|status" >&2; exit 2 ;;
esac
