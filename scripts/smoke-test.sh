#!/bin/bash
# Prove the hardening of a built image from the OUTSIDE, on a throwaway clone
# (the image itself is never booted, so it stays exactly as built).
#
#   RHUBARB_FAMILY=macos|nixos|kali RHUBARB_SSH_ENABLED=1|0 ./scripts/smoke-test.sh <vm>
#   (scripts/build.sh sets these; RHUBARB_USER and RHUBARB_ROSETTA=true are optional)
#   RHUBARB_OPEN_PORTS="3000 8080" (optional): ports a package's service declares; each must be reachable
#
# With SSH enabled, the private key for one of the authorized keys must be
# available to ssh (agent or default identity).

set -euo pipefail
# shellcheck source=scripts/env.sh
source "$(dirname "$0")/env.sh"

VM="${1:?vm name}"
USER_NAME="${RHUBARB_USER:-admin}"
SSH_ENABLED="${RHUBARB_SSH_ENABLED:?set to 1 or 0}"
FAMILY="${RHUBARB_FAMILY:?set to macos, nixos or kali}"
RUN_ARGS=(--no-graphics)
[[ "$FAMILY" != macos && "${RHUBARB_ROSETTA:-false}" == true ]] && RUN_ARGS+=(--rosetta=rosetta)
# Default resolver (DHCP leases, MAC-matched) is stable; arp cache is volatile, so only fall back to it.
vm_ip() { tart ip --wait 300 "$1" 2>/dev/null || tart ip --wait 60 --resolver arp "$1"; }
SMOKE="$VM-smoke-$$"
WORK="$(mktemp -d)"
log() { echo "[smoke] $*"; }
fail() { echo "[smoke] FAILED: $*" >&2; exit 1; }

cleanup() {
  tart stop "$SMOKE" >/dev/null 2>&1 || true
  [[ -n "${RUN_PID:-}" ]] && wait "$RUN_PID" 2>/dev/null || true
  tart delete "$SMOKE" >/dev/null 2>&1 || true
  rm -rf "$WORK"
}
trap cleanup EXIT

tart clone "$VM" "$SMOKE"
tart run "${RUN_ARGS[@]}" "$SMOKE" >"$WORK/run.log" 2>&1 &
RUN_PID=$!
IP="$(vm_ip "$SMOKE")" || fail "no IP within 300s"
log "clone $SMOKE booted at $IP"

port_open() { nc -z -G 3 "$IP" "$1" >/dev/null 2>&1; }

# Linux mount checks, run on the first boot and again after a reboot (#98): a Kali clone once
# came up on its second boot with / read-only and no fstab mounts, because the fstab generator's
# units were missing, while its first boot had been fine.
# shellcheck disable=SC2016  # expands in the guest's shell, not here
LINUX_MOUNTS='set -e
export PATH=/run/wrappers/bin:/run/current-system/sw/bin:/usr/bin:/bin:$PATH
bad() { echo "mounts: $*" >&2; exit 1; }
opts="$(findmnt -no OPTIONS /)"
case ",$opts," in *,rw,*) ;; *) bad "root filesystem is not read-write ($opts)" ;; esac
t="$HOME/.rhubarb-smoke-write"
( : > "$t" ) 2>/dev/null || bad "home directory not writable"
rm -f "$t"
# -L, not -e: on NixOS the link points into a store path that need not exist (systemd resolves
# the unit by name), so only "the generator created the link" is meaningful.
test -L /run/systemd/generator/local-fs.target.wants/systemd-remount-fs.service \
  || bad "fstab generator did not link systemd-remount-fs (fstab mounts were not set up)"
failed="$(systemctl --failed --type=mount --no-legend --plain | awk "{print \$1}")"
test -z "$failed" || bad "mount units failed: $failed"
if [ "$ROSETTA" = true ]; then test -e /proc/sys/fs/binfmt_misc/rosetta || bad "rosetta binfmt not registered"; fi
echo "root $opts"'

