"""Host toolchain: pins (config/toolchain.env), pin derivation, preflight."""

import hashlib
import platform
import shutil
import subprocess
import tempfile
from pathlib import Path
import re

from . import gpg, pgp_ed25519
from .common import KEYS, ROOT, VerifyError, get_bytes, get_json, log, parse_sums, sha256_file, warn
from .macos import verify_app

PINS = ROOT / "config" / "toolchain.env"
TOOLCHAIN = ROOT / ".toolchain"
HASHICORP_KEYFILE = "hashicorp-72D7468F.asc"
HASHICORP_FPR = "C874011F0AB405110D02105534365D9472D7468F"
PIN_RE = re.compile(r"^([A-Z0-9_]+)=([A-Za-z0-9._:/+-]*)$")

# GnuPG is built from source into .toolchain by bootstrap (#54). Its release tarballs are
# dual-signed with these Ed25519 keys (config/keys/gnupg-release-signing.asc, fingerprints
# cross-checked against https://gnupg.org/signature_key.html), verified in pure Python so
# pinning never depends on an already-trusted gpg.
GNUPG_KEYFILE = "gnupg-release-signing.asc"
GNUPG_SIGNERS = {
    "6DAA6E64A76D2840571B4902528897B826403ADA",  # Werner Koch (dist signing 2020)
    "AC8E115BF73E2D8D47FA9908E98E9B2D19C6C8BD",  # NIIBE Yutaka (GnuPG Release Key)
}
GNUPG_FTP = "https://gnupg.org/ftp/gcrypt"
# (pin prefix, ftp dir / tarball name, swdb.lst key) in build order
GNUPG_COMPONENTS = [
    ("LIBGPG_ERROR", "libgpg-error", "libgpg_error"),
    ("LIBGCRYPT", "libgcrypt", "libgcrypt"),
    ("LIBASSUAN", "libassuan", "libassuan"),
    ("LIBKSBA", "libksba", "libksba"),
    ("NPTH", "npth", "npth"),
    ("GNUPG", "gnupg", "gnupg26"),
]
PINS_HEADER = """\
# Pinned host toolchain, installed into ./.toolchain by tools/bootstrap.sh (no Homebrew).
# Update with: uv run tools/resolve.py toolchain-pin [--latest]  (then review the diff)
#
# Each SHA256 was accepted only when two upstream views agreed:
#   GitHub assets: release checksums file == GitHub asset digest
#   Packer:        HashiCorp SHA256SUMS, GPG-verified with config/keys/hashicorp-72D7468F.asc
#   GnuPG + libs:  tarball signature by a pinned GnuPG release key (config/keys/
#                  gnupg-release-signing.asc) == sha256 in gnupg.org's signed swdb.lst
#                  (built from source by bootstrap; needs the Xcode Command Line Tools)
#   zot + cosign:  GitHub asset digest == the release's checksums file (registry publishing, #32)
# *_TEAM_ID: Apple Developer Team ID of the notarized signature; empty = record-and-warn.
# Pin after confirming the value printed by bootstrap out-of-band.
"""


def load_pins() -> dict[str, str]:
    """Strict KEY=value parser (the same grammar tools/bootstrap.sh accepts)."""
    pins = {}
    for n, line in enumerate(PINS.read_text().splitlines(), 1):
        s = line.strip()
        if not s or s.startswith("#"):
            continue
        m = PIN_RE.match(s)
        if not m:
            raise VerifyError(f"{PINS.name}:{n}: not a KEY=value line")
        pins[m[1]] = m[2]
    return pins


def github_asset(repo: str, tag: str, name: str, sums_name: str) -> dict:
    """URL + sha256 of a release asset; the checksums file must agree with GitHub's digest."""
    release = get_json(f"https://api.github.com/repos/{repo}/releases/tags/{tag}")
    assets = {a["name"]: a for a in release["assets"]}
    if name not in assets or sums_name not in assets:
        raise VerifyError(f"{repo}@{tag}: missing asset {name} or {sums_name}")
    digest = (assets[name].get("digest") or "").removeprefix("sha256:")
    if not digest:
        raise VerifyError(f"{repo}@{tag}: GitHub publishes no digest for {name}")
    listed = parse_sums(get_bytes(assets[sums_name]["browser_download_url"]).decode()).get(name)
    if listed != digest:
        raise VerifyError(f"{repo}@{tag}: {sums_name} says {listed}, GitHub digest says {digest}")
    return {"url": assets[name]["browser_download_url"], "sha256": digest}


