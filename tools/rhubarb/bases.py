"""Base OS resolvers: where each family's installer comes from and how it is pinned.

Every artifact entry carries url, file, sha256 (or None until downloaded) and
hash_sources, so resolve/verify can treat them uniformly. `large: True` marks
multi-GB images that `resolve --skip-large` leaves for the build host to fetch.
"""

import re
import tempfile
import urllib.parse
from pathlib import Path

from .common import VerifyError, get_bytes, get_json, head, log, parse_sums
from .gpg import verify_detached

IPSW_ME = "https://api.ipsw.me/v4/device/{device}?type=ipsw"
APPLE_IPSW_HOST = "updates.cdn-apple.com"
NIXOS_CHANNELS = "https://channels.nixos.org"
NIXOS_RELEASES_HOST = "releases.nixos.org"
KALI_CDIMAGE = "https://cdimage.kali.org"


def _vtuple(v: str) -> tuple[int, ...]:
    return tuple(int(p) for p in re.findall(r"\d+", v))


# ---- macOS: Apple restore image ------------------------------------------------------

def plan_macos(base: dict) -> dict:
    """Newest restore image for the base's major version, hash from Apple's CDN.

    `tart create --from-ipsw=latest` follows Apple's catalog, which may already point at
    a newer major than this base wants, so we always filter on the major explicitly.
    """
    cfg = base["ipsw"]
    fw = get_json(IPSW_ME.format(device=cfg["device"]))["firmwares"]
    cands = [f for f in fw if int(f["version"].split(".")[0]) == cfg["major"]]
    if cfg.get("pin_build"):
        cands = [f for f in cands if f["buildid"] == cfg["pin_build"]]
    if not cands:
        raise VerifyError(f"no IPSW for macOS {cfg['major']} (pin_build={cfg.get('pin_build')})")
    best = max(cands, key=lambda f: _vtuple(f["version"]))
    url = best["url"]
    if urllib.parse.urlparse(url).hostname != APPLE_IPSW_HOST:
        raise VerifyError(f"IPSW URL is not on {APPLE_IPSW_HOST}: {url}")
    hdrs = head(url)
    apple_sha = hdrs.get("x-amz-meta-digest-sha256")
    if not apple_sha:
        raise VerifyError("Apple CDN did not return x-amz-meta-digest-sha256")
    if best.get("sha256sum") and best["sha256sum"] != apple_sha:
        raise VerifyError(f"IPSW hash disagreement: apple={apple_sha} ipsw.me={best['sha256sum']}")
    return {
        "version": best["version"],
        "build": best["buildid"],
        "image": {
            "url": url,
            "file": Path(urllib.parse.urlparse(url).path).name,
            "sha256": apple_sha,
            "size": int(hdrs.get("content-length", 0)),
            "large": True,
            "hash_sources": ["apple-cdn:x-amz-meta-digest-sha256"]
            + (["ipsw.me"] if best.get("sha256sum") else []),
        },
    }


# ---- NixOS: official aarch64 installer ISO + nixpkgs pinned by git rev -----------------

def plan_nixos(base: dict) -> dict:
    ch = base["nixos"]["channel"]
    alias = f"{NIXOS_CHANNELS}/{ch}/latest-nixos-minimal-aarch64-linux.iso"
    iso_url = head(alias)["x-final-url"]
    if urllib.parse.urlparse(iso_url).hostname != NIXOS_RELEASES_HOST:
        raise VerifyError(f"NixOS ISO resolved off {NIXOS_RELEASES_HOST}: {iso_url}")
    iso_name = iso_url.rsplit("/", 1)[1]
    listed = parse_sums(get_bytes(iso_url + ".sha256").decode())
    if iso_name not in listed:
        raise VerifyError(f"{iso_name} missing from its .sha256 file")
    release_dir = iso_url.rsplit("/", 1)[0]
    rev = get_bytes(release_dir + "/git-revision").decode().strip()
    if not re.fullmatch(r"[0-9a-f]{40}", rev):
        raise VerifyError(f"unexpected git-revision {rev!r}")
    short = re.search(r"\.([0-9a-f]{12})-aarch64", iso_name)
    if not short or not rev.startswith(short.group(1)):
        raise VerifyError(f"ISO name {iso_name} does not match release git-revision {rev}")
    return {
        "channel": ch,
        "release": release_dir.rsplit("/", 1)[1],
        "image": {
            "url": iso_url, "file": iso_name, "sha256": listed[iso_name], "large": True,
            "hash_sources": [f"{NIXOS_RELEASES_HOST} {iso_name}.sha256 (HTTPS)"],
        },
        # Source of the whole OS: nixpkgs at the exact commit the channel release was built
        # from. The NAR hash is computed at resolve time (tools/rhubarb/nar.py) and re-checked
        # by Nix in the guest; binaries come from cache.nixos.org, signed with its pinned key.
        "nixpkgs": {
            "rev": rev,
            "url": f"https://github.com/NixOS/nixpkgs/archive/{rev}.tar.gz",
            "file": f"nixpkgs-{rev[:12]}.tar.gz",
            "sha256": None,
            "nar_sha256": None,
            "hash_sources": ["tofu:downloaded (github archive of the channel's git-revision)",
                             "nar_sha256 re-verified by nix in the guest"],
        },
    }


# ---- Kali: official arm64 installer ISO, GPG-signed SHA256SUMS -------------------------

def plan_kali(base: dict) -> dict:
    k = base["kali"]
    current = get_bytes(f"{KALI_CDIMAGE}/current/SHA256SUMS").decode()
    name = next((n for n in parse_sums(current) if re.fullmatch(k["iso_pattern"], n)), None)
    if not name:
        raise VerifyError(f"no ISO matching {k['iso_pattern']} in Kali current/SHA256SUMS")
    version = re.search(r"kali-linux-(\d+\.\d+[a-z]?)-", name).group(1)
    if k.get("pin_version"):
        version = k["pin_version"]
        name = re.sub(r"kali-linux-[\d.]+[a-z]?-", f"kali-linux-{version}-", name)
    vdir = f"{KALI_CDIMAGE}/kali-{version}"
    with tempfile.TemporaryDirectory() as tmp:
        sums, sig = Path(tmp, "SHA256SUMS"), Path(tmp, "SHA256SUMS.gpg")
        sums.write_bytes(get_bytes(f"{vdir}/SHA256SUMS"))
        sig.write_bytes(get_bytes(f"{vdir}/SHA256SUMS.gpg"))
        verify_detached(sums, sig, k["key"], k["key_fpr"])
        listed = parse_sums(sums.read_text())
    if name not in listed:
        raise VerifyError(f"{name} not in signed kali-{version}/SHA256SUMS")
    log(f"kali {version}: SHA256SUMS signed by {k['key_fpr']}")
    return {
        "version": version,
        "image": {
            "url": f"{vdir}/{name}", "file": name, "sha256": listed[name], "large": True,
            "hash_sources": [f"kali-{version}/SHA256SUMS signed by {k['key_fpr']}"],
        },
    }


PLANNERS = {"macos": plan_macos, "nixos": plan_nixos, "kali": plan_kali}