if [[ "$SSH_ENABLED" == 1 ]]; then
  for _ in $(seq 1 40); do port_open 22 && break; sleep 3; done
  port_open 22 || fail "SSH port not reachable"

  # Pin this clone's host key for the rest of the test (fresh per clone).
  ssh-keyscan -t ed25519 "$IP" 2>/dev/null > "$WORK/known_hosts"
  [[ -s "$WORK/known_hosts" ]] || fail "no ed25519 host key offered (host keys not regenerated?)"
  log "host key: $(ssh-keygen -lf "$WORK/known_hosts")"
  SSH_OPTS=(-o BatchMode=yes -o ConnectTimeout=10 -o StrictHostKeyChecking=yes
            -o UserKnownHostsFile="$WORK/known_hosts")
  SSH=(ssh "${SSH_OPTS[@]}" "$USER_NAME@$IP")

  # 1. Password / keyboard-interactive must be refused; only publickey offered.
  if out="$(ssh "${SSH_OPTS[@]}" -o PubkeyAuthentication=no \
            -o PreferredAuthentications=password,keyboard-interactive "$USER_NAME@$IP" true 2>&1)"; then
    fail "login succeeded without a key"
  fi
  grep -q 'Permission denied (publickey)' <<<"$out" || fail "unexpected auth methods: $out"
  log "ok: password auth refused, server offers publickey only"

  # 2. Key login works (also proves from= matches the host address).
  for _ in $(seq 1 30); do "${SSH[@]}" true >/dev/null 2>&1 && break; sleep 1; done
  "${SSH[@]}" true || fail "key login failed (key in agent? RHUBARB_SSH_FROM correct?)"
  log "ok: key login"

  # 3. In-guest posture (no sudo needed; `sudo -n` must fail: no NOPASSWD).
  #    (`! cmd` never trips set -e, so negative checks use explicit ifs.)
  if [[ "$FAMILY" == macos ]]; then
    "${SSH[@]}" 'bash -s' <<'EOF' || fail "in-guest posture checks failed"
set -e
if sudo -n true 2>/dev/null; then echo "passwordless sudo works" >&2; exit 1; fi
if defaults read /Library/Preferences/com.apple.loginwindow autoLoginUser >/dev/null 2>&1; then
  echo "auto-login configured" >&2; exit 1
fi
csrutil status | grep -q 'status: enabled\.'
spctl --status | grep -qx 'assessments enabled'
/usr/libexec/ApplicationFirewall/socketfilterfw --getglobalstate | grep -q 'State = [12]'
/usr/libexec/ApplicationFirewall/socketfilterfw --getstealthmode | grep -Eiq 'stealth mode (is )?(on|enabled)'
test ! -e /etc/kcpassword
test -f /Library/RhubarbTart/lock.json
if [ -d "/Applications/Google Chrome.app" ]; then
  test "$(/usr/libexec/PlistBuddy -c 'Print :updatePolicies:com.google.Chrome:UpdateDefault' \
    '/Library/Managed Preferences/com.google.Keystone.plist')" = 2
fi
cat /Library/RhubarbTart/installed.txt
EOF
    log "ok: SIP, Gatekeeper, firewall on; no auto-login; no passwordless sudo; Chrome auto-update off"
  else
    "${SSH[@]}" "FAMILY=$FAMILY ROSETTA=${RHUBARB_ROSETTA:-false} bash -s" <<'EOF' || fail "in-guest posture checks failed"
set -e
# A non-login SSH command shell may not inherit the system PATH, so system/wrapper binaries
# (systemctl, nixos-version, the setuid sudo wrapper) can be missing; put them on PATH first.
export PATH=/run/wrappers/bin:/run/current-system/sw/bin:/usr/bin:/bin:$PATH
bad() { echo "posture: $*" >&2; exit 1; }
if sudo -n true 2>/dev/null; then bad "passwordless sudo works"; fi
if grep -rEqs '^[[:space:]]*autologin-user[[:space:]]*=[[:space:]]*[^[:space:]]' /etc/lightdm; then
  bad "display-manager auto-login configured"
fi
if [[ "$FAMILY" == kali ]]; then
  systemctl is-active --quiet nftables || bad "nftables service not active"
