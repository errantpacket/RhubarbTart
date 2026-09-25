#!/bin/bash
# Install the pinned host toolchain (tart, packer + tart plugin, uv) into ./.toolchain
# from upstream release artifacts. No Homebrew, no system-wide changes, no sudo.
#
#   ./tools/bootstrap.sh
#
# Every download must match the SHA256 pinned in config/toolchain.env. Tart.app
# must additionally pass codesign + Gatekeeper notarization checks, and Team IDs
# are enforced once pinned. Runs with macOS's stock bash 3.2 and system tools only.

set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
PINS="$ROOT/config/toolchain.env"
TC="$ROOT/.toolchain"
DL="$TC/downloads"
log() { echo "[bootstrap] $*"; }
warn() { echo "[bootstrap] WARNING: $*" >&2; }
die() { echo "[bootstrap] FAILED: $*" >&2; exit 1; }
export CHECKPOINT_DISABLE=1   # no Packer version-check/telemetry calls to HashiCorp

[[ "$(uname -s)" == Darwin && "$(uname -m)" == arm64 ]] || die "needs macOS on Apple silicon"

# --- load pins: strict KEY=value parsing, never `source` ---------------------------
while IFS= read -r line || [[ -n "$line" ]]; do
  [[ "$line" =~ ^[[:space:]]*(#|$) ]] && continue
  [[ "$line" =~ ^([A-Z0-9_]+)=([A-Za-z0-9._:/+-]*)$ ]] || die "bad line in $PINS: $line"
  case "${BASH_REMATCH[1]}" in  # only known keys; never let the file set PATH, IFS, ...
    TART_VERSION|TART_URL|TART_SHA256|TART_TEAM_ID|\
    PACKER_VERSION|PACKER_URL|PACKER_SHA256|PACKER_TEAM_ID|\
    PACKER_PLUGIN_TART_VERSION|PACKER_PLUGIN_TART_URL|PACKER_PLUGIN_TART_SHA256|\
    UV_VERSION|UV_URL|UV_SHA256) ;;
    *) die "unknown key in $PINS: ${BASH_REMATCH[1]}" ;;
  esac
  printf -v "${BASH_REMATCH[1]}" '%s' "${BASH_REMATCH[2]}"
done < "$PINS"

for u in "$TART_URL" "$PACKER_URL" "$PACKER_PLUGIN_TART_URL" "$UV_URL"; do
  [[ "$u" != *..* ]] || die "URL contains '..': $u"
  case "$u" in
    https://github.com/openai/tart/releases/download/*|\
    https://releases.hashicorp.com/packer/*|\
    https://github.com/cirruslabs/packer-plugin-tart/releases/download/*|\
    https://github.com/astral-sh/uv/releases/download/*) ;;
    *) die "URL not on the allow-list: $u" ;;
  esac
done

mkdir -p "$DL" "$TC/bin" "$TC/packer-plugins"
WORK="$(mktemp -d)"
trap 'rm -rf "$WORK"' EXIT

fetch() { # <url> <sha256> -> prints local path of a verified download
  local url=$1 sha=$2 out
  out="$DL/$sha-$(basename "$url")"
  if [[ ! -f "$out" ]]; then
    curl --fail --silent --show-error --location --proto '=https' --tlsv1.2 \
      --retry 3 --output "$out.part" "$url" || die "download failed: $url"
    mv "$out.part" "$out"
  fi
  if ! echo "$sha  $out" | shasum -a 256 -c - >/dev/null 2>&1; then
    rm -f "$out"
    die "sha256 mismatch for $url"
  fi
  echo "$out"
}

team_of() { codesign -dv "$1" 2>&1 | sed -n 's/^TeamIdentifier=//p'; }

check_team() { # <name> <observed> <pinned>
  if [[ -z "$3" ]]; then
    warn "$1 signed by Team ID '${2:-none}'; confirm out-of-band and pin ${1}_TEAM_ID"
  elif [[ "$2" != "$3" ]]; then
    die "$1 Team ID '$2' != pinned '$3'"
  fi
}

