"""Nix NAR hash of a directory tree, in pure Python (no Nix needed on the host).

Nix's `fetchTarball { sha256 = ...; }` checks the NAR hash of the *unpacked* tree.
Computing it here lets resolve.py pin nixpkgs by (git rev, NAR hash); the NixOS
guest's own Nix then re-verifies the same hash when it unpacks the tarball.
Format: https://nixos.org/manual/nix/stable/protocols/nix-archive.html
"""

import base64
import hashlib
import os
import stat
import tarfile
import tempfile
from pathlib import Path


def _str(h, b: bytes) -> None:
    h.update(len(b).to_bytes(8, "little"))
    h.update(b)
    h.update(b"\0" * (-len(b) % 8))


def _strs(h, *items: bytes) -> None:
    for b in items:
        _str(h, b)


def _serialise(h, path: Path) -> None:
    st = os.lstat(path)
    _str(h, b"(")
    if stat.S_ISLNK(st.st_mode):
        _strs(h, b"type", b"symlink", b"target", os.readlink(path).encode())
    elif stat.S_ISREG(st.st_mode):
        _strs(h, b"type", b"regular")
        if st.st_mode & stat.S_IXUSR:
            _strs(h, b"executable", b"")
        _str(h, b"contents")
        size = st.st_size
        h.update(size.to_bytes(8, "little"))
        with open(path, "rb") as f:
            while chunk := f.read(1 << 20):
                h.update(chunk)
        h.update(b"\0" * (-size % 8))
    elif stat.S_ISDIR(st.st_mode):
        _strs(h, b"type", b"directory")
        for name in sorted(os.listdir(path), key=os.fsencode):
            _strs(h, b"entry", b"(", b"name", os.fsencode(name), b"node")
            _serialise(h, path / name)
            _str(h, b")")
    else:
        raise ValueError(f"unsupported file type in NAR: {path}")
    _str(h, b")")


def nar_sha256(path: Path) -> str:
    """SRI form, e.g. 'sha256-…=' (what Nix prints and accepts)."""
    h = hashlib.sha256()
    _str(h, b"nix-archive-1")
    _serialise(h, path)
    return "sha256-" + base64.b64encode(h.digest()).decode()


def tarball_nar_sha256(tarball: Path) -> str:
    """NAR hash of a GitHub-style tarball's single top-level directory, as fetchTarball sees it."""
    with tempfile.TemporaryDirectory() as tmp:
        with tarfile.open(tarball) as tf:
            tf.extractall(tmp, filter="tar")  # keeps modes/symlinks, rejects unsafe paths
        entries = os.listdir(tmp)
        if len(entries) != 1:
            raise ValueError(f"expected one top-level directory in {tarball.name}")
        return nar_sha256(Path(tmp) / entries[0])
