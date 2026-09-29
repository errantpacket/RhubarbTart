#!/bin/bash
# Publish a verified image to an OCI registry, signed and provenance-attested with this
# publisher's cosign key, and verify published images (#32).
#
#   ./scripts/publish.sh publish rbt-<profile>-<inputs-sha12>
#   ./scripts/publish.sh verify  <registry>/rhubarbtart/<profile>@sha256:<digest>
#
# RHUBARB_REGISTRY  registry host[:port] (default: the local one from scripts/registry.sh,
#                   127.0.0.1:${RHUBARB_REGISTRY_PORT:-5780}). Localhost is plain HTTP.
#
# Signing is offline: key + passphrase come from the keychain (scripts/signing-key.sh) via env
# only, and config/cosign/signing-config-offline.json names no CA, OIDC, transparency-log or
# timestamp service, so nothing about these images is sent to public Sigstore services.
# Verification uses the committed public key, config/keys/rhubarb-cosign.pub. Always pull by
# digest; a tag is only a pointer.

set -euo pipefail
# shellcheck source=scripts/env.sh
source "$(dirname "$0")/env.sh"

REG="${RHUBARB_REGISTRY:-127.0.0.1:${RHUBARB_REGISTRY_PORT:-5780}}"
PUB="${RHUBARB_COSIGN_PUB:-$RHUBARB_ROOT/config/keys/rhubarb-cosign.pub}"   # override: another publisher's key
SIGNING_CONFIG="$RHUBARB_ROOT/config/cosign/signing-config-offline.json"
SERVICE=RhubarbTart-signing
log() { echo "[publish] $*"; }
die() { echo "[publish] FAILED: $*" >&2; exit 1; }

[[ "$REG" =~ ^[A-Za-z0-9.-]+(:[0-9]{2,5})?$ ]] || die "bad RHUBARB_REGISTRY '$REG'"
TART_NET=() COSIGN_NET=() SCHEME=https
if [[ "$REG" == 127.0.0.1:* || "$REG" == localhost:* ]]; then
  TART_NET=(--insecure) COSIGN_NET=(--allow-http-registry) SCHEME=http
fi
# cosign may talk to the registry and nothing else: even with the offline signing config it
# tries to fetch Sigstore's public TUF trust root. Route every other host to a dead proxy so no
# public service is contacted (it then warns "Could not fetch trusted_root.json" and continues).
cosign() {
  local host="${REG%%:*}"
  env HTTPS_PROXY=http://127.0.0.1:9 HTTP_PROXY=http://127.0.0.1:9 https_proxy=http://127.0.0.1:9 \
      http_proxy=http://127.0.0.1:9 NO_PROXY="$host" no_proxy="$host" command cosign "$@"
}

verify_ref() { # <ref@digest>: signature + provenance attestation against the committed key
  local ref=$1
  [[ "$ref" =~ ^[A-Za-z0-9.:-]+/rhubarbtart/[a-z0-9-]+@sha256:[0-9a-f]{64}$ ]] \
    || die "verify needs <registry>/rhubarbtart/<profile>@sha256:<digest>, got '$ref'"
  [[ -f "$PUB" ]] || die "missing $PUB"
  cosign verify --key "$PUB" --insecure-ignore-tlog=true ${COSIGN_NET[@]+"${COSIGN_NET[@]}"} \
    "$ref" >/dev/null 2>&1 || die "signature does not verify against ${PUB#"$RHUBARB_ROOT"/}: $ref"
  cosign verify-attestation --key "$PUB" --type custom --insecure-ignore-tlog=true \
    ${COSIGN_NET[@]+"${COSIGN_NET[@]}"} "$ref" >/dev/null 2>&1 \
    || die "provenance attestation does not verify: $ref"
  log "verified: $ref (signature + provenance, key ${PUB#"$RHUBARB_ROOT"/})"
}

case "${1:-}" in
  publish)
    VM="${2:?usage: $0 publish rbt-<profile>-<inputs-sha12>}"
    [[ "$VM" =~ ^rbt-([a-z0-9-]+)-([0-9a-f]{12})$ ]] \
      || die "'$VM' is not a verified image name (rbt-<profile>-<sha12>; never -unverified)"
    PROFILE="${BASH_REMATCH[1]}" TAG="${BASH_REMATCH[2]}"
    PROV="$RHUBARB_ROOT/out/$VM.provenance.json"
    [[ -f "$PROV" ]] || die "missing ${PROV#"$RHUBARB_ROOT"/}: only built, smoke-passed images are published"
    [[ "$(plutil -extract vm raw -o - "$PROV")" == "$VM" ]] || die "provenance record is not for $VM"
    tart get "$VM" >/dev/null 2>&1 || die "$VM is not a local VM"
    security find-generic-password -s "$SERVICE" -a cosign-key >/dev/null 2>&1 \
      || die "no signing key in the keychain (./scripts/signing-key.sh init)"
    curl --silent --max-time 5 -o /dev/null "$SCHEME://$REG/v2/" \
      || die "registry $REG is not reachable (local: ./scripts/registry.sh start)"

    REPO="$REG/rhubarbtart/$PROFILE"
    log "pushing $VM -> $REPO:$TAG"
    tart push ${TART_NET[@]+"${TART_NET[@]}"} "$VM" "$REPO:$TAG"
    DIGEST="$(curl --silent --fail --max-time 30 --head \
      -H 'Accept: application/vnd.oci.image.manifest.v1+json' \
      "$SCHEME://$REG/v2/rhubarbtart/$PROFILE/manifests/$TAG" \
      | tr -d '\r' | awk -F': ' 'tolower($1)=="docker-content-digest"{print $2}')"
    [[ "$DIGEST" =~ ^sha256:[0-9a-f]{64}$ ]] || die "could not read the pushed manifest digest"
    REF="$REPO@$DIGEST"

    # Secrets reach cosign only through its environment (never argv, never a file).
    COSIGN_PRIVATE_KEY="$(security find-generic-password -s "$SERVICE" -a cosign-key -w | xxd -r -p)"
    COSIGN_PASSWORD="$(security find-generic-password -s "$SERVICE" -a cosign-password -w)"
    export COSIGN_PRIVATE_KEY COSIGN_PASSWORD
    common=(--key env://COSIGN_PRIVATE_KEY --signing-config "$SIGNING_CONFIG" --yes
            ${COSIGN_NET[@]+"${COSIGN_NET[@]}"})
    log "signing $REF"
    cosign sign "${common[@]}" "$REF" >/dev/null
    log "attesting provenance"
    cosign attest "${common[@]}" --type custom --predicate "$PROV" "$REF" >/dev/null
    unset COSIGN_PRIVATE_KEY COSIGN_PASSWORD

    verify_ref "$REF"
    printf '{\n  "image": "%s",\n  "ref": "%s",\n  "digest": "%s",\n  "registry": "%s",\n  "published_at": "%s",\n  "cosign_pub_sha256": "%s"\n}\n' \
      "$VM" "$REF" "$DIGEST" "$REG" "$(date -u +%Y-%m-%dT%H:%M:%SZ)" "$(shasum -a 256 "$PUB" | cut -d' ' -f1)" \
      > "$RHUBARB_ROOT/out/$VM.published.json"
    log "published $REF (record: out/$VM.published.json)"
    ;;
  verify)
    verify_ref "${2:?usage: $0 verify <registry>/rhubarbtart/<profile>@sha256:<digest>}"
    ;;
  *) echo "usage: $0 publish <rbt-image> | verify <ref@digest>" >&2; exit 2 ;;
esac