else
  systemctl is-active --quiet firewall || bad "firewall service not active"
fi
test -s /etc/machine-id || bad "machine-id empty (not regenerated for this clone)"
test -s /var/lib/rhubarbtart/installed.txt || bad "installed.txt missing"
if [[ "$ROSETTA" == true ]]; then test -e /proc/sys/fs/binfmt_misc/rosetta || bad "rosetta binfmt not registered"; fi
if [[ "$FAMILY" == nixos ]]; then nixos-version || bad "nixos-version failed"; fi
if systemctl cat juice-shop >/dev/null 2>&1; then   # lab target: no egress (#30)
  deny="$(systemctl show -p IPAddressDeny --value juice-shop)"
  [[ "$deny" == *0.0.0.0/0* && "$deny" == *::/0* ]] || bad "juice-shop egress not denied ($deny)"
fi
echo "packages recorded: $(wc -l < /var/lib/rhubarbtart/installed.txt)"
EOF
    log "ok: no passwordless sudo, no auto-login, firewall active, fresh machine-id"
    "${SSH[@]}" "ROSETTA=${RHUBARB_ROSETTA:-false} bash -c '$LINUX_MOUNTS'" >/dev/null \
      || fail "mount checks failed on the first boot"
    log "ok: root read-write, home writable, fstab mounts up"

    # 4. Second boot (#98): flush, stop, boot the same clone again and repeat the mount checks.
    #    `sync` needs no privileges; it keeps tart's stop from losing the first boot's writes.
    "${SSH[@]}" sync || true
    tart stop "$SMOKE" >/dev/null 2>&1 || true
    wait "$RUN_PID" 2>/dev/null || true
    tart run "${RUN_ARGS[@]}" "$SMOKE" >>"$WORK/run.log" 2>&1 &
    RUN_PID=$!
    IP="$(vm_ip "$SMOKE")" || fail "no IP within 300s on the second boot"
    for _ in $(seq 1 40); do port_open 22 && break; sleep 3; done
    port_open 22 || fail "SSH port not reachable on the second boot"
    ssh-keyscan -t ed25519 "$IP" 2>/dev/null > "$WORK/known_hosts2"
    [[ "$(cut -d' ' -f2- "$WORK/known_hosts")" == "$(cut -d' ' -f2- "$WORK/known_hosts2")" ]] \
      || fail "host key changed across a reboot of the same clone"
    cp "$WORK/known_hosts2" "$WORK/known_hosts"
    SSH=(ssh "${SSH_OPTS[@]}" "$USER_NAME@$IP")
    # Wait for user sessions to be allowed: systemd removes /run/nologin late in the boot,
    # and pam_nologin rejects non-root logins until then, which can race with sshd opening port 22.
    for _ in $(seq 1 30); do "${SSH[@]}" true >/dev/null 2>&1 && break; sleep 1; done
    "${SSH[@]}" "ROSETTA=${RHUBARB_ROSETTA:-false} bash -c '$LINUX_MOUNTS'" >/dev/null \
      || fail "mount checks failed on the second boot"
    log "ok: second boot: root read-write, fstab mounts up, host key unchanged"
  fi
else
  sleep 60  # let launchd settle so a closed port means disabled, not "not yet up"
  port_open 22 && fail "SSH reachable but should be disabled"
  log "ok: SSH disabled"
fi

# Ports a package's service declares (e.g. Juice Shop on 3000) must answer from outside.
# Services such as Node can take a little while after boot.
for p in ${RHUBARB_OPEN_PORTS:-}; do
  [[ "$p" =~ ^[0-9]{1,5}$ ]] || fail "bad RHUBARB_OPEN_PORTS entry '$p'"
  for _ in $(seq 1 60); do port_open "$p" && break; sleep 3; done
  port_open "$p" || fail "declared service port $p not reachable"
  log "ok: declared service port $p reachable"
done

port_open 5900 && fail "Screen Sharing (5900) reachable"
log "ok: Screen Sharing not reachable"
log "PASSED"