def github_latest(repo: str) -> str:
    return get_json(f"https://api.github.com/repos/{repo}/releases/latest")["tag_name"]


def packer_asset(version: str) -> dict:
    """darwin_arm64 zip hash from HashiCorp's SHA256SUMS, after verifying its GPG signature."""
    base = f"https://releases.hashicorp.com/packer/{version}/packer_{version}"
    with tempfile.TemporaryDirectory() as tmp:
        sums, sig = Path(tmp, "SHA256SUMS"), Path(tmp, "SHA256SUMS.sig")
        sums.write_bytes(get_bytes(f"{base}_SHA256SUMS"))
        sig.write_bytes(get_bytes(f"{base}_SHA256SUMS.72D7468F.sig"))
        gpg.verify_detached(sums, sig, HASHICORP_KEYFILE, HASHICORP_FPR)
        name = f"packer_{version}_darwin_arm64.zip"
        sha = parse_sums(sums.read_text()).get(name)
    if not sha:
        raise VerifyError(f"{name} not listed in SHA256SUMS")
    return {"url": f"https://releases.hashicorp.com/packer/{version}/{name}", "sha256": sha}


def _gnupg_verify(data: bytes, sig: bytes, what: str) -> None:
    signers = pgp_ed25519.verify_detached(data, sig, (KEYS / GNUPG_KEYFILE).read_text(), GNUPG_SIGNERS)
    log(f"{what}: signed by {', '.join(sorted(s[-16:] for s in signers))}")


def gnupg_assets(old: dict, latest: bool) -> dict[str, dict]:
    """{prefix: {version, url, sha256}} for GnuPG and its libraries. Two views must agree: each
    tarball's detached signature by a pinned release key, and the sha256 published in gnupg.org's
    swdb.lst (itself signed). A pinned version swdb no longer lists (not --latest) is accepted on
    its signature alone, and reported."""
    swdb_text = get_bytes("https://versions.gnupg.org/swdb.lst")
    _gnupg_verify(swdb_text, get_bytes("https://versions.gnupg.org/swdb.lst.sig"), "swdb.lst")
    swdb = dict(ln.split(" ", 1) for ln in swdb_text.decode().splitlines() if " " in ln)
    out = {}
    for prefix, name, key in GNUPG_COMPONENTS:
        version = swdb[f"{key}_ver"].strip() if latest or f"{prefix}_VERSION" not in old \
            else old[f"{prefix}_VERSION"]
        if not re.fullmatch(r"[0-9][0-9.]*[0-9]", version):
            raise VerifyError(f"unexpected {name} version {version!r}")
        url = f"{GNUPG_FTP}/{name}/{name}-{version}.tar.bz2"
        data = get_bytes(url)
        _gnupg_verify(data, get_bytes(f"{url}.sig"), f"{name} {version}")
        sha = hashlib.sha256(data).hexdigest()
        if swdb.get(f"{key}_ver", "").strip() == version:
            if swdb.get(f"{key}_sha2", "").strip() != sha:
                raise VerifyError(f"{name} {version}: sha256 {sha} != signed swdb.lst value")
        else:
            warn(f"{name} {version} is no longer current in swdb.lst; pinned on its signature alone")
        out[prefix] = {"version": version, "url": url, "sha256": sha}
    return out


# Registry tooling for publishing verified images (#32): a localhost OCI registry and cosign.
# (prefix, GitHub repo, asset name for a given version, checksums file for a given version)
REGISTRY_TOOLS = [
    ("ZOT", "project-zot/zot", lambda v: "zot-darwin-arm64-minimal", lambda v: "checksums.sha256.txt"),
    ("COSIGN", "sigstore/cosign", lambda v: "cosign-darwin-arm64", lambda v: "cosign_checksums.txt"),
]


