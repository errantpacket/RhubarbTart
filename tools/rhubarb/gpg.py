"""OpenPGP verification with a pinned key file AND a pinned primary-key fingerprint.

The key file comes from config/keys/ (committed); the fingerprint comes from the
package/base config. Both must agree with the signature, so swapping either the
key file or the upstream signature fails closed. Uses a throwaway GNUPGHOME.
"""

import shutil
import subprocess
import tempfile
from pathlib import Path

from .common import KEYS, VerifyError


def _require_gpg() -> None:
    if not shutil.which("gpg"):
        raise VerifyError("gpg is required for this resolver (resolve Linux profiles on any "
                          "machine with gnupg; the build host only needs the lock file)")


def _verify(keyfile: str, fpr: str, args: list[str]) -> str:
    _require_gpg()
    key = KEYS / keyfile
    if not key.exists():
        raise VerifyError(f"missing pinned key config/keys/{keyfile}")
    fpr = fpr.replace(" ", "").upper()
    with tempfile.TemporaryDirectory() as home:
        gpg = ["gpg", "--homedir", home, "--batch", "--no-tty", "--no-auto-key-retrieve"]
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
