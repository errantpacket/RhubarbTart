#!/usr/bin/env bash
# Runs as root in the official NixOS live ISO (Packer SSH, throwaway bootstrap login).
# Verifies the staged inputs, partitions /dev/vda, installs the pinned system.
#
# usage: RB_SSH_FROM=<pattern|""> install.sh <stage-dir>
#   <stage-dir>/SHA256SUMS, profile.json, lock.json, nixpkgs-*.tar.gz   from resolve.py verify
#   <stage-dir>/nix/                                                    repo nix/ (configuration)
#   <stage-dir>/authorized_keys                                         empty => no sshd
#   <stage-dir>/password                                                final password (0600), shredded here

set -euo pipefail

STAGE="${1:?stage dir}"
: "${RB_SSH_FROM?}"
DISK=/dev/vda
say() { echo "[nixos-install] $*"; }
die() { echo "[nixos-install] FAILED: $*" >&2; exit 1; }
json() { nix-instantiate --eval --json -E "(builtins.fromJSON (builtins.readFile $STAGE/profile.json)).$1" | tr -d '"'; }

cd "$STAGE"
say "verifying staged inputs"
sha256sum -c SHA256SUMS || die "hash mismatch inside guest"
[[ -s password ]] || die "no password staged"

# nixpkgs: the tarball hash was checked above; now let Nix itself confirm the NAR hash
# recorded in the lock (the same check fetchTarball performs).
NAR="$(json nixpkgs.nar_sha256)"
TARBALL="$(json nixpkgs.file)"
mkdir -p nixpkgs && tar -xzf "$TARBALL" -C nixpkgs --strip-components=1
GOT="$(nix --extra-experimental-features nix-command hash path nixpkgs)"
[[ "$GOT" == "$NAR" ]] || die "nixpkgs NAR hash $GOT != locked $NAR"
say "nixpkgs NAR hash ok ($NAR)"

say "partitioning $DISK (GPT: 1 GiB ESP + ext4 root)"
wipefs -a "$DISK"
sfdisk --quiet "$DISK" <<'EOF'
label: gpt
size=1GiB, type=uefi, name=BOOT
type=linux, name=nixos
EOF
udevadm settle
mkfs.fat -F 32 -n BOOT "${DISK}1" >/dev/null
mkfs.ext4 -q -F -L nixos "${DISK}2"
mount /dev/disk/by-label/nixos /mnt
mkdir -p /mnt/boot && mount -o umask=077 /dev/disk/by-label/BOOT /mnt/boot

say "staging configuration"
install -d -m 755 /mnt/etc/nixos
cp -r nix/. /mnt/etc/nixos/
install -m 644 profile.json lock.json /mnt/etc/nixos/
install -m 644 authorized_keys /mnt/etc/nixos/authorized_keys
printf '{"from": "%s"}\n' "$RB_SSH_FROM" > /mnt/etc/nixos/ssh.json
install -d -m 700 /mnt/var/lib/rhubarbtart
# yescrypt hash from stdin; the plaintext never appears in argv or the Nix store
mkpasswd -m yescrypt -s < password > /mnt/var/lib/rhubarbtart/password.hash
chmod 600 /mnt/var/lib/rhubarbtart/password.hash
shred -u password 2>/dev/null || rm -f password

say "installing (binaries only from cache.nixos.org, signature-checked)"
nixos-install --root /mnt --no-root-passwd --no-channel-copy \
  -I nixpkgs="$STAGE/nixpkgs" -I nixos-config=/mnt/etc/nixos/configuration.nix

SYSTEM="$(readlink -f /mnt/nix/var/nix/profiles/system)"
say "recording installed closure"
nix-store --store /mnt -qR "$SYSTEM" | sed 's|^/nix/store/[a-z0-9]*-||' | sort -u \
  > /mnt/var/lib/rhubarbtart/installed.txt
chmod 644 /mnt/var/lib/rhubarbtart/installed.txt
say "installed $(wc -l < /mnt/var/lib/rhubarbtart/installed.txt) store paths"
