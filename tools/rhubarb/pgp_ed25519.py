"""Minimal OpenPGP verifier for one case: detached v4 signatures over a binary document, made by
a pinned v4 Ed25519 *primary* key (legacy EdDSA, algorithm 22), hashed with SHA-2.

It exists to verify GnuPG's own release tarballs (dual-signed with Ed25519 release keys) when
pinning the toolchain gpg: that must work before any trusted gpg exists (#54). Anything outside
that case fails closed: other packet types, versions, algorithms, hashes, subkeys, partial or
indeterminate lengths, and unknown critical subpackets.
"""

import base64
import hashlib
import re

from .common import VerifyError
from .distsign import ed25519_verify

ED25519_OID = bytes.fromhex("2b06010401da470f01")  # 1.3.6.1.4.1.11591.15.1
HASHES = {8: hashlib.sha256, 9: hashlib.sha384, 10: hashlib.sha512}  # no SHA-1 / MD5
_SUB_CREATED, _SUB_ISSUER_FPR = 2, 33
_KNOWN_CRITICAL = {_SUB_CREATED}


def _packets(b: bytes):
    i = 0
    while i < len(b):
        h = b[i]
        i += 1
        if not h & 0x80:
            raise VerifyError("OpenPGP: bad packet header")
        if h & 0x40:
            tag, ln = h & 0x3F, b[i]
            i += 1
            if 192 <= ln < 224:
                ln = ((ln - 192) << 8) + b[i] + 192
                i += 1
            elif ln == 255:
                ln = int.from_bytes(b[i:i + 4], "big")
                i += 4
            elif ln >= 224:
                raise VerifyError("OpenPGP: partial body lengths not supported")
        else:
            tag, lt = (h >> 2) & 0xF, h & 3
            if lt == 3:
                raise VerifyError("OpenPGP: indeterminate length not supported")
            n = (1, 2, 4)[lt]
            ln = int.from_bytes(b[i:i + n], "big")
            i += n
        if i + ln > len(b):
            raise VerifyError("OpenPGP: truncated packet")
        yield tag, b[i:i + ln]
        i += ln


def _mpi(b: bytes, i: int) -> tuple[bytes, int]:
    n = (int.from_bytes(b[i:i + 2], "big") + 7) // 8
    if i + 2 + n > len(b):
        raise VerifyError("OpenPGP: truncated MPI")
    return b[i + 2:i + 2 + n], i + 2 + n


def _subpackets(area: bytes):
    i = 0
    while i < len(area):
        ln = area[i]
        i += 1
        if 192 <= ln < 255:
            ln = ((ln - 192) << 8) + area[i] + 192
            i += 1
        elif ln == 255:
            ln = int.from_bytes(area[i:i + 4], "big")
            i += 4
        if ln == 0 or i + ln > len(area):
            raise VerifyError("OpenPGP: malformed subpacket")
        yield area[i] & 0x7F, bool(area[i] & 0x80), area[i + 1:i + ln]
        i += ln


def dearmor(text: str) -> list[bytes]:
    """Every PUBLIC KEY BLOCK in an armored file, as binary."""
    out = []
    for block in re.findall(r"-----BEGIN PGP PUBLIC KEY BLOCK-----(.*?)-----END PGP PUBLIC KEY BLOCK-----",
                            text, re.S):
        lines = [ln for ln in block.strip().splitlines() if ln and ":" not in ln and not ln.startswith("=")]
        out.append(base64.b64decode("".join(lines), validate=True))
    if not out:
        raise VerifyError("OpenPGP: no public key block found")
    return out


def ed25519_primary_keys(armored: str) -> dict[str, bytes]:
    """{v4 fingerprint: 32-byte Ed25519 public key} for every Ed25519 primary key in the file."""
    keys = {}
    for raw in dearmor(armored):
        for tag, body in _packets(raw):
            if tag != 6 or body[0] != 4 or body[5] != 22 or body[7:7 + body[6]] != ED25519_OID:
                continue
            q, _ = _mpi(body, 7 + body[6])
            if len(q) == 33 and q[0] == 0x40:
                fpr = hashlib.sha1(b"\x99" + len(body).to_bytes(2, "big") + body).hexdigest().upper()
                keys[fpr] = q[1:]
    return keys


def verify_detached(data: bytes, sig: bytes, armored_keys: str, pinned: set[str]) -> set[str]:
    """Fingerprints (of ``pinned``) whose signature over ``data`` is valid. Raises if no pinned
    key made a valid signature, or if any signature by a pinned key is invalid."""
    keys = {f: k for f, k in ed25519_primary_keys(armored_keys).items() if f in pinned}
    good, seen = set(), 0
    for tag, b in _packets(sig):
        if tag != 2:
            raise VerifyError(f"OpenPGP: unexpected packet type {tag} in a signature file")
        if b[0] != 4 or b[1] != 0x00 or b[2] != 22 or b[3] not in HASHES:
            raise VerifyError(f"OpenPGP: unsupported signature (v{b[0]}, type {b[1]:#x}, "
                              f"algorithm {b[2]}, hash {b[3]})")
        n = int.from_bytes(b[4:6], "big")
        hashed = b[:6 + n]
        u = int.from_bytes(b[6 + n:8 + n], "big")
        j = 8 + n + u
        left16 = b[j:j + 2]
        r, j = _mpi(b, j + 2)
        s, j = _mpi(b, j)
        issuer = None
        for area, is_hashed in ((b[6:6 + n], True), (b[8 + n:8 + n + u], False)):
            for t, critical, d in _subpackets(area):
                if is_hashed and critical and t not in _KNOWN_CRITICAL and t != _SUB_ISSUER_FPR:
                    raise VerifyError(f"OpenPGP: unknown critical subpacket {t}")
                if t == _SUB_ISSUER_FPR and d[:1] == b"\x04":
                    issuer = d[1:].hex().upper()
        if issuer not in keys:
            continue  # a co-signature by a key we don't pin carries no weight either way
        seen += 1
        digest = HASHES[b[3]](data + hashed + b"\x04\xff" + len(hashed).to_bytes(4, "big")).digest()
        if left16 != digest[:2] or len(r) > 32 or len(s) > 32 or \
                not ed25519_verify(keys[issuer], digest, r.rjust(32, b"\0") + s.rjust(32, b"\0")):
            raise VerifyError(f"OpenPGP: signature by pinned key {issuer} is NOT valid")
        good.add(issuer)
    if not good:
        raise VerifyError(f"OpenPGP: no valid signature by a pinned key ({seen} pinned signature(s) seen)")
    return good