def registry_assets(old: dict, latest: bool) -> dict[str, dict]:
    """{prefix: {version, url, sha256}}; GitHub asset digest == the release's checksums file."""
    out = {}
    for prefix, repo, asset, sums in REGISTRY_TOOLS:
        v = github_latest(repo).lstrip("v") if latest or f"{prefix}_VERSION" not in old \
            else old[f"{prefix}_VERSION"]
        a = github_asset(repo, f"v{v}", asset(v), sums(v))
        out[prefix] = {"version": v, **a}
    return out


def _block(assets: dict[str, dict]) -> str:
    return "".join(f"\n{p}_VERSION={a['version']}\n{p}_URL={a['url']}\n{p}_SHA256={a['sha256']}\n"
                   for p, a in assets.items())


def _kept(old: dict, prefixes: list[str]) -> dict[str, dict] | None:
    """The committed pins for these prefixes, or None if any is missing."""
    try:
        return {p: {"version": old[f"{p}_VERSION"], "url": old[f"{p}_URL"], "sha256": old[f"{p}_SHA256"]}
                for p in prefixes}
    except KeyError:
        return None


def cmd_toolchain_pin(args) -> None:
    old = load_pins()
    latest = args.latest
    only = getattr(args, "only", None)
    if only in ("gnupg", "registry"):
        # Re-derive only the named block (neither needs gpg); keep the other, already-verified
        # pins exactly as committed. This is how a Mac without a toolchain gpg gets its first
        # GnuPG pins, and how the registry tools are pinned or bumped on their own.
        def kept(p: str) -> dict:
            return {"url": old[f"{p}_URL"], "sha256": old[f"{p}_SHA256"]}
        tart_v, plugin_v = old["TART_VERSION"], old["PACKER_PLUGIN_TART_VERSION"]
        uv_v, packer_v = old["UV_VERSION"], old["PACKER_VERSION"]
        tart, plugin, uv, packer = kept("TART"), kept("PACKER_PLUGIN_TART"), kept("UV"), kept("PACKER")
    else:
        tart_v = github_latest("openai/tart") if latest else old["TART_VERSION"]
        plugin_v = (github_latest("cirruslabs/packer-plugin-tart").lstrip("v") if latest
                    else old["PACKER_PLUGIN_TART_VERSION"])
        uv_v = github_latest("astral-sh/uv") if latest else old["UV_VERSION"]
        packer_v = (get_json("https://api.releases.hashicorp.com/v1/releases/packer/latest")["version"]
                    if latest else old["PACKER_VERSION"])

        tart = github_asset("openai/tart", tart_v, "tart.tar.gz", f"tart_{tart_v}_checksums.txt")
        plugin_zip = f"packer-plugin-tart_v{plugin_v}_x5.0_darwin_arm64.zip"
        plugin = github_asset("cirruslabs/packer-plugin-tart", f"v{plugin_v}", plugin_zip,
                              f"packer-plugin-tart_v{plugin_v}_SHA256SUMS")
        uv = github_asset("astral-sh/uv", uv_v, "uv-aarch64-apple-darwin.tar.gz",
                          "uv-aarch64-apple-darwin.tar.gz.sha256")
        packer = packer_asset(packer_v)
    gnupg_prefixes = [p for p, _, _ in GNUPG_COMPONENTS]
    registry_prefixes = [p for p, *_ in REGISTRY_TOOLS]
    gnupg = (_kept(old, gnupg_prefixes) if only == "registry" else None) or gnupg_assets(old, latest)
    registry = (_kept(old, registry_prefixes) if only == "gnupg" else None) or registry_assets(old, latest)
    gnupg_body = _block(gnupg) + _block(registry)

    # A version bump invalidates a Team ID pin only if the signer actually changes;
    # keep pins and let bootstrap enforce them.
    body = f"""
TART_VERSION={tart_v}
TART_URL={tart["url"]}
TART_SHA256={tart["sha256"]}
TART_TEAM_ID={old.get("TART_TEAM_ID", "")}

PACKER_VERSION={packer_v}
PACKER_URL={packer["url"]}
PACKER_SHA256={packer["sha256"]}
PACKER_TEAM_ID={old.get("PACKER_TEAM_ID", "")}

PACKER_PLUGIN_TART_VERSION={plugin_v}
PACKER_PLUGIN_TART_URL={plugin["url"]}
PACKER_PLUGIN_TART_SHA256={plugin["sha256"]}

UV_VERSION={uv_v}
UV_URL={uv["url"]}
UV_SHA256={uv["sha256"]}
"""
    PINS.write_text(PINS_HEADER + body + gnupg_body)
    log(f"wrote {PINS.relative_to(ROOT)}: tart {tart_v}, packer {packer_v}, "
        f"plugin {plugin_v}, uv {uv_v}, gnupg {gnupg['GNUPG']['version']}, "
        f"zot {registry['ZOT']['version']}, cosign {registry['COSIGN']['version']}. "
        f"Review the diff, commit, then run tools/bootstrap.sh")
    if plugin_v != old["PACKER_PLUGIN_TART_VERSION"]:
        warn("plugin version changed: update required_plugins in packer/*/*.pkr.hcl to match")


