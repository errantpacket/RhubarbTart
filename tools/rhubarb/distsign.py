"""Tailscale "distsign" verification (clientupdate/distsign in tailscale/tailscale).

Chain: pinned root public key (config/keys/tailscale-distsign-root-*.pem, copied from
the Tailscale source tree) -> signs https://pkgs.tailscale.com/distsign.pub (bundle of
signing keys) -> a signing key signs each package: Ed25519 over
BLAKE2s-256(file) || uint64_le(len(file)).

Ed25519 *verification* below follows RFC 8032 §5.1.7 (reference algorithm). It only
handles public data, so constant-time concerns don't apply; it is tested against real
Tailscale signatures and tampered copies (tools/check.sh, test_distsign).
"""

import base64
import hashlib
import re
from pathlib import Path

from .common import KEYS, VerifyError, get_bytes

# ---- Ed25519 verify (RFC 8032) -----------------------------------------------------
_P = 2**255 - 19
_L = 2**252 + 27742317777372353535851937790883648493
_D = -121665 * pow(121666, _P - 2, _P) % _P
_I = pow(2, (_P - 1) // 4, _P)


def _xrecover(y: int, sign: int) -> int:
    x2 = (y * y - 1) * pow(_D * y * y + 1, _P - 2, _P) % _P
    if x2 == 0:
        if sign:
            raise ValueError("invalid point")
        return 0
    x = pow(x2, (_P + 3) // 8, _P)
    if (x * x - x2) % _P != 0:
        x = x * _I % _P
    if (x * x - x2) % _P != 0:
        raise ValueError("invalid point")
    if x & 1 != sign:
        x = _P - x
    return x


_BY = 4 * pow(5, _P - 2, _P) % _P
_B = (_xrecover(_BY, 0), _BY, 1, _xrecover(_BY, 0) * _BY % _P)


def _add(p, q):
    x1, y1, z1, t1 = p
    x2, y2, z2, t2 = q
    a = (y1 - x1) * (y2 - x2) % _P
    b = (y1 + x1) * (y2 + x2) % _P
    c = 2 * t1 * t2 * _D % _P
    d = 2 * z1 * z2 % _P
    e, f, g, h = b - a, d - c, d + c, b + a
    return (e * f % _P, g * h % _P, f * g % _P, e * h % _P)


def _mul(s: int, p):
    q = (0, 1, 1, 0)
    while s:
        if s & 1:
            q = _add(q, p)
        p = _add(p, p)
        s >>= 1
    return q


def _decode_point(b: bytes):
    if len(b) != 32:
        raise ValueError("bad point length")
    y = int.from_bytes(b, "little")
    sign = y >> 255
    y &= (1 << 255) - 1
    if y >= _P:
        raise ValueError("non-canonical point")
    x = _xrecover(y, sign)
    return (x, y, 1, x * y % _P)


def _eq(p, q) -> bool:
    return ((p[0] * q[2] - q[0] * p[2]) % _P == 0) and ((p[1] * q[2] - q[1] * p[2]) % _P == 0)


def ed25519_verify(pub: bytes, msg: bytes, sig: bytes) -> bool:
    try:
        if len(sig) != 64:
            return False
        a = _decode_point(pub)
        r = _decode_point(sig[:32])
        s = int.from_bytes(sig[32:], "little")
        if s >= _L:
            return False
        k = int.from_bytes(hashlib.sha512(sig[:32] + pub + msg).digest(), "little") % _L
        # cofactored check: [8][S]B == [8]R + [8][k]A
        return _eq(_mul(8 * s, _B), _add(_mul(8, r), _mul(8 * k, a)))
    except ValueError:
        return False


# ---- distsign ------------------------------------------------------------------------
_PEM = re.compile(r"-----BEGIN ([A-Z ]+)-----\s*([A-Za-z0-9+/=\s]+?)\s*-----END \1-----")


def _pem_keys(text: str, tag: str) -> list[bytes]:
    keys = [base64.b64decode(body) for kind, body in _PEM.findall(text) if kind == tag]
    if not keys or any(len(k) != 32 for k in keys):
        raise VerifyError(f"no valid '{tag}' Ed25519 keys found")
    return keys


def _file_msg(data: bytes) -> bytes:
    return hashlib.blake2s(data, digest_size=32).digest() + len(data).to_bytes(8, "little")


def signing_keys(root_files: list[str], base: str = "https://pkgs.tailscale.com") -> list[bytes]:
    roots = [k for f in root_files for k in _pem_keys((KEYS / f).read_text(), "ROOT PUBLIC KEY")]
    bundle = get_bytes(f"{base}/distsign.pub")
    sig = get_bytes(f"{base}/distsign.pub.sig")
    if not any(ed25519_verify(r, bundle, sig) for r in roots):
        raise VerifyError("distsign.pub is not signed by any pinned Tailscale root key")
    return _pem_keys(bundle.decode(), "SIGNING PUBLIC KEY")


def verify_file(path: Path, sig: bytes, keys: list[bytes]) -> None:
    if not any(ed25519_verify(k, _file_msg(path.read_bytes()), sig) for k in keys):
        raise VerifyError(f"{path.name}: Tailscale distsign signature invalid")
