# /// script
# requires-python = ">=3.12"
# dependencies = []
# ///
"""Resolve, download, verify and lock every input that goes into a RhubarbTart image.

Subcommands:
  plan        Resolve latest versions from upstream metadata only (no downloads). Portable.
  preflight   Check the repo-local toolchain (.toolchain/) against config/toolchain.env.
  toolchain-pin  Re-derive config/toolchain.env hashes from upstream (two sources must agree).
  resolve     plan + download into the cache + verify hashes/signatures + write sources.lock.json.
  verify      Re-verify cached artifacts against sources.lock.json and build the guest stage dir.
  provenance  Emit a provenance record for a built VM (lock + toolchain + repo file hashes).

Trust model: every artifact must match a hash published by its vendor over TLS
(Apple CDN header, GitHub release digest) or be pinned in the lock file on first
resolve, AND carry a valid Apple-notarized Developer ID signature whose Team ID
matches config/sources.json.
"""

import argparse
import datetime as dt
import hashlib
import json
import os
import platform
import plistlib
import re
import shutil
import subprocess
import sys
import tempfile
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
CONFIG = ROOT / "config" / "sources.json"
LOCK = ROOT / "sources.lock.json"
CACHE = Path(os.environ.get("RHUBARB_CACHE", ROOT / "cache"))
STAGE = CACHE / "stage"

IPSW_ME = "https://api.ipsw.me/v4/device/{device}?type=ipsw"
APPLE_IPSW_HOST = "updates.cdn-apple.com"
ZAP_VERSIONS = "https://raw.githubusercontent.com/zaproxy/zap-admin/master/ZapVersions.xml"
ZAP_RELEASE = "https://api.github.com/repos/zaproxy/zaproxy/releases/tags/v{version}"
CHROME_VERSIONS = "https://versionhistory.googleapis.com/v1/chrome/platforms/mac/channels/stable/versions?pageSize=1"
TEAM_ID_RE = re.compile(r"\(([A-Z0-9]{10})\)")


class VerifyError(Exception):
    pass


def log(msg: str) -> None:
    print(f"[resolve] {msg}", file=sys.stderr)


def warn(msg: str) -> None:
    print(f"[resolve] WARNING: {msg}", file=sys.stderr)


# --------------------------------------------------------------------------- http


def _request(url: str, method: str = "GET") -> urllib.request.Request:
    headers = {"User-Agent": "RhubarbTart-resolver"}
    host = urllib.parse.urlparse(url).hostname or ""
    if host == "api.github.com" and os.environ.get("GITHUB_TOKEN"):
        headers["Authorization"] = f"Bearer {os.environ['GITHUB_TOKEN']}"
    return urllib.request.Request(url, headers=headers, method=method)


def get_bytes(url: str) -> bytes:
    with urllib.request.urlopen(_request(url), timeout=60) as r:
        return r.read()


def get_json(url: str):
    return json.loads(get_bytes(url))


def head(url: str) -> dict[str, str]:
    with urllib.request.urlopen(_request(url, "HEAD"), timeout=60) as r:
        return {k.lower(): v for k, v in r.headers.items()}


def download(url: str, dest: Path) -> None:
    """Resumable download via curl (handles the ~20 GB IPSW far better than urllib)."""
    dest.parent.mkdir(parents=True, exist_ok=True)
    part = dest.with_suffix(dest.suffix + ".part")
    log(f"downloading {url}")
    subprocess.run(
        ["curl", "--fail", "--location", "--proto", "=https", "--tlsv1.2",
         "--retry", "5", "--continue-at", "-", "--output", str(part), url],
        check=True,
    )
    part.rename(dest)


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        while chunk := f.read(8 * 1024 * 1024):
            h.update(chunk)
    return h.hexdigest()


# ---------------------------------------------------------------- upstream resolvers


