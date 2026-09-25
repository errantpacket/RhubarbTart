#!/bin/bash
# Runs inside the guest as root. Re-verifies every staged artifact (hash, Team ID,
# notarization) before installing, then records what was installed.
#
# usage: install.sh <stage-dir>
#   <stage-dir>/SHA256SUMS         sha256 manifest from the host lock
#   <stage-dir>/packages.tsv       id  kind  file  team_id  app
#   <stage-dir>/lock.json          the profile lock, copied into the image for audit

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

# Post-install: every installed bundle must still verify with its locked Team ID. The
# bundle name comes from config/packages/<id>.json ("app"); Perimeter 81's has varied
# across releases (Perimeter 81 / Harmony SASE), so it is found by pattern.
while IFS=$'\t' read -r id kind file team app; do
  [[ -z "$id" ]] && continue
  if [[ "$app" == "-" && "$id" == perimeter81 ]]; then
    app="$(find /Applications -maxdepth 1 \( -iname '*perimeter*81*.app' -o -iname '*harmony*sase*.app' \) \
             -exec basename {} \; | head -n1)"
  fi
  [[ -n "$app" && "$app" != "-" ]] || die "$id: installed app bundle unknown (set \"app\" in config/packages/$id.json)"
  [[ -d "/Applications/$app" ]] || die "$id: /Applications/$app missing after install"
  verify_app "$id" "/Applications/$app" "$team"
done < packages.tsv

# Vendor self-updaters would change the image after it was verified; updates are a
# re-resolve + rebuild. (Chrome's GoogleUpdater: see README "Known gaps".)
if launchctl print system/com.cloudflare.warp.updater >/dev/null 2>&1 || \
   [[ -e /Library/LaunchDaemons/com.cloudflare.warp.updater.plist ]]; then
  say "warp: disabling the WARP self-updater"
  launchctl bootout system/com.cloudflare.warp.updater 2>/dev/null || true
  launchctl disable system/com.cloudflare.warp.updater
fi

say "recording installed versions"
mkdir -p "$RECORD_DIR"
install -m 644 lock.json "$RECORD_DIR/lock.json"
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
