#!/bin/bash
# Build a RhubarbTart guest from its profile and reviewed lock.
#
#   RHUBARB_SSH_PUBKEYS=~/.ssh/rhubarbtart_ed25519.pub ./scripts/build.sh <profile>
#   REBUILD_VANILLA=1 ./scripts/build.sh <macos-profile>   reinstall macOS from the IPSW
#   ./scripts/build.sh --list                               available profiles
#
# Environment:
#   RHUBARB_SSH_PUBKEYS   file of public keys allowed into the image. Unset => SSH disabled.
#   RHUBARB_SSH_FROM      authorized_keys from= pattern (default 192.168.64.1, Tart's
#                         shared-NAT host); set empty to omit. Also where the Kali
#                         preseed server binds.
#   RHUBARB_CACHE         artifact cache (default ./cache)
#
# Passwords are generated here and kept only in the host login keychain
# (service "RhubarbTart", account = VM name):
#   security find-generic-password -s RhubarbTart -a <vm> -w

set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"
# shellcheck source=scripts/env.sh
source "$ROOT/scripts/env.sh"
log() { echo "[build] $*"; }
die() { echo "[build] FAILED: $*" >&2; exit 1; }
KEYCHAIN_SERVICE=RhubarbTart

if [[ "${1:-}" == --list ]]; then exec uv run --quiet tools/resolve.py list; fi
PROFILE="${1:?usage: build.sh <profile>  (see: build.sh --list)}"

# --- preflight --------------------------------------------------------------
[[ "$(uname -s)" == Darwin && "$(uname -m)" == arm64 ]] || die "Tart requires macOS on Apple silicon"
for bin in tart packer uv ssh-keygen security plutil; do command -v "$bin" >/dev/null || die "$bin not found"; done
uv run --quiet tools/resolve.py preflight
if [[ -n "$(git status --porcelain 2>/dev/null)" ]]; then
  log "WARNING: working tree is dirty; provenance will record git_dirty=true"
fi

py() { uv run --quiet --no-project python -c "$@"; }
json_field() { plutil -extract "$1" raw -o - - <<<"$2"; }   # macOS plutil reads JSON
vm_exists() { tart get "$1" >/dev/null 2>&1; }
random_password() { py 'import secrets,string; a=string.ascii_letters+string.digits; print("".join(secrets.choice(a) for _ in range(32)))'; }
# Bootstrap password is typed over VNC (NixOS) / put in a preseed / passed to the provisioning
# API. Lowercase+digits only: no Shift keys for VNC typing to drop. Throwaway; rotated away.
random_bootstrap() { py 'import secrets,string; a=string.ascii_lowercase+string.digits; print("".join(secrets.choice(a) for _ in range(32)))'; }
keychain_get() { security find-generic-password -s "$KEYCHAIN_SERVICE" -a "$1" -w 2>/dev/null; }
keychain_put() { # <account> <password>; `security -i` keeps the secret out of argv
  printf 'add-generic-password -U -s %s -a %s -w %s\n' "$KEYCHAIN_SERVICE" "$1" "$2" | security -i >/dev/null
  [[ "$(keychain_get "$1")" == "$2" ]] || die "could not store password in keychain for $1"
}

WORK="$(mktemp -d)"
SERVER_PID=""
# Must not fail before rm: under set -e a failing last command of an && list aborts even the
# EXIT trap (the preseed server has usually exited), which left $WORK and its password file
# behind and turned a successful build into exit 1. (#61)
cleanup() {
  if [[ -n "$SERVER_PID" ]]; then kill "$SERVER_PID" 2>/dev/null || true; fi
  rm -rf "$WORK"
}
trap cleanup EXIT
chmod 700 "$WORK"