def plan_macos(cfg: dict) -> dict:
    """Newest restore image for the configured major version, hash cross-checked with Apple.

    NOTE: `tart create --from-ipsw=latest` follows Apple's catalog, which already
    points at the next major release; we filter on the major explicitly.
    """
    fw = get_json(IPSW_ME.format(device=cfg["device"]))["firmwares"]
    candidates = [f for f in fw if int(f["version"].split(".")[0]) == cfg["major"]]
    if cfg.get("pin_build"):
        candidates = [f for f in candidates if f["buildid"] == cfg["pin_build"]]
    if not candidates:
        raise VerifyError(f"no IPSW for macOS {cfg['major']} (pin_build={cfg.get('pin_build')})")
    best = max(candidates, key=lambda f: tuple(int(p) for p in f["version"].split(".")))

    url = best["url"]
    if urllib.parse.urlparse(url).hostname != APPLE_IPSW_HOST:
        raise VerifyError(f"IPSW URL is not on {APPLE_IPSW_HOST}: {url}")

    # Apple's CDN publishes the object's digest as S3 metadata; this is the
    # authoritative hash. ipsw.me is used only for discovery + a second opinion.
    hdrs = head(url)
    apple_sha = hdrs.get("x-amz-meta-digest-sha256")
    if not apple_sha:
        raise VerifyError("Apple CDN did not return x-amz-meta-digest-sha256")
    if best.get("sha256sum") and best["sha256sum"] != apple_sha:
        raise VerifyError(f"IPSW hash disagreement: apple={apple_sha} ipsw.me={best['sha256sum']}")

    return {
        "version": best["version"],
        "build": best["buildid"],
        "url": url,
        "file": Path(urllib.parse.urlparse(url).path).name,
        "sha256": apple_sha,
        "size": int(hdrs.get("content-length", 0)),
        "hash_sources": ["apple-cdn:x-amz-meta-digest-sha256"]
        + (["ipsw.me"] if best.get("sha256sum") else []),
    }


def plan_chrome(pkg: dict) -> dict:
    # The enterprise pkg URL is unversioned, so version + hash are only known
    # after download (see cmd_resolve). Record the advertised stable version.
    advertised = get_json(CHROME_VERSIONS)["versions"][0]["version"]
    return {"url": pkg["url"], "file": "GoogleChrome.pkg", "advertised_version": advertised,
            "hash_sources": ["tofu:downloaded"]}


def plan_zap(pkg: dict) -> dict:
    root = ET.fromstring(get_bytes(ZAP_VERSIONS))
    version = root.findtext("core/version")
    if not version:
        raise VerifyError("could not read core/version from ZapVersions.xml")
    name = f"ZAP_{version}_{pkg['arch']}.dmg"
    release = get_json(ZAP_RELEASE.format(version=version))
    asset = next((a for a in release["assets"] if a["name"] == name), None)
    if asset is None:
        raise VerifyError(f"release v{version} has no asset {name}")
    digest = asset.get("digest") or ""
    if not digest.startswith("sha256:"):
        raise VerifyError(f"GitHub did not publish a sha256 digest for {name}")
    return {"version": version, "url": asset["browser_download_url"], "file": name,
            "sha256": digest.removeprefix("sha256:"), "size": asset["size"],
            "hash_sources": ["github-release-digest"]}


def plan_local(pkg: dict) -> dict:
    path = ROOT / pkg["path"]
    return {"url": None, "file": path.name, "local_path": pkg["path"],
            "hash_sources": ["tofu:local-file"]}


RESOLVERS = {"chrome": plan_chrome, "zap": plan_zap, "local": plan_local}


# ------------------------------------------------------------ signature checks (macOS)


def _require_macos() -> None:
    if platform.system() != "Darwin":
        raise VerifyError("signature verification needs macOS (pkgutil/codesign/spctl)")


