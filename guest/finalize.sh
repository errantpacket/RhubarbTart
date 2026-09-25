#!/bin/bash
# Runs inside the guest as root, as the LAST build step. Hardens the image,
# asserts the result, removes build inputs, then powers the guest off.
#
# usage: RB_USER=<admin> RB_SSH_FROM=<pattern|""> finalize.sh <stage-dir>
#   <stage-dir>/authorized_keys   public keys for RB_USER; empty => SSH disabled
#
# Ordering matters:
#   * `sshd -T` needs the host keys, so validation happens before they are removed.
#   * Packer's SSH session stays open while this runs; the new sshd policy only
#     applies to new connections, and services are disabled (not booted out) so
#     they stop at the next boot rather than cutting this session.

set -euo pipefail

STAGE="${1:?stage dir}"
: "${RB_USER:?}" "${RB_SSH_FROM?}"
USER_HOME="$(dscl . -read "/Users/$RB_USER" NFSHomeDirectory | awk '{print $2}')"
SSHD_DROPIN=/etc/ssh/sshd_config.d/010-rhubarb.conf
say() { echo "[finalize] $*"; }
die() { echo "[finalize] FAILED: $*" >&2; exit 1; }

# --- software update: keep Apple's security content flowing ---------------------
# Setup Assistant's "update automatically: off" also stops XProtect/RSR data.
# OS upgrades stay manual (a new IPSW => a new locked build).
say "enabling background security responses and system data files"
SU=/Library/Preferences/com.apple.SoftwareUpdate
defaults write "$SU" AutomaticCheckEnabled -bool true
defaults write "$SU" ConfigDataInstall -bool true
defaults write "$SU" CriticalUpdateInstall -bool true
defaults write "$SU" AutomaticallyInstallMacOSUpdates -bool false

# --- application firewall ----------------------------------------------------------
say "enabling application firewall + stealth mode"
FW=/usr/libexec/ApplicationFirewall/socketfilterfw
"$FW" --setglobalstate on >/dev/null
"$FW" --setstealthmode on >/dev/null

# --- Screen Sharing: not needed (Tart's window / Packer use host-side VNC) ---------
say "disabling Screen Sharing (effective next boot)"
launchctl disable system/com.apple.screensharing

# --- SSH -----------------------------------------------------------------------------
if [[ -s "$STAGE/authorized_keys" ]]; then
  say "configuring key-only SSH for $RB_USER"
  grep -Eq '^Include /etc/ssh/sshd_config\.d/\*' /etc/ssh/sshd_config \
    || die "sshd_config has no Include for sshd_config.d; drop-in would be ignored"

  # 010- sorts before Apple's 100-macos.conf; sshd uses the first value it reads.
  cat > "$SSHD_DROPIN" <<EOF
# RhubarbTart: key-only SSH for a single admin user.
PasswordAuthentication no
KbdInteractiveAuthentication no
AuthenticationMethods publickey
PermitRootLogin no
PermitEmptyPasswords no
AllowUsers $RB_USER
AllowAgentForwarding no
X11Forwarding no
PermitTunnel no
EOF
  chmod 644 "$SSHD_DROPIN"

  opts="no-agent-forwarding,no-X11-forwarding"
  [[ -n "$RB_SSH_FROM" ]] && opts="from=\"$RB_SSH_FROM\",$opts"
  install -d -m 700 -o "$RB_USER" -g staff "$USER_HOME/.ssh"
  grep -Ev '^[[:space:]]*(#|$)' "$STAGE/authorized_keys" | sed "s|^|$opts |" \
    > "$USER_HOME/.ssh/authorized_keys"
  chown "$RB_USER:staff" "$USER_HOME/.ssh/authorized_keys"
  chmod 600 "$USER_HOME/.ssh/authorized_keys"

  sshd -t || die "sshd config does not parse"
  effective="$(sshd -T)"
  for want in "passwordauthentication no" "kbdinteractiveauthentication no" \
              "authenticationmethods publickey" "permitrootlogin no" \
              "allowusers $RB_USER" "allowagentforwarding no"; do
    grep -qx "$want" <<<"$effective" || die "sshd -T missing '$want'"
  done
else
  say "no authorized_keys supplied: disabling Remote Login (effective next boot)"
  rm -f "$SSHD_DROPIN" "$USER_HOME/.ssh/authorized_keys"
  launchctl disable system/com.openssh.sshd
fi

# --- assertions: the promised posture --------------------------------------------------
say "asserting security posture"
csrutil status | grep -q 'status: enabled\.' || die "SIP is not enabled"
spctl --status | grep -qx 'assessments enabled' || die "Gatekeeper is not enabled"
"$FW" --getglobalstate | grep -q 'State = [12]' || die "firewall is not enabled"
"$FW" --getstealthmode | grep -Eiq 'stealth mode (is )?(on|enabled)' || die "stealth mode is off"
[[ ! -e /etc/kcpassword ]] || die "/etc/kcpassword present (auto-login)"
if defaults read /Library/Preferences/com.apple.loginwindow autoLoginUser >/dev/null 2>&1; then
  die "auto-login user is configured"
fi
[[ -z "$(ls -A /etc/sudoers.d 2>/dev/null)" ]] || die "/etc/sudoers.d is not empty"
if grep -Eq 'NOPASSWD' /etc/sudoers; then die "NOPASSWD entry in /etc/sudoers"; fi
launchctl print-disabled system | grep -Eq '"com.apple.screensharing" => (disabled|true)' \
  || die "Screen Sharing not disabled"

# --- remove build inputs and per-build state --------------------------------------------
say "removing build inputs, caches, histories, logs"
rm -rf "$STAGE"
rm -rf "$USER_HOME"/Library/Caches/* /Library/Caches/* 2>/dev/null || true
rm -f "$USER_HOME"/.zsh_history "$USER_HOME"/.bash_history /var/root/.zsh_history
rm -rf "$USER_HOME"/.zsh_sessions /var/root/.zsh_sessions
rm -rf /var/db/sudo/ts/* 2>/dev/null || true      # cached sudo credentials from the build
rm -f /tmp/script_*.sh 2>/dev/null || true        # Packer's uploaded provisioner scripts
log erase --all >/dev/null 2>&1 || true

# Every clone must get its own SSH host identity: sshd-keygen-wrapper regenerates
# missing keys at the next sshd start. Removed last; this session keeps its keys in memory.
say "removing SSH host keys (regenerated per clone on first boot)"
rm -f /etc/ssh/ssh_host_*

say "powering off"
( sleep 3; shutdown -h now ) >/dev/null 2>&1 &
exit 0
