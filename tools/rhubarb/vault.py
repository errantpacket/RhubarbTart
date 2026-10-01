"""Evidence vault (#86, PLAN.md Phase 4): a signed, sealed, portable copy of an engagement's
evidence, made on the host.

``seal`` takes the append-only evidence store (evidence.py) and writes a self-contained bundle:

    <engagement>-<sealed-at>.vault/
        root.json          the manifest: engagement scope, chain head, journal + item hashes,
                           and each range's source image + provenance
        root.bundle.json   the cosign signature over root.json (offline, key never in the guest)
        cosign.pub         the public half, so the bundle verifies with nothing else on hand
        journal.jsonl      the sealed evidence chain, copied verbatim
        items/<sha256>     every referenced item
        provenance/<vm>.provenance.json   which verified images produced this evidence

root.json commits to the journal's sha256, every item's sha256 and the chain head, so one
signature over it anchors the whole bundle: a consumer verifies the signature, then re-derives
every hash. After writing, the bundle is made read-only — the seal. Signing/verifying is the
caller's job (a cosign shell-out on macOS; a fake in tests), injected as a callable, so this
module stays pure stdlib and testable off a Mac. Encryption at rest is a later slice; until then
confidentiality rests on the host disk (FileVault).
"""

import hashlib
import json
import os
import shutil
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from . import engagements as _engagements
from . import evidence as _evidence
from .common import ROOT, VerifyError

# root.json -> writes a detached signature bundle beside it. Raises on failure.
SignFn = Callable[[Path, Path], None]
# (root.json, bundle, pub) -> raises VerifyError if the signature does not verify.
VerifyFn = Callable[[Path, Path, Path | None], None]

PROVENANCE_DIR = ROOT / "out"
PUB_KEY = ROOT / "config" / "keys" / "rhubarb-cosign.pub"
ROOT_NAME = "root.json"
BUNDLE_NAME = "root.bundle.json"
PUB_NAME = "cosign.pub"


def _sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def _ranges(engagement: str, journal: list[dict]) -> list[dict]:
    """Each clone's source image, from the provision lifecycle entry, with the sha256 of its
    build provenance record (out/<image>.provenance.json) when present on this host."""
    created: dict[str, str] = {}
    for e in journal:
        if e.get("kind") == "lifecycle" and e.get("data", {}).get("event") == "provision":
            created.update(e["data"].get("created", {}))
    out = []
    for clone, image in sorted(created.items()):
        prov = PROVENANCE_DIR / f"{image}.provenance.json"
        out.append({"clone": clone, "image": image,
                    "provenance_sha256": _sha256_file(prov) if prov.is_file() else None})
    return out


def build_root(engagement: str, sealed_at: str, cosign_version: str | None = None) -> dict:
    """The vault's root manifest. Commits to the whole evidence set by hash. Verifying the
    engagement's chain first is the caller's job (``seal`` does it)."""
    eng = _engagements.load_engagement(engagement)
    store = _evidence.store_dir(engagement)
    journal = _evidence.entries(engagement)
    rep = _evidence.verify(engagement)
    items = {}
    for e in journal:
        for digest in (e.get("items") or {}).values():
            p = _evidence.item_path(engagement, digest)
            items[digest] = p.stat().st_size if p.is_file() else None
    return {
        "schema": 1,
        "engagement": eng.id,
        "manifest": {"label": eng.label, "operator": eng.operator,
                     "authorization": eng.authorization},
        "sealed_at": sealed_at,
        "sealed_by": eng.operator,
        "chain_head": rep.head,
        "entries": len(journal),
        "journal_sha256": _sha256_file(store / "journal.jsonl") if journal else None,
        "items": items,
        "ranges": _ranges(engagement, journal),
        "tool": {"cosign": cosign_version} if cosign_version else {},
    }


def _make_readonly(path: Path) -> None:
    """Seal: files 0400, directories 0500, deepest first. The signature is the real guarantee;
    this makes accidental edits obvious and marks the tree as sealed."""
    for p in sorted(path.rglob("*"), key=lambda q: len(q.parts), reverse=True):
        os.chmod(p, 0o500 if p.is_dir() else 0o400)
    os.chmod(path, 0o500)