def verify_pkg(path: Path) -> dict:
    _require_macos()
    out = subprocess.run(["pkgutil", "--check-signature", str(path)],
                         capture_output=True, text=True)
    if out.returncode != 0 or "Developer ID Installer" not in out.stdout:
        raise VerifyError(f"{path.name}: not signed with a Developer ID Installer cert\n{out.stdout}")
    if "trusted by the Apple notary service" not in out.stdout:
        raise VerifyError(f"{path.name}: not notarized\n{out.stdout}")
    signer = next(ln.strip() for ln in out.stdout.splitlines() if "Developer ID Installer" in ln)
    team = TEAM_ID_RE.search(signer)
    if not team:
        raise VerifyError(f"{path.name}: no Team ID in signer line: {signer}")
    spctl = subprocess.run(["spctl", "--assess", "--type", "install", "-vv", str(path)],
                           capture_output=True, text=True)
    if spctl.returncode != 0:
        raise VerifyError(f"{path.name}: Gatekeeper rejected install\n{spctl.stderr}")
    return {"signer": re.sub(r"^\d+\.\s*", "", signer),
            "team_id": team.group(1), "notarized": True}


def verify_app(app: Path) -> dict:
    _require_macos()
    subprocess.run(["codesign", "--verify", "--deep", "--strict", str(app)], check=True)
    info = subprocess.run(["codesign", "-dv", "--verbose=2", str(app)],
                          capture_output=True, text=True).stderr
    spctl = subprocess.run(["spctl", "--assess", "--type", "execute", "-vv", str(app)],
                           capture_output=True, text=True)
    if spctl.returncode != 0 or "Notarized Developer ID" not in spctl.stderr:
        raise VerifyError(f"{app.name}: Gatekeeper/notarization check failed\n{spctl.stderr}")
    team = re.search(r"^TeamIdentifier=(\S+)", info, re.M)
    auth = re.search(r"^Authority=(Developer ID Application:.*)$", info, re.M)
    if not team or not auth:
        raise VerifyError(f"{app.name}: missing TeamIdentifier/Developer ID authority")
    return {"signer": auth.group(1), "team_id": team.group(1), "notarized": True}


def verify_dmg(path: Path, app_name: str) -> dict:
    _require_macos()
    with tempfile.TemporaryDirectory() as mnt:
        subprocess.run(["hdiutil", "attach", "-nobrowse", "-readonly", "-noautoopen",
                        "-mountpoint", mnt, str(path)], check=True, capture_output=True)
        try:
            app = Path(mnt) / app_name
            result = verify_app(app)
            plist = plistlib.loads((app / "Contents" / "Info.plist").read_bytes())
            result["bundle_version"] = plist.get("CFBundleShortVersionString")
            result["bundle_id"] = plist.get("CFBundleIdentifier")
            return result
        finally:
            subprocess.run(["hdiutil", "detach", mnt], check=False, capture_output=True)


def pkg_ref_version(path: Path, ref: str) -> str | None:
    """Read <pkg-ref id=ref version=...> from a flat pkg's Distribution file."""
    with tempfile.TemporaryDirectory() as tmp:
        subprocess.run(["xar", "-xf", str(path), "-C", tmp, "Distribution"],
                       check=True, capture_output=True)
        dist = ET.parse(Path(tmp) / "Distribution").getroot()
    for el in dist.iter("pkg-ref"):
        if el.get("id") == ref and el.get("version"):
            return el.get("version")
    return None


def check_signature(pkg_cfg: dict, path: Path) -> dict:
    sig = verify_dmg(path, pkg_cfg["app"]) if pkg_cfg["kind"] == "dmg" else verify_pkg(path)
    expected = pkg_cfg.get("team_id")
    if expected and sig["team_id"] != expected:
        raise VerifyError(f"{pkg_cfg['id']}: Team ID {sig['team_id']} != pinned {expected}")
    if not expected:
        warn(f"{pkg_cfg['id']}: no team_id pinned in config/sources.json; observed "
             f"{sig['team_id']} ({sig['signer']}). Confirm out-of-band and pin it.")
    return sig


# ----------------------------------------------------------------------- commands


def load_config() -> dict:
    return json.loads(CONFIG.read_text())


