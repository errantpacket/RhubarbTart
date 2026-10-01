"""Host-side logs for the TUI's Logs tab (#120, #128).

Each clone's run log (written by ``run``), build output (written by the TUI's build action via
``new_build_log``) and the append-only lifecycle trail (``events.log``). File access stays in the
audited core: only regular files directly inside the state dir are read, opened without following
symlinks. ``rhubarb.api`` re-exports the public functions; callers use ``api.list_logs()`` etc.
"""

import os
from pathlib import Path

from . import clones as _clones
from .common import VerifyError
from .results import LogRef, LogTail


BUILD_LOG_PREFIX = "build-"
MAX_LOG_BYTES = 512 * 1024   # read at most the last 512 KiB of a log


def _mtime_iso(st: os.stat_result) -> str:
    import datetime as _dt
    return _dt.datetime.fromtimestamp(st.st_mtime, _dt.UTC).isoformat(timespec="seconds")


def list_logs() -> list[LogRef]:
    """The readable logs, newest first. Read-only; creates nothing.

    Each clone's run log is labelled with the clone name (``(removed)`` when that clone no
    longer exists), builds as ``build <profile>``, and the lifecycle trail as ``events``.
    """
    import stat as _stat

    base = _clones.state_dir()
    try:
        live = {r["name"] for r in _clones.all_records()[0]}
    except Exception:
        live = set()
    out: list[LogRef] = []
    logs = base / "logs"
    if logs.is_dir() and not logs.is_symlink():
        for p in logs.iterdir():
            st = os.lstat(p)
            if not _stat.S_ISREG(st.st_mode) or p.suffix != ".log":
                continue
            if p.name.startswith(BUILD_LOG_PREFIX):
                kind, label = "build", "build " + p.stem[len(BUILD_LOG_PREFIX):]
            else:
                kind = "clone"
                label = p.stem if p.stem in live else f"{p.stem} (removed)"
            out.append(LogRef(id=f"logs/{p.name}", label=label, kind=kind,
                              size=st.st_size, mtime=_mtime_iso(st)))
    events = base / "events.log"
    if events.exists() and _stat.S_ISREG(os.lstat(events).st_mode):
        st = os.lstat(events)
        out.append(LogRef(id="events.log", label="events", kind="events",
                          size=st.st_size, mtime=_mtime_iso(st)))
    return sorted(out, key=lambda r: r.mtime, reverse=True)


def _open_log(log_id: str):
    """Open a log from ``list_logs()`` for reading: ``(binary file, stat)``.

    ``log_id`` is resolved inside the state dir and must name a regular file (opened without
    following symlinks). Raises ``VerifyError`` for an unknown or unsafe id and
    ``FileNotFoundError`` if the log has gone.
    """
    import stat as _stat
    base = _clones.state_dir()
    name = log_id[len("logs/"):] if log_id.startswith("logs/") else None
    if log_id == "events.log":
        path = base / "events.log"
    elif name and "/" not in name and name not in (".", "..") and name.endswith(".log"):
        path = base / "logs" / name
    else:
        raise VerifyError(f"invalid log id {log_id!r}")
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)   # FileNotFoundError / ELOOP propagate
    f = os.fdopen(fd, "rb")
    st = os.fstat(f.fileno())
    if not _stat.S_ISREG(st.st_mode):
        f.close()
        raise VerifyError(f"{log_id}: not a regular file")
    return f, st


def read_log(log_id: str, max_lines: int = 2000) -> str:
    """The last ``max_lines`` lines of a log from ``list_logs()``. Read-only.

    Raises ``VerifyError`` for an unknown or unsafe id and ``FileNotFoundError`` if the log
    has gone.
    """
    f, st = _open_log(log_id)
    with f:
        f.seek(max(0, st.st_size - MAX_LOG_BYTES))
        data = f.read()
    lines = data.decode(errors="replace").splitlines()
    if st.st_size > MAX_LOG_BYTES and lines:
        lines = lines[1:]   # the first line is probably cut mid-way
    return "\n".join(lines[-max_lines:])


def tail_log(log_id: str, cursor: tuple | None = None, max_lines: int = 2000) -> LogTail:
    """Follow a log from ``list_logs()``: what was written since ``cursor``. Read-only (#122).

    Without a cursor, or when the log was truncated or replaced since (or grew by more than
    the read window), returns the last ``max_lines`` lines with ``reset=True``. Otherwise
    returns only the new bytes, so following a growing build log costs what it wrote.
    Raises like ``read_log()``.

    A log counts as the same one only if it has the same device and inode AND the bytes just
    before the cursor are unchanged (#137). The inode alone is not enough: Linux reuses a
    deleted file's inode at once, and a log truncated and rewritten longer between two reads
    keeps its inode on every system.
    """
    f, st = _open_log(log_id)
    ident = (st.st_dev, st.st_ino)
    with f:
        start = None
        if cursor is not None and len(cursor) == 4 and tuple(cursor[:2]) == ident:
            offset = cursor[2]
            if (0 <= offset <= st.st_size and st.st_size - offset <= MAX_LOG_BYTES
                    and _fingerprint(f, offset) == cursor[3]):
                start = offset
        reset = start is None
        if reset:
            start = max(0, st.st_size - MAX_LOG_BYTES)
        f.seek(start)
        data = f.read(st.st_size - start)
        if reset and start > 0:
            cut = data.find(b"\n")          # the first line is probably cut mid-way
            start, data = (start + cut + 1, data[cut + 1:]) if cut >= 0 else (st.st_size, b"")
        done = data.rfind(b"\n") + 1        # bytes up to the end of the last complete line
        end = start + done
        fp = _fingerprint(f, end)
    lines = data.decode(errors="replace").splitlines()
    if reset:
        lines = lines[-max_lines:]
    return LogTail(text="\n".join(lines), cursor=(*ident, end, fp), reset=reset,
                   partial=done < len(data))


_FINGERPRINT_BYTES = 256


def _fingerprint(f, end: int) -> str:
    """A short hash of the bytes just before ``end``: if they change, the log is not the one
    the cursor was taken on."""
    import hashlib
    f.seek(max(0, end - _FINGERPRINT_BYTES))
    return hashlib.sha256(f.read(min(end, _FINGERPRINT_BYTES))).hexdigest()[:16]


def new_build_log(profile: str) -> Path:
    """A fresh, private build-log path for ``profile``: logs/build-<profile>-<UTC>.log (0600)."""
    import datetime as _dt
    if "/" in profile or profile in ("", ".", ".."):
        raise VerifyError(f"invalid profile {profile!r}")
    logs = _clones._secure_dir(_clones._secure_dir(_clones.state_dir()) / "logs")
    stamp = _dt.datetime.now(_dt.UTC).strftime("%Y%m%dT%H%M%SZ")
    path = logs / f"{BUILD_LOG_PREFIX}{profile}-{stamp}.log"
    os.close(os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600))
    return path
