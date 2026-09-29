"""OpenPGP verification with a pinned key file AND a pinned primary-key fingerprint.

The key file comes from config/keys/ (committed); the fingerprint comes from the
package/base config. Both must agree with the signature, so swapping either the
key file or the upstream signature fails closed. Uses a throwaway GNUPGHOME and never starts
gpg-agent/keyboxd (verification needs neither; an autostarted agent outlives the temp home).

On macOS the gpg is the one tools/bootstrap.sh built from pinned source into .toolchain (#54),
never whatever is on PATH. Elsewhere (a Linux resolve host) the system gpg is used.
"""

import platform
import shutil
import subprocess
import tempfile
from pathlib import Path

from .common import KEYS, ROOT, VerifyError

TOOLCHAIN_GPG = ROOT / ".toolchain" / "bin" / "gpg"


def gpg_binary() -> str:
    if platform.system() == "Darwin":
        if not TOOLCHAIN_GPG.exists():
            raise VerifyError("the pinned gpg is missing from .toolchain; run ./tools/bootstrap.sh")
        return str(TOOLCHAIN_GPG)
    found = shutil.which("gpg")
    if not found:
        raise VerifyError("gpg is required for this resolver (install gnupg on this host)")
    return found


def gpg_version() -> str | None:
    """First line of `gpg --version` for the gpg resolve would use, or None if there is none."""
    try:
        res = subprocess.run([gpg_binary(), "--version"], capture_output=True, text=True)
    except (VerifyError, OSError):
        return None
    return res.stdout.split("\n", 1)[0].strip() or None


def _verify(keyfile: str, fpr: str, args: list[str]) -> str:
    binary = gpg_binary()
    key = KEYS / keyfile
    if not key.exists():
        raise VerifyError(f"missing pinned key config/keys/{keyfile}")
    fpr = fpr.replace(" ", "").upper()
    with tempfile.TemporaryDirectory() as home:
        gpg = [binary, "--homedir", home, "--batch", "--no-tty", "--no-autostart",
               "--no-auto-key-retrieve"]
        subprocess.run([*gpg, "--import", str(key)], check=True, capture_output=True)
        res = subprocess.run([*gpg, "--status-fd", "1", *args], capture_output=True)
    # Status lines may carry raw non-UTF-8 user IDs; only the hex fingerprint fields matter.
    out = res.stdout.decode("utf-8", "replace")
    valid = [ln.split() for ln in out.splitlines() if ln.startswith("[GNUPG:] VALIDSIG")]
    # VALIDSIG <sig-fpr> ... <primary-key-fpr> is the last field
    if res.returncode != 0 or not valid or valid[0][-1].upper() != fpr:
        err = res.stderr.decode("utf-8", "replace").strip()
        raise VerifyError(f"OpenPGP signature not valid for pinned key {fpr}\n{err}")
    return out


def verify_detached(data: Path, sig: Path, keyfile: str, fpr: str) -> None:
    _verify(keyfile, fpr, ["--verify", str(sig), str(data)])


def verify_clearsigned(signed: bytes, keyfile: str, fpr: str) -> bytes:
    """Verify an inline-signed document (e.g. apt InRelease); return the signed payload."""
    with tempfile.TemporaryDirectory() as tmp:
        src, out = Path(tmp, "in"), Path(tmp, "out")
        src.write_bytes(signed)
        _verify(keyfile, fpr, ["--output", str(out), "--decrypt", str(src)])
        return out.read_bytes()
