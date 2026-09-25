"""Lock files: loading, the artifacts they pin, and the input identity that names images."""

import hashlib
import json

from .common import ROOT, VerifyError
from .profiles import lock_path


def artifacts(lock: dict):
    """(label, entry) for every downloadable input recorded in a plan/lock."""
    base = lock["base"]
    yield "base:image", base["image"]
    if "nixpkgs" in base:
        yield "base:nixpkgs", base["nixpkgs"]
    for pid, e in lock["packages"].items():
        if e.get("file"):
            yield pid, e


def inputs_sha256(lock: dict) -> str:
    """Identity of a build's inputs: profile + artifact hashes + signers. No timestamps."""
    lines = [f"profile {lock['profile']} {lock['profile_sha256']}"]
    for label, e in artifacts(lock):
        lines.append(f"{label} {e['sha256']} {e.get('nar_sha256', '')}")
    for pid, e in sorted(lock["packages"].items()):
        extra = e.get("package") or json.dumps(e.get("nix", {}), sort_keys=True)
        lines.append(f"pkg {pid} {e.get('sha256', '')} {e.get('signature', {}).get('team_id', '')} {extra}")
    return hashlib.sha256("\n".join(sorted(lines)).encode()).hexdigest()


def load_lock(prof: dict) -> dict:
    path = lock_path(prof["id"])
    if not path.exists():
        raise VerifyError(f"{path.relative_to(ROOT)} missing; run `resolve {prof['id']}` first")
    lock = json.loads(path.read_text())
    if lock.get("schema") != 2:
        raise VerifyError(f"{path.name}: unsupported lock schema; re-resolve")
    if lock["profile_sha256"] != prof["profile_sha256"]:
        raise VerifyError(f"profiles/{prof['id']}.json changed since {path.name} was written; re-resolve")
    return lock


def image_name(prof: dict) -> str:
    """Final name of the verified image this profile's committed lock produces."""
    return f"rbt-{prof['id']}-{inputs_sha256(load_lock(prof))[:12]}"
