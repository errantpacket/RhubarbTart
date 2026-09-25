"""Package resolvers, keyed by a variant's "resolver" field.

Artifact-producing resolvers return url/file/sha256/hash_sources (sha256 None = TOFU:
recorded when resolve downloads it). "distro" and "nix" variants produce no artifact:
they are installed from a signed distro repo / pinned nixpkgs and recorded as such.
"""

import re
import xml.etree.ElementTree as ET

from . import apt
from .common import ROOT, VerifyError, get_bytes, get_json, log

ZAP_VERSIONS = "https://raw.githubusercontent.com/zaproxy/zap-admin/master/ZapVersions.xml"
ZAP_RELEASE = "https://api.github.com/repos/zaproxy/zaproxy/releases/tags/v{version}"
CHROME_VERSIONS = ("https://versionhistory.googleapis.com/v1/chrome/platforms/mac/channels/"
                   "stable/versions?pageSize=1")
WARP_FEED = "https://downloads.cloudflareclient.com/v1/update/json/macos/ga"
TAILSCALE_FEED = "https://pkgs.tailscale.com/stable/?mode=json"


def _vtuple(v: str) -> tuple[int, ...]:
    return tuple(int(p) for p in re.findall(r"\d+", v))


def chrome_mac(v: dict) -> dict:
    # The enterprise pkg URL is unversioned: version + hash are known only after download
    # (resolve reads the version from the pkg's Distribution file).
    advertised = get_json(CHROME_VERSIONS)["versions"][0]["version"]
    return {"url": v["url"], "file": "GoogleChrome.pkg", "sha256": None,
            "advertised_version": advertised, "hash_sources": ["tofu:downloaded"]}


def zap_mac(v: dict) -> dict:
    root = ET.fromstring(get_bytes(ZAP_VERSIONS))
    version = root.findtext("core/version")
    if not version:
        raise VerifyError("could not read core/version from ZapVersions.xml")
    name = f"ZAP_{version}_{v['arch']}.dmg"
    release = get_json(ZAP_RELEASE.format(version=version))
    asset = next((a for a in release["assets"] if a["name"] == name), None)
    if asset is None:
        raise VerifyError(f"ZAP v{version} has no asset {name}")
    digest = asset.get("digest") or ""
    if not digest.startswith("sha256:"):
        raise VerifyError(f"GitHub publishes no sha256 digest for {name}")
    return {"version": version, "url": asset["browser_download_url"], "file": name,
            "sha256": digest.removeprefix("sha256:"), "size": asset["size"],
            "hash_sources": ["github-release-digest"]}


def warp_mac(v: dict) -> dict:
    items = get_json(WARP_FEED)["items"]
    best = max(items, key=lambda i: _vtuple(i["version"]))
    if not best["packageURL"].startswith("https://downloads.cloudflareclient.com/"):
        raise VerifyError(f"unexpected WARP package host: {best['packageURL']}")
    return {"version": best["version"], "url": best["packageURL"],
            "file": f"Cloudflare_WARP_{best['version']}.pkg", "sha256": None,
            "size": int(best.get("packageSize", 0)),
            "hash_sources": ["tofu:downloaded (Cloudflare publishes no package hash)"]}


def tailscale_mac(v: dict) -> dict:
    feed = get_json(TAILSCALE_FEED)
    name = feed["MacZips"]["universal-package"]
    url = f"https://pkgs.tailscale.com/stable/{name}"
    sha = get_bytes(url + ".sha256").decode().split()[0].lower()
    if not re.fullmatch(r"[0-9a-f]{64}", sha):
        raise VerifyError(f"bad sha256 file for {name}")
    return {"version": feed["Version"], "url": url, "file": name, "sha256": sha,
            "distsign": True,  # resolve verifies the Ed25519 chain from the pinned root
            "hash_sources": ["pkgs.tailscale.com .sha256", "distsign ed25519 (pinned root)"]}


def local(v: dict) -> dict:
    path = ROOT / v["path"]
    return {"url": None, "file": path.name, "local_path": v["path"], "sha256": None,
            "hash_sources": ["tofu:local-file (vendor portal)"]}


def apt_deb(v: dict) -> dict:
    return apt.resolve_deb(v)


def distro(v: dict) -> dict:
    # Installed by the guest's own package manager from the distro's signed repo; the
    # exact installed version is recorded in the image (/var/lib/rhubarbtart/installed.txt).
    log(f"distro package {v['package']} (installed from the signed distro repository)")
    return {"package": v["package"], "hash_sources": ["distro repo (signed); version recorded in image"]}


def nix(v: dict) -> dict:
    # Pinned transitively by the base's nixpkgs rev + NAR hash; nothing to download here.
    return {"nix": {k: v[k] for k in ("attr", "module", "unfree") if k in v},
            "hash_sources": ["nixpkgs pinned by base (rev + nar_sha256)"]}


RESOLVERS = {
    "chrome-mac": chrome_mac, "zap-mac": zap_mac, "warp-mac": warp_mac,
    "tailscale-mac": tailscale_mac, "local": local, "apt": apt_deb,
    "distro": distro, "nix": nix,
}


def plan_package(pid: str, variant: dict) -> dict:
    fn = RESOLVERS.get(variant.get("resolver"))
    if fn is None:
        raise VerifyError(f"{pid}: unknown resolver {variant.get('resolver')!r}")
    entry = fn(variant)
    entry["kind"] = variant.get("kind", variant["resolver"])
    return entry
