#!/usr/bin/env bash
# Runs as root on Kali as the LAST build step: hardens, asserts, seals, rotates the
# bootstrap password to the final one, powers off. Mirrors guest/macos/finalize.sh.
#
# usage: RB_USER=<admin> RB_SSH_FROM=<pattern|""> finalize.sh <stage-dir>
#   <stage-dir>/authorized_keys   empty => SSH disabled
#   <stage-dir>/password          final password (0600), applied then shredded
#   <stage-dir>/profile.json      (tailscale in packages => allow its UDP port)

set -euo pipefail

STAGE="${1:?stage dir}"
: "${RB_USER:?}" "${RB_SSH_FROM?}"
USER_HOME="$(getent passwd "$RB_USER" | cut -d: -f6)"
SSHD_DROPIN=/etc/ssh/sshd_config.d/010-rhubarb.conf
say() { echo "[kali-finalize] $*"; }
die() { echo "[kali-finalize] FAILED: $*" >&2; exit 1; }
has_pkg() { python3 -c 'import json,sys; sys.exit(0 if sys.argv[2] in json.load(open(sys.argv[1]))["packages"] else 1)' "$STAGE/profile.json" "$1"; }

# --- firewall: inbound default-deny (nftables) ---------------------------------------
say "installing nftables inbound default-deny policy"
ssh_rule="" ts_rule=""
[[ -s "$STAGE/authorized_keys" ]] && ssh_rule="tcp dport 22 accept"
has_pkg tailscale && ts_rule="udp dport 41641 accept   # tailscale direct connections"
cat > /etc/nftables.conf <<EOF
#!/usr/sbin/nft -f
# RhubarbTart: drop unsolicited inbound traffic; outbound is unrestricted.
flush ruleset
table inet filter {
  chain input {
    type filter hook input priority 0; policy drop;
    iif lo accept
    ct state established,related accept
    ct state invalid drop
    meta l4proto ipv6-icmp accept
    ${ssh_rule}
    ${ts_rule}
  }
  chain forward { type filter hook forward priority 0; policy drop; }
  chain output { type filter hook output priority 0; policy accept; }
}
EOF
nft -c -f /etc/nftables.conf || die "nftables ruleset does not parse"
systemctl enable nftables.service >/dev/null

# --- SSH ---------------------------------------------------------------------------------
if [[ -s "$STAGE/authorized_keys" ]]; then
  say "configuring key-only SSH for $RB_USER"
  grep -Eq '^Include /etc/ssh/sshd_config\.d/\*' /etc/ssh/sshd_config \
    || die "sshd_config has no Include for sshd_config.d; drop-in would be ignored"
  cat > "$SSHD_DROPIN" <<EOF
# RhubarbTart: key-only SSH for a single admin user (first value wins in sshd).
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
  install -d -m 700 -o "$RB_USER" -g "$RB_USER" "$USER_HOME/.ssh"
  grep -Ev '^[[:space:]]*(#|$)' "$STAGE/authorized_keys" | sed "s|^|$opts |" > "$USER_HOME/.ssh/authorized_keys"
  chown "$RB_USER:$RB_USER" "$USER_HOME/.ssh/authorized_keys"
  chmod 600 "$USER_HOME/.ssh/authorized_keys"
  sshd -t || die "sshd config does not parse"
  effective="$(sshd -T -C user="$RB_USER",host=rhubarb,addr=192.168.64.1)"
  for want in "passwordauthentication no" "kbdinteractiveauthentication no" \
              "authenticationmethods publickey" "permitrootlogin no" \
              "allowusers $RB_USER" "allowagentforwarding no"; do
    grep -qx "$want" <<<"$effective" || die "sshd -T missing '$want'"
  done
  # Debian doesn't recreate deleted host keys by itself; do it once per clone.
  cat > /etc/systemd/system/rhubarb-ssh-hostkeys.service <<'EOF'
[Unit]
Description=Generate per-clone SSH host keys (RhubarbTart)
Before=ssh.service
ConditionPathExists=!/etc/ssh/ssh_host_ed25519_key

[Service]
Type=oneshot
ExecStart=/usr/bin/ssh-keygen -A

[Install]
WantedBy=multi-user.target
RequiredBy=ssh.service
EOF
  systemctl enable rhubarb-ssh-hostkeys.service >/dev/null
else
  say "no authorized_keys supplied: disabling sshd"
  rm -f "$SSHD_DROPIN" "$USER_HOME/.ssh/authorized_keys"
  systemctl disable ssh.service ssh.socket >/dev/null 2>&1 || true
fi

# --- assertions --------------------------------------------------------------------------
say "asserting security posture"
if grep -rEqs '^[^#]*NOPASSWD' /etc/sudoers /etc/sudoers.d; then die "NOPASSWD in sudoers"; fi
if dpkg -s kali-grant-root >/dev/null 2>&1; then die "kali-grant-root (passwordless root) installed"; fi
if grep -rEqs '^[[:space:]]*autologin-user[[:space:]]*=[[:space:]]*[^[:space:]]' /etc/lightdm; then
  die "LightDM auto-login configured"
fi
if getent shadow root | cut -d: -f2 | grep -Eq '^[^!*]'; then die "root has a usable password"; fi
systemctl is-enabled nftables.service >/dev/null || die "nftables not enabled"

# --- VPN identity: never baked (scripts/enroll.sh enrolls each clone) --------------------
say "removing VPN runtime state"
systemctl stop tailscaled.service warp-svc.service 2>/dev/null || true
rm -rf /var/lib/tailscale/* /var/lib/cloudflare-warp/* 2>/dev/null || true

# --- residue + identity ------------------------------------------------------------------
say "removing build residue and machine identity"
apt-get clean
rm -rf /var/log/installer /var/lib/apt/lists/* /root/.bash_history "$USER_HOME/.bash_history" \
       "$USER_HOME/.zsh_history" /root/.zsh_history /tmp/script_*.sh
journalctl --rotate >/dev/null 2>&1 && journalctl --vacuum-time=1s >/dev/null 2>&1 || true
: > /etc/machine-id
rm -f /var/lib/dbus/machine-id
rm -f /etc/ssh/ssh_host_*

# --- rotate bootstrap -> final password (last: nothing below needs sudo) ----------------
say "rotating the bootstrap password"
[[ -s "$STAGE/password" ]] || die "no final password staged"
printf '%s:%s\n' "$RB_USER" "$(cat "$STAGE/password")" | chpasswd   # printf is a builtin: no argv
hash="$(getent shadow "$RB_USER" | cut -d: -f2)"
# perl reads the password from the file itself, so it never appears in argv
perl -e 'open(my $f, "<", $ARGV[0]) or exit 2; my $p = <$f>; chomp $p;
         exit(crypt($p, $ARGV[1]) eq $ARGV[1] ? 0 : 1)' -- "$STAGE/password" "$hash" \
  || die "final password does not verify"
shred -u "$STAGE/password" 2>/dev/null || rm -f "$STAGE/password"
rm -rf "$STAGE"

say "powering off"
( sleep 3; systemctl poweroff ) >/dev/null 2>&1 &
exit 0
