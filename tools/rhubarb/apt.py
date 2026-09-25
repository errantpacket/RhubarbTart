"""Resolve a .deb from a signed apt repository without apt.

Chain of trust: pinned key + fingerprint -> InRelease signature -> SHA256 of the
Packages index listed *inside the signed payload* -> SHA256 of the .deb listed in
that index. Only data taken from the verified payload is used.
"""

import gzip
import hashlib
import re
import urllib.parse

from .common import VerifyError, get_bytes, log
from .gpg import verify_clearsigned


def _stanzas(text: str):
    for block in re.split(r"\n\s*\n", text.strip()):
        fields, key = {}, None
        for line in block.splitlines():
            if line[:1] in (" ", "\t") and key:
                fields[key] += "\n" + line.strip()
            elif ":" in line:
                key, _, value = line.partition(":")
                fields[key] = value.strip()
        if fields:
            yield fields


# ---- dpkg version comparison (deb-version(7)) --------------------------------------

def _order(c: str) -> int:
    if c == "~":
        return -1
    if c.isdigit():
        return 0
    if c.isalpha():
        return ord(c)
    return ord(c) + 256


def _cmp_part(a: str, b: str) -> int:
    while a or b:
        na = re.match(r"[^0-9]*", a).group(0)
        nb = re.match(r"[^0-9]*", b).group(0)
        for i in range(max(len(na), len(nb))):
            ca = _order(na[i]) if i < len(na) else 0
            cb = _order(nb[i]) if i < len(nb) else 0
            if ca != cb:
                return -1 if ca < cb else 1
        a, b = a[len(na):], b[len(nb):]
        da = re.match(r"[0-9]*", a).group(0)
        db = re.match(r"[0-9]*", b).group(0)
        ia, ib = int(da or 0), int(db or 0)
        if ia != ib:
            return -1 if ia < ib else 1
        a, b = a[len(da):], b[len(db):]
    return 0


def _split(v: str) -> tuple[int, str, str]:
    epoch, _, rest = v.rpartition(":") if ":" in v else ("0", "", v)
    upstream, _, rev = rest.rpartition("-") if "-" in rest else (rest, "", "0")
    return int(epoch or 0), upstream, rev


def dpkg_cmp(a: str, b: str) -> int:
    ea, ua, ra = _split(a)
    eb, ub, rb = _split(b)
    if ea != eb:
        return -1 if ea < eb else 1
    return _cmp_part(ua, ub) or _cmp_part(ra, rb)


# ---- resolver ------------------------------------------------------------------------

def resolve_deb(v: dict) -> dict:
    """v: {repo, suite, component, package, arch, key, key_fpr} -> lock entry (no download)."""
    base = v["repo"].rstrip("/")
    signed = verify_clearsigned(get_bytes(f"{base}/dists/{v['suite']}/InRelease"),
                                v["key"], v["key_fpr"]).decode()
    release = next(_stanzas(signed))
    listed = {}
    for line in release.get("SHA256", "").splitlines():
        parts = line.split()
        if len(parts) == 3:
            listed[parts[2]] = parts[0]
    index = None
    for name in (f"{v['component']}/binary-{v['arch']}/Packages.gz",
                 f"{v['component']}/binary-{v['arch']}/Packages"):
        if name in listed:
            raw = get_bytes(f"{base}/dists/{v['suite']}/{name}")
            if hashlib.sha256(raw).hexdigest() != listed[name]:
                raise VerifyError(f"{name}: SHA256 does not match signed InRelease")
            index = gzip.decompress(raw).decode() if name.endswith(".gz") else raw.decode()
            break
    if index is None:
        raise VerifyError(f"{base} {v['suite']}: no {v['component']}/binary-{v['arch']} index")

    cands = [s for s in _stanzas(index) if s.get("Package") == v["package"]]
    if not cands:
        raise VerifyError(f"{v['package']} not found in {base} {v['suite']} ({v['arch']})")
    best = cands[0]
    for s in cands[1:]:
        if dpkg_cmp(s["Version"], best["Version"]) > 0:
            best = s
    url = f"{base}/{best['Filename']}"
    log(f"apt {v['package']} {best['Version']} ({v['arch']}) via signed {base}")
    return {
        "version": best["Version"],
        "url": url,
        "file": urllib.parse.unquote(best["Filename"].rsplit("/", 1)[-1]),
        "sha256": best["SHA256"].lower(),
        "size": int(best.get("Size", 0)),
        "depends": best.get("Depends", ""),
        "hash_sources": [f"apt:{base} {v['suite']} InRelease signed by {v['key_fpr']}"],
    }