def seal(engagement: str, out_dir: Path, sign: SignFn, sealed_at: str,
         cosign_version: str | None = None) -> Path:
    """Write and sign a sealed vault for ``engagement`` under ``out_dir``; return its path.

    Refuses (``VerifyError``) if the evidence chain doesn't verify, if there is no evidence, or
    if the destination already exists. The tree is read-only on return.
    """
    rep = _evidence.verify(engagement)
    if rep.entries == 0:
        raise VerifyError(f"engagement {engagement}: no evidence to seal")
    if rep.problems:
        raise VerifyError(f"engagement {engagement}: evidence does not verify, refusing to seal "
                          f"({len(rep.problems)} problem(s)); run: rhubarbtart evidence verify {engagement}")
    store = _evidence.store_dir(engagement)
    vault = out_dir / f"{engagement}-{sealed_at.replace(':', '').replace('-', '')}.vault"
    if vault.exists():
        raise VerifyError(f"{vault} already exists")

    tmp = out_dir / f".{vault.name}.partial"
    if tmp.exists():
        shutil.rmtree(tmp)
    (tmp / "items").mkdir(parents=True)
    (tmp / "provenance").mkdir()
    try:
        root = build_root(engagement, sealed_at, cosign_version)
        (tmp / ROOT_NAME).write_text(json.dumps(root, indent=2, sort_keys=True) + "\n")
        shutil.copy2(store / "journal.jsonl", tmp / "journal.jsonl")
        for digest in root["items"]:
            shutil.copy2(_evidence.item_path(engagement, digest), tmp / "items" / digest)
        for rng in root["ranges"]:
            if rng["provenance_sha256"]:
                shutil.copy2(PROVENANCE_DIR / f"{rng['image']}.provenance.json",
                             tmp / "provenance" / f"{rng['image']}.provenance.json")
        if PUB_KEY.is_file():
            shutil.copy2(PUB_KEY, tmp / PUB_NAME)
        sign(tmp / ROOT_NAME, tmp / BUNDLE_NAME)   # raises on failure
        if not (tmp / BUNDLE_NAME).is_file():
            raise VerifyError("signing produced no bundle")
        os.replace(tmp, vault)
    finally:
        if tmp.exists():
            shutil.rmtree(tmp, ignore_errors=True)
    _make_readonly(vault)
    return vault


@dataclass(frozen=True)
class VaultReport:
    """``verify()``'s result: what the bundle claims, whether the signature held, and every
    problem found (empty ``problems`` and ``signed`` true means the vault is trustworthy)."""
    vault: Path
    engagement: str
    signed: bool
    entries: int
    items: int
    chain_head: str
    problems: list[str]


def verify(vault_dir: Path, verify_sig: VerifyFn, pub: Path | None = None) -> "VaultReport":
    """Verify a standalone sealed vault: signature over root.json, then every hash root.json
    commits to (journal, each item, chain head). Offline; needs no engagement or state dir.
    Never raises on bad evidence — each problem is reported."""
    problems: list[str] = []
    root_path = vault_dir / ROOT_NAME
    bundle = vault_dir / BUNDLE_NAME
    if not root_path.is_file():
        raise VerifyError(f"{vault_dir}: no {ROOT_NAME} (not a vault?)")
    if not bundle.is_file():
        raise VerifyError(f"{vault_dir}: no {BUNDLE_NAME} (unsigned?)")
    root = json.loads(root_path.read_text())

    pub_arg = pub if pub is not None else (vault_dir / PUB_NAME if (vault_dir / PUB_NAME).is_file()
                                           else None)
    try:
        verify_sig(root_path, bundle, pub_arg)
        signed = True
    except VerifyError as e:
        signed = False
        problems.append(f"signature: {e}")

    journal = vault_dir / "journal.jsonl"
    if root.get("journal_sha256"):
        if not journal.is_file():
            problems.append("journal.jsonl missing")
        elif _sha256_file(journal) != root["journal_sha256"]:
            problems.append("journal.jsonl does not match root.json (altered)")
        else:
            entries = [json.loads(line) for line in journal.read_text().splitlines() if line.strip()]
            head = entries[-1].get("hash") if entries else _evidence.GENESIS
            if head != root.get("chain_head"):
                problems.append("chain head does not match root.json")
    for digest, size in (root.get("items") or {}).items():
        p = vault_dir / "items" / digest
        if not p.is_file():
            problems.append(f"item {digest[:12]} missing")
        elif _sha256_file(p) != digest:
            problems.append(f"item {digest[:12]} content does not match its name (altered)")
        elif size is not None and p.stat().st_size != size:
            problems.append(f"item {digest[:12]} size differs from root.json")
    return VaultReport(vault=vault_dir, engagement=root.get("engagement", "?"), signed=signed,
                       entries=root.get("entries", 0), items=len(root.get("items") or {}),
                       chain_head=root.get("chain_head", ""), problems=problems)
