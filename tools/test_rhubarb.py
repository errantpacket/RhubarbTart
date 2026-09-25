# /// script
# requires-python = ">=3.12"
# dependencies = []
# ///
"""Offline self-tests for the security-relevant parts of tools/rhubarb (run by tools/check.sh).

Each expected value was produced by the reference implementation, not by this code:
  - Ed25519: RFC 8032 §7.1 test vectors 1 and 2
  - NAR: `nix hash path` (Nix 2.28.4) on the exact tree built by _nar_tree()
  - dpkg ordering: deb-version(7) semantics as implemented by dpkg --compare-versions
"""

import os
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from rhubarb.apt import dpkg_cmp  # noqa: E402
from rhubarb.distsign import _file_msg, ed25519_verify  # noqa: E402
from rhubarb.nar import nar_sha256  # noqa: E402

FAILS = []


def check(name: str, cond: bool) -> None:
    print(f"  {'ok  ' if cond else 'FAIL'}  {name}")
    if not cond:
        FAILS.append(name)


def test_ed25519() -> None:
    v1_pub = bytes.fromhex("d75a980182b10ab7d54bfed3c964073a0ee172f3daa62325af021a68f707511a")
    v1_sig = bytes.fromhex("e5564300c360ac729086e2cc806e828a84877f1eb8e5d974d873e065224901555fb8821590a33bacc61e39701cf9b46bd25bf5f0595bbe24655141438e7a100b")
    v2_pub = bytes.fromhex("3d4017c3e843895a92b70aa74d1b7ebc9c982ccf2ec4968cc0cd55f12af4660c")
    v2_msg = bytes.fromhex("72")
    v2_sig = bytes.fromhex("92a009a9f0d4cab8720e820b5f642540a2b27b5416503f8fb3762223ebdb69da085ac1e43e15996e458f3613d0f11d8c387b2eaeb4302aeeb00d291612bb0c00")
    check("ed25519 RFC8032 vector 1 verifies", ed25519_verify(v1_pub, b"", v1_sig))
    check("ed25519 RFC8032 vector 2 verifies", ed25519_verify(v2_pub, v2_msg, v2_sig))
    check("ed25519 rejects wrong message", not ed25519_verify(v2_pub, b"\x73", v2_sig))
    bad = bytearray(v2_sig)
    bad[10] ^= 1
    check("ed25519 rejects flipped signature bit", not ed25519_verify(v2_pub, v2_msg, bytes(bad)))
    check("ed25519 rejects wrong key", not ed25519_verify(v1_pub, v2_msg, v2_sig))
    check("ed25519 rejects S >= L (malleability)",
          not ed25519_verify(v1_pub, b"", v1_sig[:32] + (2**253).to_bytes(32, "little")))
    check("distsign message = blake2s256 || len_le64",
          _file_msg(b"abc")[-8:] == (3).to_bytes(8, "little") and len(_file_msg(b"abc")) == 40)


def _nar_tree(root: Path) -> None:
    (root / "sub" / "deeper").mkdir(parents=True)
    (root / "a.txt").write_bytes(b"hello")
    (root / "empty").write_bytes(b"")
    (root / "run.sh").write_bytes(b"#!/bin/sh\necho hi\n")
    os.chmod(root / "run.sh", 0o755)
    for f in ("a.txt", "empty"):
        os.chmod(root / f, 0o644)
    os.symlink("a.txt", root / "link")
    os.symlink("../missing", root / "sub" / "dangling")
    (root / "sub" / "eight").write_bytes(b"12345678")
    (root / "sub" / "deeper" / "big").write_bytes(b"x" * 1000)
    (root / "sub" / "Z-upper").write_bytes(b"")
    (root / "sub" / "a-lower").write_bytes(b"")


def test_nar() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp, "tree")
        root.mkdir()
        _nar_tree(root)
        check("NAR hash matches Nix 2.28.4 on reference tree",
              nar_sha256(root) == "sha256-aCxd7E/HSWSh4hWCo4ABpEtn3rZaP1ALGM2FAOBfR/Y=")
        os.chmod(root / "run.sh", 0o644)
        check("NAR hash changes when an executable bit changes",
              nar_sha256(root) != "sha256-aCxd7E/HSWSh4hWCo4ABpEtn3rZaP1ALGM2FAOBfR/Y=")


def test_dpkg() -> None:
    cases = [("1.0", "1.0", 0), ("1.0", "1.1", -1), ("1.0~rc1", "1.0", -1), ("1:0.1", "2.0", 1),
             ("1.0-1", "1.0-2", -1), ("154.0.8037.57-1", "154.0.8037.100-1", -1),
             ("2026.7.1377.0", "2026.7.1376.0", 1), ("1.0a", "1.0", 1), ("1.0+b1", "1.0", 1)]
    for a, b, want in cases:
        check(f"dpkg_cmp({a}, {b}) == {want}", dpkg_cmp(a, b) == want)


if __name__ == "__main__":
    for t in (test_ed25519, test_nar, test_dpkg):
        print(t.__name__)
        t()
    if FAILS:
        sys.exit(f"{len(FAILS)} test(s) failed")
    print("all rhubarb self-tests passed")
