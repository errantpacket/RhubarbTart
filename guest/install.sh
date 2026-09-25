#!/bin/bash
# Runs inside the guest as root. Re-verifies every staged artifact (hash, Team ID,
# notarization) before installing, then records what was installed.
#
# usage: install.sh <stage-dir>
#   <stage-dir>/SHA256SUMS         sha256 manifest from the host lock
#   <stage-dir>/packages.tsv       id  kind  file  team_id  app
#   <stage-dir>/sources.lock.json  copied into the image for audit

set -euo pipefail

STAGE="${1:?stage dir}"
RECORD_DIR=/Library/RhubarbTart
say() { echo "[guest-install] $*"; }  # not log(): that shadows macOS's log(1)
die() { echo "[guest-install] FAILED: $*" >&2; exit 1; }

cd "$STAGE"
say "verifying sha256 of staged artifacts"
shasum -a 256 -c SHA256SUMS || die "hash mismatch inside guest"

assert_team() { # <observed> <expected> <what>
  [[ "$1" == "$2" ]] || die "$3: Team ID '$1' != locked '$2'"
}

install_pkg() { # <id> <file> <team_id>
  local id=$1 file=$2 team=$3 sig observed
  sig=$(pkgutil --check-signature "$file") || die "$id: pkg signature invalid"
  grep -q "trusted by the Apple notary service" <<<"$sig" || die "$id: pkg not notarized"
  observed=$(grep -m1 'Developer ID Installer' <<<"$sig" | sed -E 's/.*\(([A-Z0-9]{10})\).*/\1/')
  assert_team "$observed" "$team" "$id"
  spctl --assess --type install "$file" || die "$id: Gatekeeper rejected pkg"
  say "$id: installing $file"
  installer -pkg "$file" -target /
}

verify_app() { # <id> <app-path> <team_id>
  local id=$1 app=$2 team=$3 observed
  codesign --verify --deep --strict "$app" || die "$id: codesign verify failed for $app"
  spctl --assess --type execute "$app" || die "$id: Gatekeeper rejected $app"
  observed=$(codesign -dv "$app" 2>&1 | sed -n 's/^TeamIdentifier=//p')
  assert_team "$observed" "$team" "$id"
}

install_dmg_app() { # <id> <file> <team_id> <app-name>
  local id=$1 file=$2 team=$3 app=$4 mnt
  mnt=$(mktemp -d)
  hdiutil attach -nobrowse -readonly -noautoopen -mountpoint "$mnt" "$file" >/dev/null
  verify_app "$id" "$mnt/$app" "$team"
  say "$id: copying $app to /Applications"
  rm -rf "/Applications/$app"
  ditto "$mnt/$app" "/Applications/$app"
  hdiutil detach "$mnt" >/dev/null
}

while IFS=$'\t' read -r id kind file team app; do
  [[ -z "$id" ]] && continue
  case "$kind" in
    pkg) install_pkg "$id" "$file" "$team" ;;
    dmg) install_dmg_app "$id" "$file" "$team" "$app" ;;
    *) die "$id: unknown kind '$kind'" ;;
  esac
done < packages.tsv

# Post-install: the installed bundles must still verify with the locked Team IDs.
while IFS=$'\t' read -r id kind file team app; do
  case "$id" in
    chrome) verify_app chrome "/Applications/Google Chrome.app" "$team" ;;
    zap) verify_app zap "/Applications/$app" "$team" ;;
    perimeter81)
      # Bundle name has varied across Perimeter 81 / Harmony SASE releases.
      p81=$(find /Applications -maxdepth 1 \( -iname '*perimeter*81*.app' -o -iname '*harmony*sase*.app' \) | head -n1)
      [[ -n "$p81" ]] || die "perimeter81: no app bundle found in /Applications after install"
      verify_app perimeter81 "$p81" "$team" ;;
  esac
done < packages.tsv

say "recording installed versions"
mkdir -p "$RECORD_DIR"
install -m 644 sources.lock.json "$RECORD_DIR/sources.lock.json"
{
  sw_vers
  for app in /Applications/*.app; do
    printf '%s\t%s\t%s\n' "$(basename "$app")" \
      "$(defaults read "$app/Contents/Info" CFBundleShortVersionString 2>/dev/null || echo '?')" \
      "$(codesign -dv "$app" 2>&1 | sed -n 's/^TeamIdentifier=//p')"
  done
} > "$RECORD_DIR/installed.txt"
chmod 644 "$RECORD_DIR/installed.txt"
cat "$RECORD_DIR/installed.txt"