def cmd_plan(_args) -> dict:
    cfg = load_config()
    plan = {"macos": plan_macos(cfg["macos"]), "packages": {}}
    for pkg in cfg["packages"]:
        plan["packages"][pkg["id"]] = RESOLVERS[pkg["resolver"]](pkg)
    print(json.dumps(plan, indent=2))
    return plan


def fetch(entry: dict, dest: Path) -> None:
    if dest.exists():
        return
    if entry.get("local_path"):
        src = ROOT / entry["local_path"]
        if not src.exists():
            raise VerifyError(f"missing local file {src} (download it from the vendor portal)")
        shutil.copy2(src, dest)
    else:
        download(entry["url"], dest)


def cmd_resolve(args) -> None:
    cfg = load_config()
    plan = cmd_plan(args)
    CACHE.mkdir(parents=True, exist_ok=True)

    mac = plan["macos"]
    if not args.skip_ipsw:
        ipsw = CACHE / mac["file"]
        fetch(mac, ipsw)
        if (got := sha256_file(ipsw)) != mac["sha256"]:
            ipsw.unlink()
            raise VerifyError(f"IPSW sha256 mismatch: {got} != {mac['sha256']}")
        log(f"IPSW ok: macOS {mac['version']} ({mac['build']})")

    for pkg in cfg["packages"]:
        entry = plan["packages"][pkg["id"]]
        path = CACHE / entry["file"]
        if path.exists() and "sha256" not in entry:
            path.unlink()  # unversioned upstream: always re-fetch on resolve
        fetch(entry, path)
        got = sha256_file(path)
        if "sha256" in entry and got != entry["sha256"]:
            path.unlink()
            raise VerifyError(f"{pkg['id']}: sha256 mismatch {got} != {entry['sha256']}")
        entry["sha256"], entry["size"] = got, path.stat().st_size
        entry["signature"] = check_signature(pkg, path)
        if pkg["resolver"] == "chrome":
            entry["version"] = pkg_ref_version(path, pkg["pkg_ref"])
            if entry["version"] != entry["advertised_version"]:
                warn(f"chrome pkg is {entry['version']}, versionhistory says "
                     f"{entry['advertised_version']} (rollout lag is normal)")
        elif pkg["kind"] == "dmg" and entry["signature"].get("bundle_version") != entry["version"]:
            warn(f"{pkg['id']}: bundle version {entry['signature'].get('bundle_version')} "
                 f"!= release {entry['version']}")
        log(f"{pkg['id']} ok: {entry.get('version', '?')} sha256={got[:16]}…")

    lock = {
        "schema": 1,
        "resolved_at": dt.datetime.now(dt.UTC).isoformat(timespec="seconds"),
        "resolved_on": toolchain(),
        **plan,
    }
    LOCK.write_text(json.dumps(lock, indent=2) + "\n")
    log(f"wrote {LOCK.relative_to(ROOT)} — review the diff and commit it")


def inputs_sha256(lock: dict) -> str:
    """Identity of the build inputs: artifact hashes + signer Team IDs, nothing time-dependent.

    Re-resolving to the same artifacts yields the same value (and so the same VM name),
    even though sources.lock.json itself records when and where it was resolved.
    """
    lines = [f"macos {lock['macos']['build']} {lock['macos']['sha256']}"]
    for pid, entry in sorted(lock["packages"].items()):
        lines.append(f"{pid} {entry['sha256']} {entry['signature']['team_id']}")
    return hashlib.sha256("\n".join(lines).encode()).hexdigest()


