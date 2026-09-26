#!/usr/bin/env bash
# Runs as root in the live ISO after install.sh, as the LAST build step. Asserts the
# posture of the *built* system (it has never booted), seals it, powers off.
#
# NixOS declares the posture (nix/modules/hardening.nix); these checks prove the
# declaration made it into the system that will boot.

set -euo pipefail

say() { echo "[nixos-finalize] $*"; }
die() { echo "[nixos-finalize] FAILED: $*" >&2; exit 1; }

# The system profile symlink stores an absolute /nix/store/... target; on the live ISO the
# physical copy lives under /mnt. Rebase it there to confirm the install produced a system.
SYSDIR="/mnt$(readlink -f /mnt/nix/var/nix/profiles/system)"
[[ -d "$SYSDIR" ]] || die "no built system at /mnt"

say "asserting security posture of the built system"
# The built /etc is a tree of symlinks into /nix/store (absolute), so it only resolves with the
# installed store mounted as /nix/store — i.e. inside the target. Run the assertions under
# nixos-enter, which chroots into /mnt; the system has not booted. Everything the checks read
# (the profile's etc/sw, /etc/nixos, /var/lib/rhubarbtart) is under /mnt, hence visible in there.
mkdir -p /mnt/root
cat > /mnt/root/.rbt-check.sh <<'CHECK'
#!/usr/bin/env bash
set -euo pipefail
die() { echo "[nixos-finalize] FAILED: $*" >&2; exit 1; }
SYS=/nix/var/nix/profiles/system
if [[ -e "$SYS/etc/ssh/sshd_config" ]]; then
  cfg="$(cat "$SYS/etc/ssh/sshd_config")"
  for want in "PasswordAuthentication no" "KbdInteractiveAuthentication no" \
              "AuthenticationMethods publickey" "PermitRootLogin no" "AllowAgentForwarding no"; do
    grep -qx "$want" <<<"$cfg" || die "sshd_config missing '$want'"
  done
  [[ -s /etc/nixos/authorized_keys ]] || die "sshd enabled without authorized keys"
else
  [[ ! -s /etc/nixos/authorized_keys ]] || die "keys supplied but sshd not enabled"
  echo "[nixos-finalize] no sshd in the built system (no keys supplied)"
fi
if grep -rqs 'NOPASSWD' "$SYS/etc/sudoers"; then die "NOPASSWD in sudoers"; fi
grep -q 'Defaults' "$SYS/etc/sudoers" || die "no sudoers generated"
if grep -rEqs '^[[:space:]]*autologin-user[[:space:]]*=[[:space:]]*[^[:space:]]' "$SYS/etc/lightdm"; then
  die "LightDM auto-login configured"
fi
[[ -s /var/lib/rhubarbtart/password.hash ]] || die "no password hash"
[[ "$(stat -c %a /var/lib/rhubarbtart/password.hash)" == 600 ]] || die "password hash not 0600"
[[ -e "$SYS/sw/bin/nft" || -e "$SYS/sw/bin/iptables" ]] || die "no firewall tooling in system"
CHECK
chmod 700 /mnt/root/.rbt-check.sh
nixos-enter --root /mnt -c 'bash /root/.rbt-check.sh' || die "posture assertion failed"
rm -f /mnt/root/.rbt-check.sh

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
