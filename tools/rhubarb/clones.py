"""Per-clone records for the `rhubarb` CLI.

Where: ~/Library/Application Support/RhubarbTart (macOS), $XDG_STATE_HOME/rhubarbtart
elsewhere, or $RHUBARB_STATE_DIR. Deliberately outside the repo (never committed or synced
with it) and outside Tart's VM directories (never shipped inside a VM bundle or push).

Security model:
  * Records hold NO secrets: names, lineage, username, timestamps. Passwords stay in the
    macOS keychain (service "RhubarbTart"); enrollment secrets in "RhubarbTart-enroll".
  * Like OpenSSH's StrictModes, records are only trusted if the state dir is 0700 and each
    file is 0600, owned by the current user, a regular file (not a symlink).
  * Strict schema: unknown keys, bad names or a name/filename mismatch are rejected, so a
    corrupted or planted record can't steer the CLI at the wrong VM or keychain entry.
  * Atomic writes (temp file + rename), so a crash never leaves a half-written record.
  * Not a boundary against malware running as you: such code can already drive `tart`
    and prompt for your keychain. The goal is correctness, least surprise, and no leaks.
"""

import datetime as dt
import json
import os
import re
import stat
import sys
import tempfile
from pathlib import Path

from .common import VerifyError

CLONE_RE = re.compile(r"^[a-z0-9][a-z0-9-]{0,39}$")     # clones never start with "rbt-"
IMAGE_RE = re.compile(r"^rbt-[a-z0-9][a-z0-9.-]{1,80}-[0-9a-f]{12}$")
USER_RE = re.compile(r"^[a-z][a-z0-9]{2,15}$")
PROFILE_RE = re.compile(r"^[a-z0-9][a-z0-9-]{1,40}$")
ENGAGEMENT_RE = re.compile(r"^[a-z0-9][a-z0-9-]{1,40}$")   # mirrors profiles.ID_RE / engagements ids (no import: stay standalone)
FAMILIES = {"macos", "nixos", "kali"}
SERVICES = {"tailscale", "warp", "perimeter81"}
# "engagement" was added after schema 1 shipped: it is OPTIONAL, so records written before it
# (with no "engagement" key) still load and are treated as engagement=None (see _validate).
OPTIONAL_KEYS = {"engagement"}
RECORD_KEYS = {"schema", "name", "profile", "family", "image", "username", "rosetta",
               "password_account", "created_at", "enrollments", "engagement"}


def state_dir() -> Path:
    if os.environ.get("RHUBARB_STATE_DIR"):
        base = Path(os.environ["RHUBARB_STATE_DIR"])
    elif sys.platform == "darwin":
        base = Path.home() / "Library" / "Application Support" / "RhubarbTart"
    else:
        base = Path(os.environ.get("XDG_STATE_HOME", Path.home() / ".local" / "state")) / "rhubarbtart"
    return base


def _secure_dir(path: Path) -> Path:
    path.mkdir(mode=0o700, parents=True, exist_ok=True)
    st = os.lstat(path)
    if stat.S_ISLNK(st.st_mode) or not stat.S_ISDIR(st.st_mode):
        raise VerifyError(f"{path} must be a real directory, not a symlink")
    if st.st_uid != os.getuid():
        raise VerifyError(f"{path} is not owned by you; refusing to use it")
    if st.st_mode & 0o077:
        os.chmod(path, 0o700)  # we created/own it: tighten rather than fail
    return path


def clones_dir() -> Path:
    base = _secure_dir(state_dir())
    return _secure_dir(base / "clones")


def now() -> str:
    return dt.datetime.now(dt.UTC).isoformat(timespec="seconds")


def check_clone_name(name: str) -> str:
    if not CLONE_RE.match(name) or name.startswith("rbt-"):
        raise VerifyError(f"invalid clone name {name!r}: use lowercase letters, digits and dashes "
                          "(max 40), not starting with 'rbt-' (reserved for built images)")
    return name