# --- SSH authorized keys (validated on the host) -------------------------------
AUTH_KEYS="$WORK/authorized_keys"
: > "$AUTH_KEYS"
if [[ -n "${RHUBARB_SSH_PUBKEYS:-}" ]]; then
  [[ -f "$RHUBARB_SSH_PUBKEYS" ]] || die "RHUBARB_SSH_PUBKEYS not a file: $RHUBARB_SSH_PUBKEYS"
  while IFS= read -r line; do
    [[ "$line" =~ ^[[:space:]]*(#|$) ]] && continue
    case "$line" in
      ssh-ed25519\ *|sk-ssh-ed25519@openssh.com\ *|ecdsa-sha2-nistp256\ *|sk-ecdsa-sha2-nistp256@openssh.com\ *) ;;
      *) die "unsupported key type (use ed25519/ecdsa, optionally -sk): ${line:0:40}…" ;;
    esac
    ssh-keygen -lf /dev/stdin <<<"$line" >/dev/null || die "invalid public key: ${line:0:40}…"
    echo "$line" >> "$AUTH_KEYS"
  done < "$RHUBARB_SSH_PUBKEYS"
  [[ -s "$AUTH_KEYS" ]] || die "no usable keys in $RHUBARB_SSH_PUBKEYS"
  log "SSH: $(wc -l < "$AUTH_KEYS" | tr -d ' ') key(s) authorized"
else
  log "SSH: RHUBARB_SSH_PUBKEYS unset -> SSH will be DISABLED in the image"
fi
SSH_FROM="${RHUBARB_SSH_FROM-192.168.64.1}"
[[ "$SSH_FROM" =~ ^[0-9A-Fa-f.:/*?,!]*$ ]] || die "RHUBARB_SSH_FROM has unexpected characters"

# --- verify inputs & stage ----------------------------------------------------
log "verifying cached inputs against locks/$PROFILE.lock.json"
info="$(uv run --quiet tools/resolve.py verify "$PROFILE")"
FAMILY="$(json_field family "$info")"
BASE_ID="$(json_field base_id "$info")"
IMAGE="$(json_field image_path "$info")"
STAGE="$(json_field stage_dir "$info")"
OS_BUILD="$(json_field os_build "$info")"
INPUTS_SHA="$(json_field inputs_sha256 "$info")"
ROSETTA="$(json_field rosetta "$info")"
OPEN_PORTS="$(json_field open_ports "$info")"
# Profiles with Rosetta boot with `tart run --rosetta`, which needs Rosetta for Linux VMs on this
# Mac. Ask the Virtualization framework (0 unsupported, 1 not installed, 2 installed) rather than
# `arch -x86_64`, which fails on macOS 27 even when Linux Rosetta works (#158). Fail now, not when
# the smoke test times out at the end of the build.
if [[ "$ROSETTA" == true ]]; then
  rosetta="$(osascript -l JavaScript -e 'ObjC.import("Foundation");
    $.NSBundle.bundleWithPath("/System/Library/Frameworks/Virtualization.framework").load;
    $.NSClassFromString("VZLinuxRosettaDirectoryShare").availability' 2>/dev/null || echo unknown)"
  case "$rosetta" in
    2) log "Rosetta for Linux VMs: installed" ;;
    1) die "$PROFILE uses Rosetta, which isn't installed on this Mac. Install it, then build again: softwareupdate --install-rosetta --agree-to-license" ;;
    0) die "$PROFILE uses Rosetta, which this Mac doesn't support. Set \"rosetta\": false in profiles/$PROFILE.json" ;;
    *) log "WARNING: couldn't check whether Rosetta is installed (got '$rosetta'); if it isn't, the smoke test will time out" ;;
  esac
fi
# A Mac runs at most two macOS VMs at once (Apple's licence, enforced by the Virtualization
# framework); Linux VMs don't count. A macOS build runs one at a time (the install, then the smoke
# test's clone), so it needs a free slot; otherwise tart fails deep into the build (#166).
if [[ "$FAMILY" == macos ]]; then
  running_macos="" n_macos=0
  for vm in $(ps -axo command= | awk '$0 ~ /(^|\/)tart run / {print $NF}'); do
    os="$(plutil -extract os raw "$HOME/.tart/vms/$vm/config.json" 2>/dev/null || true)"
    if [[ "$os" == darwin ]]; then running_macos="$running_macos $vm"; n_macos=$((n_macos + 1)); fi
  done
  if ((n_macos >= 2)); then
    die "a Mac runs at most two macOS VMs at once, and two are running:$running_macos. Stop one (rhubarbtart stop NAME, or tart stop NAME), then build again"
  fi
fi
export PKR_VAR_username PKR_VAR_cpu_count PKR_VAR_memory_gb PKR_VAR_disk_gb
PKR_VAR_username="$(json_field username "$info")"
PKR_VAR_cpu_count="$(json_field cpu "$info")"
PKR_VAR_memory_gb="$(json_field memory_gb "$info")"
PKR_VAR_disk_gb="$(json_field disk_gb "$info")"
export PKR_VAR_authorized_keys_path="$AUTH_KEYS" PKR_VAR_ssh_from="$SSH_FROM"
export PKR_VAR_headless="${RHUBARB_HEADLESS:-true}"   # RHUBARB_HEADLESS=false to watch the VM window
# Nix writes its progress ("copying/building …") to stderr, which Packer's colored UI paints red
# even though it isn't errors. Set RHUBARB_NO_COLOR (or the standard NO_COLOR) to get plain output.
[[ -n "${RHUBARB_NO_COLOR:-}" || -n "${NO_COLOR:-}" ]] && export PACKER_NO_COLOR=1
BASE_JSON="$(cat "config/bases/$BASE_ID.json")"
BASE_PACKER="$(json_field packer "$BASE_JSON")"            # stage-1 / install template
[[ "$BASE_PACKER" =~ ^packer/(macos|linux)/[a-z0-9.-]+\.pkr\.hcl$ && -f "$BASE_PACKER" ]] \
  || die "config/bases/$BASE_ID.json: packer template '$BASE_PACKER' missing or outside packer/"

VM="rbt-${PROFILE}-${INPUTS_SHA:0:12}"       # same inputs => same name
CANDIDATE="$VM-unverified"                  # renamed to $VM only after the smoke test passes
if vm_exists "$CANDIDATE"; then tart delete "$CANDIDATE"; fi

PASSWORD_FILE="$WORK/password"
write_password_file() { umask 077; printf '%s' "$1" > "$PASSWORD_FILE"; }

case "$FAMILY" in
  macos)
    SETUP="$(json_field setup "$BASE_JSON")"
    BASE_VM="rbt-${BASE_ID}-${OS_BUILD}-vanilla"
    if [[ "$SETUP" == provisioning ]]; then
      need="$(json_field requires_host_major "$BASE_JSON")"
      have="$(sw_vers -productVersion | cut -d. -f1)"
      ((have >= need)) || die "$BASE_ID needs a macOS $need host (this host: $(sw_vers -productVersion))"
    fi
    if [[ "${REBUILD_VANILLA:-0}" == 1 ]] && vm_exists "$BASE_VM"; then tart delete "$BASE_VM"; fi
    if vm_exists "$BASE_VM"; then
      PKR_VAR_password="$(keychain_get "$BASE_VM")" \
        || die "$BASE_VM exists but its password is not in the keychain; rerun with REBUILD_VANILLA=1"
      export PKR_VAR_password
      log "reusing $BASE_VM (REBUILD_VANILLA=1 reinstalls from the IPSW)"
    else
      PKR_VAR_password="$(random_password)"
      export PKR_VAR_password
      keychain_put "$BASE_VM" "$PKR_VAR_password"
      log "installing macOS $OS_BUILD from $IMAGE -> $BASE_VM ($SETUP)"
      if [[ "$SETUP" == provisioning ]]; then
        write_password_file "$PKR_VAR_password"
        PKR_VAR_bootstrap_password="$(random_bootstrap)" PKR_VAR_password_file="$PASSWORD_FILE" \
          packer build -var "ipsw_path=$IMAGE" -var "vm_name=$BASE_VM" "$BASE_PACKER"
      else
        # Setup Assistant steps wait for on-screen text (#63) and would wait forever if Apple
        # renamed a screen, so stage 1 gets a deadline. packer stays in the foreground (Ctrl-C
        # keeps working); the watchdog sends SIGTERM, which packer handles by cleaning up the VM.
        deadline_flag="$(mktemp -u)"
        ( sleep "${RHUBARB_STAGE1_DEADLINE:-3600}"; touch "$deadline_flag"
          pkill -TERM -P "$$" -x packer ) &
        watchdog=$!
        rc=0
        packer build -var "ipsw_path=$IMAGE" -var "vm_name=$BASE_VM" "$BASE_PACKER" || rc=$?
        pkill -P "$watchdog" 2>/dev/null || true; kill "$watchdog" 2>/dev/null || true
        if [[ -e "$deadline_flag" ]]; then
          rm -f "$deadline_flag"
          die "macOS stage 1 didn't finish within ${RHUBARB_STAGE1_DEADLINE:-3600}s; Setup Assistant may be on a screen whose text the boot_command waits for has changed (look at the VM window; see #63)"
        fi
        ((rc == 0)) || exit "$rc"
      fi
    fi
    log "building $CANDIDATE from $BASE_VM"
    packer build -var "base_vm=$BASE_VM" -var "vm_name=$CANDIDATE" -var "stage_dir=$STAGE" \
      packer/macos/apps.pkr.hcl
    ;;

  nixos|kali)
    # Linux guests are installed from scratch each build: fresh final password, plus a
    # throwaway bootstrap password for the installer session only.
    PKR_VAR_password="$(random_password)"
    export PKR_VAR_password
    write_password_file "$PKR_VAR_password"
    export PKR_VAR_password_file="$PASSWORD_FILE"
    PKR_VAR_bootstrap_password="$(random_bootstrap)"
    export PKR_VAR_bootstrap_password
    keychain_put "$VM" "$PKR_VAR_password"
    if [[ "$FAMILY" == kali ]]; then
      [[ -n "$SSH_FROM" && "$SSH_FROM" =~ ^[0-9.]+$ ]] \
        || die "Kali needs RHUBARB_SSH_FROM to be the vmnet host IPv4 (preseed server binds there)"
      pkgs="$(py 'import json,sys; o=json.load(open(sys.argv[1]))["options"]; d={"xfce":["kali-desktop-xfce"]}.get(o.get("desktop"),[]); print(" ".join(o.get("kali_metapackages",[])+d))' "$STAGE/profile.json")"
      (umask 077; sed -e "s|@USER@|$PKR_VAR_username|" -e "s|@PACKAGES@|$pkgs|" guest/kali/preseed.cfg.tmpl \
        | BOOT="$PKR_VAR_bootstrap_password" py 'import os,sys; sys.stdout.write(sys.stdin.read().replace("@BOOTSTRAP@", os.environ["BOOT"]))' \
        > "$WORK/preseed.cfg")
      port=$((20000 + RANDOM % 20000))
      uv run --quiet tools/serve_preseed.py "$WORK/preseed.cfg" "$SSH_FROM" "$port" &
      SERVER_PID=$!
      export PKR_VAR_preseed_url="http://$SSH_FROM:$port/preseed.cfg"
    fi
    log "installing $FAMILY ($OS_BUILD) from $IMAGE -> $CANDIDATE"
    packer build -var "iso_path=$IMAGE" -var "vm_name=$CANDIDATE" -var "stage_dir=$STAGE" \
      "$BASE_PACKER"
    ;;
  *) die "unknown family $FAMILY" ;;
esac

# --- prove the hardening on a throwaway clone; only then give it the real name --------
RHUBARB_FAMILY="$FAMILY" RHUBARB_USER="$PKR_VAR_username" RHUBARB_ROSETTA="$ROSETTA" RHUBARB_OPEN_PORTS="$OPEN_PORTS" \
RHUBARB_SSH_ENABLED="$([[ -s "$AUTH_KEYS" ]] && echo 1 || echo 0)" \
  ./scripts/smoke-test.sh "$CANDIDATE" \
  || die "smoke test failed; unverified image left as $CANDIDATE for inspection"
if vm_exists "$VM"; then tart delete "$VM"; fi
tart rename "$CANDIDATE" "$VM"
keychain_put "$VM" "$PKR_VAR_password"
unset PKR_VAR_password PKR_VAR_bootstrap_password

record="$(uv run --quiet tools/resolve.py provenance "$PROFILE" "$VM" \
  --ssh-keys "$AUTH_KEYS" --ssh-from "$SSH_FROM")"
log "done: $VM"
log "provenance: $record"
