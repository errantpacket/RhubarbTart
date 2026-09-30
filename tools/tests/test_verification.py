"""Self-tests: signature, hash and version checks the resolver relies on (Ed25519, NAR, dpkg, OpenPGP, the toolchain pins, the content-addressed cache, GitHub release digests)."""

import os
import tempfile
from pathlib import Path

from rhubarb.apt import dpkg_cmp
from rhubarb.distsign import _file_msg, ed25519_verify
from rhubarb.nar import nar_sha256
from tests.support import TOOLS, check


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


def test_pgp_ed25519() -> None:
    """Pure-Python OpenPGP Ed25519 verifier used to pin GnuPG's own tarballs (#54), against a
    real signed file (gnupg.org swdb.lst, signed by Werner Koch's dist key) and tamperings."""
    from rhubarb import pgp_ed25519 as p
    from rhubarb.common import KEYS, VerifyError
    werner, niibe = "6DAA6E64A76D2840571B4902528897B826403ADA", "AC8E115BF73E2D8D47FA9908E98E9B2D19C6C8BD"
    td = TOOLS / "testdata"
    data, sig = (td / "gnupg-swdb.lst").read_bytes(), (td / "gnupg-swdb.lst.sig").read_bytes()
    keys = (KEYS / "gnupg-release-signing.asc").read_text()

    def rejects(label, fn, needle):
        try:
            fn()
            check(label, False)
        except VerifyError as e:
            check(label, needle in str(e))

    check("key file: Ed25519 primaries parsed, fingerprints computed",
          {werner, niibe} <= set(p.ed25519_primary_keys(keys)))
    check("real swdb.lst signature verifies by the pinned key",
          p.verify_detached(data, sig, keys, {werner, niibe}) == {werner})
    tampered = bytearray(data)
    tampered[100] ^= 1
    rejects("tampered data rejected", lambda: p.verify_detached(bytes(tampered), sig, keys, {werner}), "NOT valid")
    bad_s = bytearray(sig)
    bad_s[-3] ^= 1  # inside the s MPI: digest/prefix still match, only the curve check can catch it
    rejects("corrupted signature value rejected by the Ed25519 check",
            lambda: p.verify_detached(data, bytes(bad_s), keys, {werner}), "NOT valid")
    rejects("signer not pinned -> no valid signature",
            lambda: p.verify_detached(data, sig, keys, {niibe}), "no valid signature")
    sha1 = bytearray(sig)
    sha1[sig.index(b"\x04\x00\x16") + 3] = 2  # hash algorithm byte -> SHA-1
    rejects("SHA-1 signature refused", lambda: p.verify_detached(data, bytes(sha1), keys, {werner}), "unsupported")
    rejects("partial-length packet refused",
            lambda: p.verify_detached(data, b"\xc2\xe0" + sig[2:], keys, {werner}), "partial")
    rejects("non-signature packet refused",
            lambda: p.verify_detached(data, b"\xcb\x01\x00", keys, {werner}), "unexpected packet")


def test_toolchain_gpg() -> None:
    """On macOS resolve uses only the bootstrap-built gpg, never one from PATH (#54)."""
    from rhubarb import gpg
    from rhubarb.common import VerifyError
    orig = (gpg.platform.system, gpg.TOOLCHAIN_GPG)
    try:
        gpg.platform.system = lambda: "Darwin"
        with tempfile.TemporaryDirectory() as d:
            fake = Path(d) / "gpg"
            gpg.TOOLCHAIN_GPG = fake
            try:
                gpg.gpg_binary()
                check("darwin without toolchain gpg -> refuses (no PATH fallback)", False)
            except VerifyError as e:
                check("darwin without toolchain gpg -> refuses (no PATH fallback)", "bootstrap" in str(e))
            fake.write_text("")
            check("darwin uses the toolchain gpg", gpg.gpg_binary() == str(fake))
    finally:
        gpg.platform.system, gpg.TOOLCHAIN_GPG = orig


def test_content_addressed_cache() -> None:
    """Artifacts live at artifacts/<sha256>/<file>: two builds behind one file name (Chrome's
    unversioned URL) coexist; a legacy flat file is adopted only if its hash matches. (#67)"""
    import hashlib
    import resolve
    with tempfile.TemporaryDirectory() as d:
        cache = Path(d)
        old, new = b"chrome .58", b"chrome .93"
        e_old = {"file": "GoogleChrome.pkg", "sha256": hashlib.sha256(old).hexdigest()}
        e_new = {"file": "GoogleChrome.pkg", "sha256": hashlib.sha256(new).hexdigest()}
        check("same file name, different builds -> distinct cache paths",
              resolve.cached(cache, e_old) != resolve.cached(cache, e_new))
        (cache / "GoogleChrome.pkg").write_bytes(new)  # legacy flat file holding the .93 build
        p_old = resolve.cached(cache, e_old)
        check("legacy file with another hash is left alone (not adopted for .58)",
              not p_old.exists() and (cache / "GoogleChrome.pkg").exists())
        p_new = resolve.cached(cache, e_new)
        check("legacy file with the matching hash is adopted (moved, no re-download)",
              p_new.read_bytes() == new and not (cache / "GoogleChrome.pkg").exists()
              and p_new == cache / e_new["sha256"] / "GoogleChrome.pkg")
    # verify must not leave a wrong download under a hash it doesn't have (#67)
    src = (TOOLS / "resolve.py").read_text()
    body = src[src.index("def cmd_verify"):src.index("def ", src.index("def cmd_verify") + 5)]
    check("verify deletes a download whose hash doesn't match the lock",
          "path.unlink()" in body.split("differs from lock")[0].rsplit("fetch(e, path)", 1)[-1])


def test_github_release_resolver() -> None:
    """github-release (#83): pins a release asset by GitHub's sha256 digest and refuses anything
    weaker or unexpected. GitHub's API is faked."""
    from rhubarb import packages
    from rhubarb.common import VerifyError
    v = {"repo": "juice-shop/juice-shop", "asset": "juice-shop-{version}_node22_linux_arm64.tgz"}
    name = "juice-shop-20.2.0_node22_linux_arm64.tgz"
    url = f"https://github.com/juice-shop/juice-shop/releases/download/v20.2.0/{name}"

    def release(**asset):
        return {"tag_name": "v20.2.0", "assets": [{"name": name, "browser_download_url": url,
                                                    "digest": "sha256:" + "a" * 64, "size": 1, **asset}]}
    orig = packages.get_json
    try:
        packages.get_json = lambda u: release()
        e = packages.github_release(v)
        check("github-release: version, file, sha256 from GitHub's digest",
              e["version"] == "20.2.0" and e["file"] == name and e["sha256"] == "a" * 64
              and e["hash_sources"] == ["github-release-digest"])
        for label, asset, needle in (
                ("asset without a digest refused", {"digest": None}, "no sha256 digest"),
                ("asset from an unexpected host refused", {"browser_download_url": "https://evil.example/x.tgz"},
                 "unexpected asset URL"),
                ("missing asset refused", {"name": "other.tgz"}, "has no asset")):
            packages.get_json = lambda u, a=asset: release(**a)
            try:
                packages.github_release(v)
                check(f"github-release: {label}", False)
            except VerifyError as err:
                check(f"github-release: {label}", needle in str(err))
        try:
            packages.github_release({**v, "repo": "not a repo"})
            check("github-release: malformed repo refused", False)
        except VerifyError:
            check("github-release: malformed repo refused", True)
    finally:
        packages.get_json = orig