def cmd_verify(_args) -> None:
    """Check the cache against the lock and assemble the directory uploaded to the guest."""
    if not LOCK.exists():
        raise VerifyError("sources.lock.json missing; run `resolve` first")
    lock = json.loads(LOCK.read_text())
    cfg = {p["id"]: p for p in load_config()["packages"]}

    mac = lock["macos"]
    ipsw = CACHE / mac["file"]
    fetch(mac, ipsw)
    if sha256_file(ipsw) != mac["sha256"]:
        raise VerifyError(f"cached IPSW does not match lock ({ipsw})")

    if STAGE.exists():
        shutil.rmtree(STAGE)
    STAGE.mkdir(parents=True)
    sums, manifest = [], []
    for pid, entry in lock["packages"].items():
        path = CACHE / entry["file"]
        fetch(entry, path)
        if sha256_file(path) != entry["sha256"]:
            raise VerifyError(f"{pid}: cached file differs from lock. If upstream moved "
                              "(e.g. Chrome's unversioned URL), run `resolve` again.")
        sig = check_signature(cfg[pid], path)
        if sig["team_id"] != entry["signature"]["team_id"]:
            raise VerifyError(f"{pid}: Team ID changed since lock ({sig['team_id']})")
        shutil.copy2(path, STAGE / entry["file"])
        sums.append(f"{entry['sha256']}  {entry['file']}")
        manifest.append("\t".join([pid, cfg[pid]["kind"], entry["file"],
                                   entry["signature"]["team_id"], cfg[pid].get("app", "-")]))
        log(f"{pid} ok")

    (STAGE / "SHA256SUMS").write_text("\n".join(sums) + "\n")
    (STAGE / "packages.tsv").write_text("\n".join(manifest) + "\n")
    shutil.copy2(LOCK, STAGE / "sources.lock.json")
    print(json.dumps({"ipsw_path": str(ipsw), "stage_dir": str(STAGE),
                      "macos_build": mac["build"], "macos_version": mac["version"],
                      "inputs_sha256": inputs_sha256(lock)}))


# ------------------------------------------------------------------------- toolchain

