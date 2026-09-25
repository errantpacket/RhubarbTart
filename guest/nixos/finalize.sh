#!/usr/bin/env bash
# Runs as root in the live ISO after install.sh, as the LAST build step. Asserts the
# posture of the *built* system (it has never booted), seals it, powers off.
#
# NixOS declares the posture (nix/modules/hardening.nix); these checks prove the
# declaration made it into the system that will boot.

set -euo pipefail

say() { echo "[nixos-finalize] $*"; }
die() { echo "[nixos-finalize] FAILED: $*" >&2; exit 1; }

SYSTEM="$(readlink -f /mnt/nix/var/nix/profiles/system)"
ETC="$SYSTEM/etc"
[[ -d "$ETC" ]] || die "no built system at /mnt"

say "asserting security posture of the built system"
if [[ -e "$ETC/ssh/sshd_config" ]]; then
  cfg="$(cat "$ETC/ssh/sshd_config")"
  for want in "PasswordAuthentication no" "KbdInteractiveAuthentication no" \
              "AuthenticationMethods publickey" "PermitRootLogin no" "AllowAgentForwarding no"; do
    grep -qx "$want" <<<"$cfg" || die "sshd_config missing '$want'"
  done
  [[ -s /mnt/etc/nixos/authorized_keys ]] || die "sshd enabled without authorized keys"
else
  [[ ! -s /mnt/etc/nixos/authorized_keys ]] || die "keys supplied but sshd not enabled"
  say "no sshd in the built system (no keys supplied)"
fi
if grep -rqs 'NOPASSWD' "$ETC/sudoers"; then die "NOPASSWD in sudoers"; fi
grep -q 'Defaults' "$ETC/sudoers" || die "no sudoers generated"
if grep -rEqs '^[[:space:]]*autologin-user[[:space:]]*=[[:space:]]*[^[:space:]]' "$ETC/lightdm"; then
  die "LightDM auto-login configured"
fi
[[ -s /mnt/var/lib/rhubarbtart/password.hash ]] || die "no password hash"
[[ "$(stat -c %a /mnt/var/lib/rhubarbtart/password.hash)" == 600 ]] || die "password hash not 0600"
[[ -e "$SYSTEM/sw/bin/nft" || -e "$SYSTEM/sw/bin/iptables" ]] || die "no firewall tooling in system"

say "sealing: no machine identity, no host keys, no VPN state, no build residue"
rm -f /mnt/etc/machine-id /mnt/var/lib/dbus/machine-id
rm -f /mnt/etc/ssh/ssh_host_*
rm -rf /mnt/var/lib/tailscale /mnt/var/lib/cloudflare-warp
rm -rf /mnt/root/.cache /mnt/root/.nix-defexpr /mnt/root/.nix-profile

sync
umount -R /mnt
say "powering off"
( sleep 3; systemctl poweroff ) >/dev/null 2>&1 &
exit 0
