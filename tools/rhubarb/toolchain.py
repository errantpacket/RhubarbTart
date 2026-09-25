"""Host toolchain: pins (config/toolchain.env), pin derivation, preflight."""

import platform
import shutil
import subprocess
import tempfile
from pathlib import Path
import re

from .common import ROOT, VerifyError, get_bytes, get_json, log, parse_sums, sha256_file, warn
from .macos import verify_app

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