PINS = ROOT / "config" / "toolchain.env"
TOOLCHAIN = ROOT / ".toolchain"
HASHICORP_KEY = ROOT / "config" / "keys" / "hashicorp-72D7468F.asc"
HASHICORP_FPR = "C874011F0AB405110D02105534365D9472D7468F"
PIN_RE = re.compile(r"^([A-Z0-9_]+)=([A-Za-z0-9._:/+-]*)$")
PINS_HEADER = """\
# Pinned host toolchain, installed into ./.toolchain by tools/bootstrap.sh (no Homebrew).
# Update with: uv run tools/resolve.py toolchain-pin [--latest]  (then review the diff)
#
# Each SHA256 was accepted only when two upstream views agreed:
#   GitHub assets: release checksums file == GitHub asset digest
#   Packer:        HashiCorp SHA256SUMS, GPG-verified with config/keys/hashicorp-72D7468F.asc
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


def parse_sums(text: str) -> dict[str, str]:
    sums = {}
    for line in text.splitlines():
        parts = line.split()
        if len(parts) == 2:
            sums[parts[1].lstrip("*")] = parts[0].lower()
    return sums


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
    if not shutil.which("gpg"):
        raise VerifyError("gpg is required to verify HashiCorp's signature (run this on any "
                          "machine with gnupg; the result is just config/toolchain.env)")
    base = f"https://releases.hashicorp.com/packer/{version}/packer_{version}"
    with tempfile.TemporaryDirectory() as home:
        sums, sig = Path(home, "SHA256SUMS"), Path(home, "SHA256SUMS.sig")
        sums.write_bytes(get_bytes(f"{base}_SHA256SUMS"))
        sig.write_bytes(get_bytes(f"{base}_SHA256SUMS.72D7468F.sig"))
        gpg = ["gpg", "--homedir", home, "--batch", "--no-tty"]
        subprocess.run([*gpg, "--import", str(HASHICORP_KEY)], check=True, capture_output=True)
        res = subprocess.run([*gpg, "--status-fd", "1", "--verify", str(sig), str(sums)],
                             capture_output=True, text=True)
        valid = [ln.split() for ln in res.stdout.splitlines() if ln.startswith("[GNUPG:] VALIDSIG")]
        if res.returncode != 0 or not valid or valid[0][-1] != HASHICORP_FPR:
            raise VerifyError(f"HashiCorp signature check failed for packer {version}\n{res.stderr}")
        name = f"packer_{version}_darwin_arm64.zip"
        sha = parse_sums(sums.read_text()).get(name)
    if not sha:
        raise VerifyError(f"{name} not listed in SHA256SUMS")
    return {"url": f"https://releases.hashicorp.com/packer/{version}/{name}", "sha256": sha}


def cmd_toolchain_pin(args) -> None:
    old = load_pins()
    latest = args.latest
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
    PINS.write_text(PINS_HEADER + body)
    log(f"wrote {PINS.relative_to(ROOT)}: tart {tart_v}, packer {packer_v}, "
        f"plugin {plugin_v}, uv {uv_v}. Review the diff, commit, then run tools/bootstrap.sh")
    if plugin_v != old["PACKER_PLUGIN_TART_VERSION"]:
        warn("plugin version changed: update required_plugins in packer/*.pkr.hcl to match")


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
        "pins_sha256": sha256_file(PINS),
        "installed": installed.read_text().splitlines() if installed.exists() else None,
    }


def cmd_preflight(_args) -> None:
    """Fail unless the repo-local toolchain is first on PATH and matches config/toolchain.env."""
    pins = load_pins()
    problems = []
    bindir = (TOOLCHAIN / "bin").resolve()
    for tool in ("tart", "packer", "uv"):
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
    log(f"toolchain ok: tart {tc['tart']}, {tc['packer']}, {tc['uv']}")


def cmd_provenance(args) -> None:
    tracked = sorted(
        p for d in ("config", "packer", "guest", "scripts", "tools") for p in (ROOT / d).rglob("*")
        if p.is_file()
    )
    vm_info = subprocess.run(["tart", "get", args.vm, "--format", "json"],
                             capture_output=True, text=True)
    git = subprocess.run(["git", "-C", str(ROOT), "rev-parse", "HEAD"],
                         capture_output=True, text=True)
    record = {
        "vm": args.vm,
        "built_at": dt.datetime.now(dt.UTC).isoformat(timespec="seconds"),
        "git_commit": git.stdout.strip() or None,
        "git_dirty": bool(subprocess.run(["git", "-C", str(ROOT), "status", "--porcelain"],
                                         capture_output=True, text=True).stdout.strip()),
        "toolchain": toolchain(),
        "tart_vm": json.loads(vm_info.stdout) if vm_info.returncode == 0 else None,
        "lock_sha256": sha256_file(LOCK),
        "inputs_sha256": inputs_sha256(json.loads(LOCK.read_text())),
        "lock": json.loads(LOCK.read_text()),
        "build_files": {str(p.relative_to(ROOT)): sha256_file(p) for p in tracked},
    }
    out = ROOT / "out" / f"{args.vm}.provenance.json"
    out.parent.mkdir(exist_ok=True)
    out.write_text(json.dumps(record, indent=2) + "\n")
    print(out)


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("plan").set_defaults(fn=cmd_plan)
    sub.add_parser("preflight").set_defaults(fn=cmd_preflight)
    tp = sub.add_parser("toolchain-pin")
    tp.add_argument("--latest", action="store_true", help="bump to the newest upstream releases")
    tp.set_defaults(fn=cmd_toolchain_pin)
    r = sub.add_parser("resolve")
    r.add_argument("--skip-ipsw", action="store_true", help="don't download the ~20 GB IPSW now")
    r.set_defaults(fn=cmd_resolve)
    sub.add_parser("verify").set_defaults(fn=cmd_verify)
    p = sub.add_parser("provenance")
    p.add_argument("vm")
    p.set_defaults(fn=cmd_provenance)
    args = ap.parse_args()
    try:
        args.fn(args)
    except (VerifyError, subprocess.CalledProcessError) as e:
        sys.exit(f"[resolve] FAILED: {e}")


if __name__ == "__main__":
    main()
