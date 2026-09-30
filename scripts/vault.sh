#!/bin/bash
# Sign / verify an evidence vault's root manifest with the publisher's cosign key (#86),
# reusing the same key and offline posture as image publishing (#32, scripts/signing-key.sh).
#
#   ./scripts/vault.sh sign   <root.json> <bundle-out>          # private key from the keychain
#   ./scripts/vault.sh verify <root.json> <bundle> [<pub-key>]  # public key (committed by default)
#
# Offline only: the signing-config names no CA, OIDC, transparency-log or timestamp server, and
# cosign's egress is pinned to a dead proxy so a signature can never reach a network service.
# The private key + passphrase live only in the login keychain (service RhubarbTart-signing) and
# reach cosign through the environment, never argv or a lasting file.

set -euo pipefail
# shellcheck source=scripts/env.sh
source "$(dirname "$0")/env.sh"

SERVICE=RhubarbTart-signing
SIGNING_CONFIG="$RHUBARB_ROOT/config/cosign/signing-config-offline.json"
DEFAULT_PUB="$RHUBARB_ROOT/config/keys/rhubarb-cosign.pub"
log() { echo "[vault] $*"; }
die() { echo "[vault] FAILED: $*" >&2; exit 1; }

# cosign may reach nothing: even offline signing must not phone home. A dead proxy for every
# scheme guarantees it (mirrors scripts/publish.sh).
cosign() { http_proxy=http://127.0.0.1:9 https_proxy=http://127.0.0.1:9 command cosign "$@"; }

case "${1:-}" in
  sign)
    [[ $# -eq 3 ]] || die "usage: $0 sign <root.json> <bundle-out>"
    root="$2"; bundle="$3"
    [[ -f "$root" ]] || die "no such file: $root"
    security find-generic-password -s "$SERVICE" -a cosign-key >/dev/null 2>&1 \
      || die "no signing key in the keychain ($SERVICE); run ./scripts/signing-key.sh init"
    # Secrets reach cosign only through its environment (never argv, never a file).
    COSIGN_PRIVATE_KEY="$(security find-generic-password -s "$SERVICE" -a cosign-key -w | xxd -r -p)"
    COSIGN_PASSWORD="$(security find-generic-password -s "$SERVICE" -a cosign-password -w)"
    export COSIGN_PRIVATE_KEY COSIGN_PASSWORD
    cosign sign-blob --key env://COSIGN_PRIVATE_KEY --signing-config "$SIGNING_CONFIG" \
      --bundle "$bundle" --yes "$root" >/dev/null 2>&1 || die "cosign sign-blob failed"
    log "signed $(basename "$root") -> $(basename "$bundle")"
    ;;
  verify)
    [[ $# -ge 3 ]] || die "usage: $0 verify <root.json> <bundle> [<pub-key>]"
    root="$2"; bundle="$3"; pub="${4:-$DEFAULT_PUB}"
    [[ -f "$root" ]] || die "no such file: $root"
    [[ -f "$bundle" ]] || die "no such bundle: $bundle"
    [[ -f "$pub" ]] || die "no such public key: $pub"
    cosign verify-blob --key "$pub" --insecure-ignore-tlog=true --bundle "$bundle" "$root" \
      >/dev/null 2>&1 || die "signature does NOT verify against $(basename "$pub")"
    log "signature verifies against $(basename "$pub")"
    ;;
  *) echo "usage: $0 sign|verify ..." >&2; exit 2 ;;
esac
