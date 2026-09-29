#!/usr/bin/env bash
# Runs as root on the freshly installed Kali system (Packer SSH, bootstrap password).
# Re-verifies staged .debs, installs the profile's packages, sets up Rosetta.
#
# usage: install.sh <stage-dir>
#   SHA256SUMS, packages.tsv (id kind file team app|package signed), profile.json, lock.json

set -euo pipefail
export DEBIAN_FRONTEND=noninteractive

STAGE="${1:?stage dir}"
RECORD_DIR=/var/lib/rhubarbtart
say() { echo "[kali-install] $*"; }
die() { echo "[kali-install] FAILED: $*" >&2; exit 1; }
opt() { python3 -c 'import json,sys; v=json.load(open(sys.argv[1]))["options"].get(sys.argv[2]); print(json.dumps(v) if isinstance(v,(list,dict,bool)) else (v or ""))' "$STAGE/profile.json" "$1"; }

cd "$STAGE"
say "verifying staged packages"
[[ ! -s SHA256SUMS ]] || sha256sum -c SHA256SUMS || die "hash mismatch inside guest"

# Vendor .debs must not add their own (unpinned) apt sources: updates are a re-resolve.
install -d /etc/default
echo 'repo_add_once="false"' > /etc/default/google-chrome
echo 'repo_reenable_on_distupgrade="false"' >> /etc/default/google-chrome

debs=() distro=()
# packages.tsv has 6 columns (resolve.py): the 6th, "signed", only matters on macOS. `_more`
# swallows it and any future column; without it read(1) appends them to $pkg (#26).
while IFS=$'\t' read -r id kind file _team pkg _signed _more; do
  [[ -z "$id" ]] && continue
  case "$kind" in
    deb) debs+=("./$file") ;;
    distro) distro+=("$pkg") ;;
    *) die "$id: kind '$kind' is not installable on Kali" ;;
  esac
done < packages.tsv

say "apt update (indexes verified against the Kali archive key)"
apt-get update -q
if ((${#debs[@]} + ${#distro[@]})); then
  say "installing: ${debs[*]} ${distro[*]}"
  apt-get install -y -q "${debs[@]}" "${distro[@]}"
fi
rm -f /etc/apt/sources.list.d/google-chrome.list   # belt and braces

# kali-desktop-core hard-depends on kali-grant-root, which ships group-wide passwordless root
# (%kali-trusted ALL=(ALL:ALL) NOPASSWD: ALL). Pin it out first so no later apt run (in a clone)
# can bring it back, then purge it; the kali-desktop-* metapackages go, the desktop stays. (#59)
say "removing kali-grant-root (group-wide passwordless root) and pinning it out"
cat > /etc/apt/preferences.d/rhubarb-no-grant-root <<'EOF'
# RhubarbTart: passwordless root is never allowed in an image (#59)
Package: kali-grant-root
Pin: version *
Pin-Priority: -1
EOF
if dpkg -s kali-grant-root >/dev/null 2>&1; then
  # apt 3's solver won't cascade-remove the manually installed metapackages that depend on it,
  # so purge them explicitly. Mark what they pulled in as manual first: the desktop itself must
  # survive a later `apt autoremove` in a clone.
  metas=(kali-desktop-xfce kali-desktop-core)
  keep=()
  while IFS= read -r p; do keep+=("$p"); done < <(
    apt-cache depends --installed --no-suggests --no-conflicts --no-breaks --no-replaces --no-enhances "${metas[@]}" \
      | awk '/^ +(PreDepends|Depends|Recommends): [^<]/ {print $2}' \
      | grep -vxF -e kali-grant-root -e kali-desktop-core -e kali-desktop-xfce | sort -u)
  if ((${#keep[@]})); then apt-mark manual "${keep[@]}" >/dev/null; fi
  apt-get purge -y -q kali-grant-root "${metas[@]}"
fi

if [[ "$(opt rosetta)" == true ]]; then
  say "enabling Rosetta for x86_64 binaries (needs: tart run --rosetta=rosetta)"
  install -d /media/rosetta
  grep -q '^rosetta ' /etc/fstab || echo 'rosetta /media/rosetta virtiofs ro,nofail 0 0' >> /etc/fstab
  # Magic/mask per Apple's "Running Intel binaries in Linux VMs with Rosetta" (same as
  # nixpkgs nixos/lib/binfmt-magics.nix x86_64-linux); flags F (fix binary) C (credentials).
  printf '%s\n' ':rosetta:M::\x7fELF\x02\x01\x01\x00\x00\x00\x00\x00\x00\x00\x00\x00\x02\x00\x3e\x00:\xff\xff\xff\xff\xff\xfe\xfe\x00\xff\xff\xff\xff\xff\xff\xff\xff\xfe\xff\xff\xff:/media/rosetta/rosetta:CF' \
    > /etc/binfmt.d/rosetta.conf
fi

say "recording installed packages"
install -d -m 755 "$RECORD_DIR"
install -m 644 lock.json "$RECORD_DIR/lock.json"
dpkg-query -W -f='${Package}\t${Version}\t${Architecture}\n' | sort > "$RECORD_DIR/installed.txt"
chmod 644 "$RECORD_DIR/installed.txt"
say "$(wc -l < "$RECORD_DIR/installed.txt") packages installed"
