"""macOS artifact signature checks (pkgutil / codesign / spctl / hdiutil). macOS hosts only."""

import platform
import plistlib
import re
import subprocess
import tempfile
import xml.etree.ElementTree as ET
from pathlib import Path

from .common import VerifyError, warn

TEAM_ID_RE = re.compile(r"\(([A-Z0-9]{10})\)")


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


def check_signature(pid: str, variant: dict, path: Path) -> dict:
    """Developer ID + notarization + Team ID (pinned in config/packages/<id>.json)."""
    sig = verify_dmg(path, variant["app"]) if variant["kind"] == "dmg" else verify_pkg(path)
    expected = variant.get("team_id")
    if expected and sig["team_id"] != expected:
        raise VerifyError(f"{pid}: Team ID {sig['team_id']} != pinned {expected}")
    if not expected:
        warn(f"{pid}: no team_id pinned in config/packages/{pid}.json; observed "
             f"{sig['team_id']} ({sig['signer']}). Confirm out-of-band and pin it.")
    return sig
