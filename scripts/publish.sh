#!/bin/bash
# DRAFT — push a built VM to an OCI registry and sign it + attach provenance with cosign.
#
#   ./scripts/publish.sh rbt-tahoe-research-0123456789ab ghcr.io/errantpacket/rhubarb-tahoe-research
#
# Requires: tart, cosign, crane (for digest lookup). Consumers should verify with
#   cosign verify <ref>@<digest> --certificate-identity ... --certificate-oidc-issuer ...
#   cosign verify-attestation --type custom <ref>@<digest> ...
# and pull BY DIGEST: tart clone <ref>@sha256:... local-name

set -euo pipefail
# shellcheck source=scripts/env.sh
source "$(dirname "$0")/env.sh"

VM="${1:?vm name}"
REPO="${2:?registry/repository}"
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
PROV="$ROOT/out/$VM.provenance.json"
[[ -f "$PROV" ]] || { echo "missing $PROV; build first" >&2; exit 1; }

TAG="${VM#rbt-}"
tart push "$VM" "$REPO:$TAG"
DIGEST="$(crane digest "$REPO:$TAG")"
REF="$REPO@$DIGEST"

cosign sign --yes "$REF"
cosign attest --yes --type custom --predicate "$PROV" "$REF"

echo "published $REF"
