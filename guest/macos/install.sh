#!/bin/bash
# Runs inside the guest as root. Re-verifies every staged artifact (hash, Team ID,
# notarization) before installing, then records what was installed.
#
# usage: install.sh <stage-dir>
#   <stage-dir>/SHA256SUMS         sha256 manifest from the host lock
#   <stage-dir>/packages.tsv       id  kind  file  team_id  app  signed  (read with a trailing
#                                  _more so a future column can't leak into signed; #26)
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

install_pkg() { # <id> <file> <team_id> <app>
  local id=$1 file=$2 team=$3 app=$4 sig observed
  sig=$(pkgutil --check-signature "$file") || die "$id: pkg signature invalid"
  grep -q "trusted by the Apple notary service" <<<"$sig" || die "$id: pkg not notarized"
  observed=$(grep -m1 'Developer ID Installer' <<<"$sig" | sed -E 's/.*\(([A-Z0-9]{10})\).*/\1/')
  assert_team "$observed" "$team" "$id"
  spctl --assess --type install "$file" || die "$id: Gatekeeper rejected pkg"
  say "$id: installing $file"
  # A vendor postinstall may end by launching the GUI app (Tailscale runs `open -a Tailscale.app`),
  # which fails in the headless build (no Aqua session) and fails the whole installer even though
  # the payload is already extracted. installer resets PATH, so `open` can't be shimmed. Tolerate a
  # non-zero exit ONLY when the app bundle actually installed; the post-install loop below then
  # re-verifies its presence, signature and Team ID, which is the real gate. A preinstall failure
  # (no payload) leaves no bundle and still dies here.
  if installer -pkg "$file" -target /; then
    return 0
  fi
  [[ -n "$app" && "$app" != "-" && -d "/Applications/$app" ]] \
    || die "$id: installer failed and /Applications/$app is not present"
  say "$id: installer postinstall returned non-zero; payload present, continuing (re-verified below)"
}

verify_app() { # <id> <app-path> <team_id>
  local id=$1 app=$2 team=$3 observed
  codesign --verify --deep --strict "$app" || die "$id: codesign verify failed for $app"
  spctl --assess --type execute "$app" || die "$id: Gatekeeper rejected $app"
  observed=$(codesign -dv "$app" 2>&1 | sed -n 's/^TeamIdentifier=//p')
  assert_team "$observed" "$team" "$id"
}

# The disk image currently attached, if any. The EXIT trap ejects it and removes its mount
# folder, so a failed check (set -e exits through die) never leaves an image mounted.
MOUNTED=""
eject_dmg() {
  [[ -n "$MOUNTED" ]] || return 0
  diskutil eject "$MOUNTED" >/dev/null 2>&1 || true
  rmdir "$MOUNTED" 2>/dev/null || true
  MOUNTED=""
}
trap eject_dmg EXIT

install_dmg_app() { # <id> <file> <team_id> <app-name> <signed>
  local id=$1 file=$2 team=$3 app=$4 signed=$5
  # `diskutil image` replaces the deprecated `hdiutil attach` (macOS 26 and later).
  MOUNTED=$(mktemp -d)
  diskutil image attach --mountOptions nobrowse --readOnly --mountPoint "$MOUNTED" "$file" \
    >/dev/null || die "$id: could not attach $file"
  # signed=0: the app ships no Apple signature; its integrity is the pinned sha256 already
  # checked against SHA256SUMS above. Signed apps additionally get codesign/Team ID/Gatekeeper.
  if [[ "$signed" == 0 ]]; then
    say "$id: unsigned, integrity from the pinned sha256 (no codesign)"
  else
    verify_app "$id" "$MOUNTED/$app" "$team"
  fi
  say "$id: copying $app to /Applications"
  rm -rf "/Applications/$app"
  ditto "$MOUNTED/$app" "/Applications/$app"
  eject_dmg
}

while IFS=$'\t' read -r id kind file team app signed _more; do
  [[ -z "$id" ]] && continue
  case "$kind" in
    pkg) install_pkg "$id" "$file" "$team" "$app" ;;
    dmg) install_dmg_app "$id" "$file" "$team" "$app" "$signed" ;;
    *) die "$id: unknown kind '$kind'" ;;
  esac
done < packages.tsv

# Post-install: every installed bundle must still verify with its locked Team ID. The
# bundle name comes from config/packages/<id>.json ("app").
while IFS=$'\t' read -r id kind file team app signed _more; do
  [[ -z "$id" ]] && continue
  [[ -n "$app" && "$app" != "-" ]] || die "$id: installed app bundle unknown (set \"app\" in config/packages/$id.json)"
  [[ -d "/Applications/$app" ]] || die "$id: /Applications/$app missing after install"
  if [[ "$signed" == 0 ]]; then
    say "$id: installed (unsigned; integrity from pinned sha256)"
  else
    verify_app "$id" "/Applications/$app" "$team"
  fi
done < packages.tsv

# Vendor self-updaters would change the image after it was verified; updates are a
# re-resolve + rebuild.
# Chrome's GoogleUpdater re-registers itself whenever Chrome runs, so it is governed by Google's
# documented update policy instead (support.google.com/chrome/a/answer/7591084), delivered as a
# managed preference. UpdateDefault 2 = never auto-apply; a user can still update deliberately
# (Chrome > About) for an urgent fix. Owner decision, #29.
KEYSTONE_POLICY="/Library/Managed Preferences/com.google.Keystone.plist"
if [[ -d "/Applications/Google Chrome.app" ]]; then
  say "chrome: auto-updates off (manual only) via managed Keystone policy"
  install -d -m 755 -o root -g wheel "/Library/Managed Preferences"
  cat > "$KEYSTONE_POLICY" <<'EOF'
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>updatePolicies</key>
  <dict>
    <key>global</key>
    <dict><key>UpdateDefault</key><integer>2</integer></dict>
    <key>com.google.Chrome</key>
    <dict><key>UpdateDefault</key><integer>2</integer></dict>
  </dict>
</dict>
</plist>
EOF
  chown root:wheel "$KEYSTONE_POLICY"
  chmod 644 "$KEYSTONE_POLICY"
  plutil -lint "$KEYSTONE_POLICY" >/dev/null || die "chrome: update policy plist does not parse"
fi
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