def toolchain() -> dict:
    def version(cmd: list[str]) -> str | None:
        try:
            return subprocess.run(cmd, capture_output=True, text=True).stdout.strip() or None
        except FileNotFoundError:
            return None

    app = TOOLCHAIN / "tart.app"
    installed = TOOLCHAIN / "INSTALLED"
    return {
        "host_os": platform.platform(),
        "tart": version(["tart", "--version"]),
        "tart_signature": ({"path": str(app.relative_to(ROOT)), **verify_app(app)}
                           if app.exists() and platform.system() == "Darwin" else None),
        "packer": version(["packer", "version"]),
        "uv": version(["uv", "--version"]),
        "gpg": gpg.gpg_version(),
        "pins_sha256": sha256_file(PINS),
        "installed": installed.read_text().splitlines() if installed.exists() else None,
    }


def cmd_preflight(_args) -> None:
    """Fail unless the repo-local toolchain is first on PATH and matches config/toolchain.env."""
    pins = load_pins()
    problems = []
    bindir = (TOOLCHAIN / "bin").resolve()
    for tool in ("tart", "packer", "uv", "gpg"):
        found = shutil.which(tool)
        if not found or Path(found).parent.resolve() != bindir:
            problems.append(f"{tool} resolves to {found}, not {bindir}/{tool}")
    tc = toolchain()
    if tc["tart"] != pins["TART_VERSION"]:
        problems.append(f"tart {tc['tart']} != pinned {pins['TART_VERSION']}")
    if not tc["packer"] or f"v{pins['PACKER_VERSION']}" not in tc["packer"]:
        problems.append(f"packer {tc['packer']!r} != pinned {pins['PACKER_VERSION']}")
    if not tc["uv"] or pins["UV_VERSION"] not in tc["uv"]:
        problems.append(f"uv {tc['uv']!r} != pinned {pins['UV_VERSION']}")
    if not tc["gpg"] or not tc["gpg"].endswith(f" {pins['GNUPG_VERSION']}"):
        problems.append(f"gpg {tc['gpg']!r} != pinned {pins['GNUPG_VERSION']}")
    stamp = dict(ln.split(" ", 1) for ln in (tc["installed"] or []) if ln.startswith("pins_sha256 "))
    if stamp.get("pins_sha256") != tc["pins_sha256"]:
        problems.append("config/toolchain.env changed since the last tools/bootstrap.sh")
    sig = tc["tart_signature"] or {}
    if "team_id" not in sig:
        problems.append(f"could not verify Tart.app signature: {sig}")
    elif pins.get("TART_TEAM_ID") and sig["team_id"] != pins["TART_TEAM_ID"]:
        problems.append(f"Tart.app Team ID {sig['team_id']} != pinned {pins['TART_TEAM_ID']}")
    elif not pins.get("TART_TEAM_ID"):
        warn(f"Tart.app signed by {sig['signer']}; confirm and pin TART_TEAM_ID")
    if problems:
        raise VerifyError("; ".join(problems) + " (run tools/bootstrap.sh; scripts use scripts/env.sh)")
    log(f"toolchain ok: tart {tc['tart']}, {tc['packer']}, {tc['uv']}, {tc['gpg']}")
