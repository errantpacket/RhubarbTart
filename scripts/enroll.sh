#!/bin/bash
# Enroll a running clone in a VPN/ZTNA service. Images never contain VPN identity;
# each clone is enrolled here, at runtime, with secrets from the host keychain.
#
#   ./scripts/enroll.sh <vm> tailscale [--image <name>]
#   ./scripts/enroll.sh <vm> warp --org <team-name> [--image <name>]
#   ./scripts/enroll.sh <vm> perimeter81        (prints the manual steps)
#
# <vm> is the running clone to enroll. Its sudo password is looked up in the keychain under
# --image (the built image it was cloned from, e.g. rbt-kali-research-<sha>), or under <vm>.
#
# Secrets (store once; `security add-generic-password ... -w` prompts, so nothing hits argv):
#   security add-generic-password -s RhubarbTart-enroll -a tailscale-authkey -w
#       -> prefer a one-off, pre-approved, *tagged* key; ephemeral for throwaway clones
#   security add-generic-password -s RhubarbTart-enroll -a warp-client-id -w
#   security add-generic-password -s RhubarbTart-enroll -a warp-client-secret -w
#       -> a Cloudflare Access service token allowed to enroll devices
#
# Transport: the VM's sudo password (keychain, service RhubarbTart) and the secret go over
# SSH stdin; in the guest they land in a 0600 temp file that is shredded after use.

set -euo pipefail
# shellcheck source=scripts/env.sh
source "$(dirname "$0")/env.sh"

VM="${1:?usage: enroll.sh <vm> tailscale|warp|perimeter81 [--org TEAM] [--image NAME]}"
SERVICE="${2:?service: tailscale|warp|perimeter81}"
shift 2
ORG=""
IMAGE="$VM"
while (($#)); do
  case "$1" in
    --org) ORG="${2:?--org needs a value}"; shift 2 ;;
    --image) IMAGE="${2:?--image needs a value}"; shift 2 ;;
    *) echo "unknown option $1" >&2; exit 2 ;;
  esac
done
die() { echo "[enroll] FAILED: $*" >&2; exit 1; }
secret() { security find-generic-password -s RhubarbTart-enroll -a "$1" -w 2>/dev/null \
             || die "no keychain item RhubarbTart-enroll/$1 (see header of this script)"; }

if [[ "$SERVICE" == perimeter81 ]]; then
  cat <<'EOF'
Perimeter 81 / Harmony SASE enrollment is tied to your tenant:
  1. tart run <vm>, log in, open the Perimeter 81 app.
  2. Approve its system/network extensions when macOS asks
     (System Settings > General > Login Items & Extensions).
  3. Sign in with your workspace; never do this in an image you will clone.
EOF
  exit 0
fi

VM_PW="$(security find-generic-password -s RhubarbTart -a "$IMAGE" -w 2>/dev/null)" \
  || die "no keychain password for $IMAGE (for a clone, pass --image <the image it was cloned from>)"
[[ "$ORG" =~ ^[A-Za-z0-9._-]*$ ]] || die "--org has unexpected characters"

case "$SERVICE" in
  tailscale) PAYLOAD="$(secret tailscale-authkey)" ;;
  warp)
    [[ -n "$ORG" ]] || die "warp needs --org <team-name>"
    PAYLOAD="$(secret warp-client-id)"$'\n'"$(secret warp-client-secret)" ;;
  *) die "unknown service $SERVICE" ;;
esac

# Root-side script, fed to `sudo -S bash -s` right after the password line.
# F = 0600 temp file holding the secret(s); removed as soon as they are read.
read -r -d '' ROOT_SCRIPT <<'ROOT' || true
set -euo pipefail
wipe() { shred -u "$1" 2>/dev/null || rm -P "$1" 2>/dev/null || rm -f "$1"; }
case "$(uname -s)/$SERVICE" in
  Linux/tailscale)
    tailscale up --auth-key="file:$F"; wipe "$F"
    tailscale status | head -3 ;;
  Darwin/tailscale)
    # Build the policy plist with printf (a builtin): the key never appears in argv.
    k="$(cat "$F")"; wipe "$F"
    umask 077; t="$(mktemp)"
    printf '<?xml version="1.0" encoding="UTF-8"?>\n<plist version="1.0"><dict><key>AuthKey</key><string>%s</string></dict></plist>\n' "$k" > "$t"
    defaults import /Library/Preferences/io.tailscale.ipn.macsys "$t"; wipe "$t"
    echo "AuthKey policy set. In the VM: open Tailscale.app and approve its system extension."
    echo "After it connects: sudo defaults delete /Library/Preferences/io.tailscale.ipn.macsys AuthKey" ;;
  */warp)
    id="$(sed -n 1p "$F")"; sec="$(sed -n 2p "$F")"; wipe "$F"
    if [ "$(uname -s)" = Darwin ]; then dir="/Library/Application Support/Cloudflare"; else dir=/var/lib/cloudflare-warp; fi
    mkdir -p "$dir"; umask 077
    printf '<dict>\n  <key>organization</key><string>%s</string>\n  <key>auth_client_id</key><string>%s</string>\n  <key>auth_client_secret</key><string>%s</string>\n  <key>service_mode</key><string>warp</string>\n  <key>onboarding</key><false/>\n  <key>allow_managed_deployments</key><false/>\n</dict>\n' \
      "$ORG" "$id" "$sec" > "$dir/mdm.xml"
    chmod 600 "$dir/mdm.xml"
    if [ "$(uname -s)" = Darwin ]; then launchctl kickstart -k system/com.cloudflare.1dot1dot1dot1.macos.warp.daemon
    else systemctl restart warp-svc; fi
    echo "WARP enrollment config written; the client registers with the service token." ;;
  *) echo "unsupported: $(uname -s)/$SERVICE" >&2; exit 1 ;;
esac
ROOT

# User-side script: stdin = sudo password line, then the payload.
read -r -d '' USER_SCRIPT <<'USER' || true
set -euo pipefail
IFS= read -r PW
umask 077; F="$(mktemp)"; cat > "$F"
ROOT_SCRIPT="$(printf '%s' "$ROOT_B64" | base64 --decode)"
{ printf '%s\n' "$PW"; printf '%s\n' "$ROOT_SCRIPT"; } \
  | sudo -S -p '' /usr/bin/env F="$F" ORG="$ORG" SERVICE="$SERVICE" bash -s
USER

b64() { printf '%s' "$1" | base64 | tr -d '\n'; }
IP="$(tart ip --wait 60 "$VM" 2>/dev/null || tart ip --wait 60 --resolver arp "$VM")"
printf '%s\n%s\n' "$VM_PW" "$PAYLOAD" | ssh \
  -o HostKeyAlias="$VM" -o UserKnownHostsFile="$HOME/.ssh/known_hosts_rhubarbtart" \
  -o StrictHostKeyChecking=accept-new -o ForwardAgent=no \
  "${RHUBARB_USER:-admin}@$IP" \
  "SERVICE=$SERVICE ORG=$ORG ROOT_B64=$(b64 "$ROOT_SCRIPT") bash -c \"\$(printf '%s' $(b64 "$USER_SCRIPT") | base64 --decode)\""
