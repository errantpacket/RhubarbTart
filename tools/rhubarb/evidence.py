"""Evidence store (#85, PLAN.md Phase 3): what happened in an engagement, recorded on the host.

state_dir/evidence/<engagement>/
    journal.jsonl     append-only, hash-chained entries (one JSON object per line)
    items/<sha256>    content-addressed blobs the entries refer to (command output, artifacts)

The guest is the thing under test, so it never holds the record. Commands an agent runs through
the control plane (``api.exec``) are journaled here as they happen, and files a clone produces
are *pulled* (``api.collect``) and hashed on arrival. Each entry commits to the previous one
(``prev``) and to its own content (``hash``), so a later edit, reordering or deletion shows up in
``verify()``. Signing, encryption and sealing are Phase 4 (#86); until then the chain proves
internal consistency, not origin.

Stdlib only; no tart, no ssh, no keychain. Directories 0700, files 0600, like clone records.
"""

import fcntl
import hashlib
import json
import os
from dataclasses import dataclass
from pathlib import Path

from .clones import ENGAGEMENT_RE, _secure_dir, now, state_dir
from .common import VerifyError

GENESIS = "0" * 64
KINDS = {"exec", "artifact", "ground_truth", "lifecycle", "approval"}


def _canonical(obj: dict) -> bytes:
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode()


def _entry_hash(entry: dict) -> str:
    return hashlib.sha256(_canonical({k: v for k, v in entry.items() if k != "hash"})).hexdigest()


def store_dir(engagement: str) -> Path:
    if not ENGAGEMENT_RE.match(engagement):
        raise VerifyError(f"invalid engagement id {engagement!r}")
    base = _secure_dir(_secure_dir(state_dir()) / "evidence")
    return _secure_dir(base / engagement)


def _read_journal(path: Path) -> list[dict]:
    if not path.exists():
        return []
    entries = []
    with open(path, "rb") as f:
        for n, line in enumerate(f, 1):
            try:
                entries.append(json.loads(line))
            except ValueError as e:
                raise VerifyError(f"{path}: line {n} is not JSON ({e})") from None
    return entries


def put_item(engagement: str, data: bytes) -> str:
    """Store ``data`` content-addressed; returns its sha256. Idempotent."""
    digest = hashlib.sha256(data).hexdigest()
    items = _secure_dir(store_dir(engagement) / "items")
    dest = items / digest
    if not dest.exists():
        tmp = items / f".tmp-{digest}-{os.getpid()}"
        fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
        try:
            with os.fdopen(fd, "wb") as f:
                f.write(data)
                f.flush()
                os.fsync(f.fileno())
            os.replace(tmp, dest)
        except BaseException:
            tmp.unlink(missing_ok=True)
            raise
    return digest


def item_path(engagement: str, digest: str) -> Path:
    return store_dir(engagement) / "items" / digest


def append(engagement: str, kind: str, data: dict, clone: str | None = None,
           blobs: dict[str, bytes] | None = None) -> dict:
    """Journal one entry (storing ``blobs`` as items first) and return it. Serialized by an
    exclusive lock on the journal, so concurrent writers still produce one unbroken chain."""
    if kind not in KINDS:
        raise VerifyError(f"unknown evidence kind {kind!r}")
    items = {name: put_item(engagement, blob) for name, blob in (blobs or {}).items()}
    path = store_dir(engagement) / "journal.jsonl"
    fd = os.open(path, os.O_RDWR | os.O_APPEND | os.O_CREAT | os.O_NOFOLLOW, 0o600)
    with os.fdopen(fd, "r+b") as f:
        fcntl.flock(f, fcntl.LOCK_EX)
        f.seek(0)
        last = None
        for line in f:
            if line.strip():
                last = line
        prev = json.loads(last) if last else None
        entry = {"seq": prev["seq"] + 1 if prev else 1, "ts": now(), "kind": kind,
                 "clone": clone, "data": data, "items": items,
                 "prev": prev["hash"] if prev else GENESIS}
        entry["hash"] = _entry_hash(entry)
        f.write(_canonical(entry) + b"\n")
        f.flush()
        os.fsync(f.fileno())
    return entry


def entries(engagement: str) -> list[dict]:
    return _read_journal(store_dir(engagement) / "journal.jsonl")


@dataclass(frozen=True)
class VerifyReport:
    """``verify()``'s result: entry/item counts, the chain head, and every problem found."""
    engagement: str
    entries: int
    items: int
    head: str
    problems: list[str]


def verify(engagement: str) -> VerifyReport:
    """Recompute the chain and every referenced item's hash. Never raises on bad evidence:
    each break is reported, so one damaged entry doesn't hide the rest."""
    problems: list[str] = []
    journal = entries(engagement)
    prev = GENESIS
    seen: set[str] = set()
    for i, e in enumerate(journal, 1):
        at = f"entry {i}"
        if e.get("seq") != i:
            problems.append(f"{at}: seq is {e.get('seq')!r}, expected {i} (entry removed or reordered)")
        if e.get("prev") != prev:
            problems.append(f"{at}: prev does not match the previous entry's hash (chain broken)")
        if e.get("hash") != _entry_hash(e):
            problems.append(f"{at}: content does not match its hash (entry altered)")
        prev = e.get("hash", "")
        for name, digest in (e.get("items") or {}).items():
            seen.add(digest)
            p = item_path(engagement, digest)
            if not p.is_file():
                problems.append(f"{at}: item {name} ({digest[:12]}) missing")
                continue
            h = hashlib.sha256()
            with open(p, "rb") as f:
                for chunk in iter(lambda: f.read(1 << 20), b""):
                    h.update(chunk)
            if h.hexdigest() != digest:
                problems.append(f"{at}: item {name} ({digest[:12]}) content altered")
    return VerifyReport(engagement=engagement, entries=len(journal), items=len(seen),
                        head=prev if journal else GENESIS, problems=problems)