def _validate(rec: dict, expected_name: str) -> dict:
    where = f"clone record {expected_name}"
    unknown = set(rec) - RECORD_KEYS
    missing = (RECORD_KEYS - OPTIONAL_KEYS) - set(rec)   # optional keys may be absent (back-compat)
    if unknown or missing:
        raise VerifyError(f"{where}: unexpected keys {sorted(unknown)} / missing {sorted(missing)}")
    if rec["schema"] != 1:
        raise VerifyError(f"{where}: unsupported schema {rec['schema']!r}")
    if rec["name"] != expected_name:
        raise VerifyError(f"{where}: name {rec['name']!r} does not match its file")
    check_clone_name(rec["name"])
    checks = [
        (PROFILE_RE.match(str(rec["profile"])), "profile"),
        (rec["family"] in FAMILIES, "family"),
        (IMAGE_RE.match(str(rec["image"])), "image"),
        (USER_RE.match(str(rec["username"])), "username"),
        (isinstance(rec["rosetta"], bool), "rosetta"),
        (rec["password_account"] in (rec["name"], rec["image"]), "password_account"),
        (isinstance(rec["enrollments"], dict) and set(rec["enrollments"]) <= SERVICES, "enrollments"),
        # engagement: absent (old records) or explicit null -> None; else an engagement id string.
        (rec.get("engagement") is None
         or (isinstance(rec["engagement"], str) and bool(ENGAGEMENT_RE.match(rec["engagement"]))),
         "engagement"),
    ]
    for ok, field in checks:
        if not ok:
            raise VerifyError(f"{where}: invalid {field}")
    rec.setdefault("engagement", None)   # normalize pre-engagement records so callers can read it
    return rec


def load(name: str) -> dict:
    check_clone_name(name)
    path = clones_dir() / f"{name}.json"
    try:
        st = os.lstat(path)
    except FileNotFoundError:
        raise VerifyError(f"no rhubarb clone named {name!r} (see `rhubarb list`)") from None
    if not stat.S_ISREG(st.st_mode) or st.st_uid != os.getuid() or st.st_mode & 0o077:
        raise VerifyError(f"{path}: must be a regular file owned by you with mode 0600; refusing it")
    try:
        rec = json.loads(path.read_text())
    except json.JSONDecodeError as e:
        raise VerifyError(f"{path}: corrupt record ({e})") from None
    return _validate(rec, name)


def save(rec: dict) -> None:
    _validate(rec, rec["name"])
    d = clones_dir()
    fd, tmp = tempfile.mkstemp(dir=d, prefix=".tmp-", suffix=".json")
    try:
        with os.fdopen(fd, "w") as f:
            json.dump(rec, f, indent=2, sort_keys=True)
            f.write("\n")
        os.chmod(tmp, 0o600)
        os.replace(tmp, d / f"{rec['name']}.json")
    except BaseException:
        Path(tmp).unlink(missing_ok=True)
        raise


def delete(name: str) -> None:
    check_clone_name(name)
    (clones_dir() / f"{name}.json").unlink(missing_ok=True)


def all_records() -> tuple[list[dict], list[str]]:
    """(valid records, problems). A bad record is reported, never silently used."""
    good, bad = [], []
    for path in sorted(clones_dir().glob("*.json")):
        try:
            good.append(load(path.stem))
        except VerifyError as e:
            bad.append(str(e))
    return good, bad


def new_record(name: str, prof: dict, image: str, engagement: str | None = None) -> dict:
    return {
        "schema": 1, "name": check_clone_name(name), "profile": prof["id"], "family": prof["family"],
        "image": image, "username": prof["username"], "rosetta": bool(prof["options"].get("rosetta")),
        "password_account": image,  # inherited until `new` rotates it to a per-clone password
        "created_at": now(), "enrollments": {},
        "engagement": engagement,  # the engagement this clone belongs to; None for ad-hoc clones
    }


def log_event(event: str, name: str, detail: str = "") -> None:
    """Append-only audit trail (no secrets): state_dir/events.log, 0600."""
    path = _secure_dir(state_dir()) / "events.log"
    fd = os.open(path, os.O_WRONLY | os.O_APPEND | os.O_CREAT | os.O_NOFOLLOW, 0o600)
    with os.fdopen(fd, "a") as f:
        f.write(f"{now()}\t{event}\t{name}\t{detail}\n")