# --- tart ------------------------------------------------------------------------
log "tart $TART_VERSION"
f="$(fetch "$TART_URL" "$TART_SHA256")"
tar -xzf "$f" -C "$WORK" tart.app
codesign --verify --deep --strict "$WORK/tart.app" || die "tart.app signature invalid"
spctl --assess --type execute -vv "$WORK/tart.app" 2>&1 | grep -q 'Notarized Developer ID' \
  || die "tart.app is not notarized"
check_team TART "$(team_of "$WORK/tart.app")" "$TART_TEAM_ID"
rm -rf "$TC/tart.app"
mv "$WORK/tart.app" "$TC/tart.app"
# exec wrapper, not a symlink: Tart resolves its bundle from the executable path.
rm -f "$TC/bin/tart"
cat > "$TC/bin/tart" <<'EOF'
#!/bin/sh
exec "$(cd "$(dirname "$0")/.." && pwd)/tart.app/Contents/MacOS/tart" "$@"
EOF
chmod 755 "$TC/bin/tart"
[[ "$("$TC/bin/tart" --version)" == "$TART_VERSION" ]] || die "tart reports an unexpected version"

# --- packer ----------------------------------------------------------------------
log "packer $PACKER_VERSION"
f="$(fetch "$PACKER_URL" "$PACKER_SHA256")"
unzip -oq "$f" packer -d "$WORK"
if codesign --verify --strict "$WORK/packer" 2>/dev/null; then
  check_team PACKER "$(team_of "$WORK/packer")" "$PACKER_TEAM_ID"
elif [[ -n "$PACKER_TEAM_ID" ]]; then
  die "packer is not code-signed but PACKER_TEAM_ID is pinned"
else
  warn "packer binary is not code-signed; relying on the pinned sha256 (GPG-verified at pin time)"
fi
install -m 755 "$WORK/packer" "$TC/bin/packer"
"$TC/bin/packer" version | grep -q "v$PACKER_VERSION" || die "packer reports an unexpected version"

# --- packer tart plugin (installed locally; `packer init` is never used) ------------
log "packer-plugin-tart $PACKER_PLUGIN_TART_VERSION"
f="$(fetch "$PACKER_PLUGIN_TART_URL" "$PACKER_PLUGIN_TART_SHA256")"
unzip -oq "$f" -d "$WORK/plugin"
plugin_bin="$(find "$WORK/plugin" -type f -name 'packer-plugin-tart_*_darwin_arm64')"
[[ -n "$plugin_bin" ]] || die "plugin binary not found in archive"
rm -rf "$TC/packer-plugins"
mkdir -p "$TC/packer-plugins"
CHECKPOINT_DISABLE=1 PACKER_PLUGIN_PATH="$TC/packer-plugins" \
  "$TC/bin/packer" plugins install --path "$plugin_bin" github.com/cirruslabs/tart
CHECKPOINT_DISABLE=1 PACKER_PLUGIN_PATH="$TC/packer-plugins" \
  "$TC/bin/packer" plugins installed | grep -q "v$PACKER_PLUGIN_TART_VERSION" \
  || die "plugin not registered at v$PACKER_PLUGIN_TART_VERSION"

# --- uv ------------------------------------------------------------------------------
log "uv $UV_VERSION"
f="$(fetch "$UV_URL" "$UV_SHA256")"
tar -xzf "$f" -C "$WORK"
install -m 755 "$WORK/uv-aarch64-apple-darwin/uv" "$WORK/uv-aarch64-apple-darwin/uvx" "$TC/bin/"
"$TC/bin/uv" --version | grep -q "$UV_VERSION" || die "uv reports an unexpected version"

# --- record what is installed (consumed by resolve.py provenance) ------------------------
{
  echo "# generated by tools/bootstrap.sh $(date -u +%Y-%m-%dT%H:%M:%SZ)"
  echo "pins_sha256 $(shasum -a 256 "$PINS" | cut -d' ' -f1)"
  echo "tart_team_id $(team_of "$TC/tart.app")"
  (cd "$TC" && shasum -a 256 tart.app/Contents/MacOS/tart bin/packer bin/uv bin/uvx \
    packer-plugins/github.com/cirruslabs/tart/packer-plugin-tart_*)
} > "$TC/INSTALLED"

log "done. Scripts put $TC/bin first on PATH via scripts/env.sh"
