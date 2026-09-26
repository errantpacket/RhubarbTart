"""Typed core API for RhubarbTart clone management — the single import surface.

This module is the one audited entry point that a TUI or the future `herdr` service
imports. It sits over the existing modules and mirrors their semantics exactly:

  * ``clones``   — per-clone record schema, StrictModes trust rules, keychain accounts.
  * ``hostops``  — every ``tart`` / keychain / SSH / ``launchctl asuser`` GUI-session call.
  * ``locks``    — the input identity (``rbt-PROFILE-SHA``) that names a verified image.
  * ``resolve``  — the ``out/<vm>.provenance.json`` build record.

The contract, not the implementation, is the interface: functions return dataclasses
(structured values), never ``print`` output or ``argv``, and raise typed errors instead
of calling ``sys.exit``. Callers format the returns for their own frontend.

Frontends MUST NOT touch ``tart`` or the keychain directly, and MUST NOT re-implement the
StrictModes record rules, keychain-only secret handling, or ``launchctl asuser``
GUI-session logic — those stay inside the core (``clones`` / ``hostops``). This module only
re-exposes them.

Errors (shared by every function unless its docstring says otherwise):
  * ``common.VerifyError`` — invalid input, a failed/untrusted record, a missing image or
    keychain entry, or any host operation that did not succeed. Carries a human message.
  * ``FileNotFoundError`` — a required host tool (``tart``, ``security``, ``ssh``) is not
    installed; ``.filename`` names it. Mirrors what ``cli.main`` catches today.

Implementation note: this module is stdlib-only. It delegates all keychain, StrictModes
record and GUI-session (``launchctl asuser`` / headless-boot) logic to ``clones`` and
``hostops`` — it never re-implements them — and re-exposes the results as typed values.
See docs/INTERFACE-PLAN.md, Stage A.
"""

import json
import subprocess
from dataclasses import dataclass

from . import clones as _clones
from . import hostops
from .common import ROOT, VerifyError
from .locks import image_name
from .profiles import list_profiles, load_profile

# ---- returned structures -------------------------------------------------------------------


@dataclass(frozen=True)
class Image:
    """A local Tart VM that is a RhubarbTart image (or a vanilla/unverified precursor).

    Mirrors one row of ``rhubarb images``.

    name:    the Tart VM name (e.g. ``rbt-macos-research-ab12cd34ef56``, or a
             ``*-vanilla`` / ``*-unverified`` precursor name).
    profile: the profile id this image belongs to, or ``"-"`` when none matches.
    kind:    ``"image"`` (a finished ``rbt-...`` image), ``"vanilla"``, or ``"unverified"``.
    status:  for an ``image``, ``"current"`` if it is what the profile's committed lock
             now produces else ``"outdated"``; for the other kinds, equal to ``kind``.
    clones:  how many clone records were made from this image.
    """

    name: str
    profile: str
    kind: str
    status: str
    clones: int


@dataclass(frozen=True)
class Clone:
    """A research clone created by ``rhubarb new``. Mirrors one row of ``rhubarb list``,
    plus the underlying record fields a frontend needs. Holds no secrets.

    name:             the clone's Tart VM name and record filename stem.
    profile:          the profile id it was cloned from.
    family:           OS family — ``"macos"`` | ``"nixos"`` | ``"kali"``.
    image:            the ``rbt-...`` image it was cloned from (its lineage).
    state:            the live Tart state (e.g. ``"running"``/``"stopped"``), or
                      ``"MISSING"`` when no such VM exists on this host.
    freshness:        ``"current"`` (image is what the profile's lock now produces),
                      ``"outdated"`` (a newer image exists), or ``"image-deleted"``
                      (the lineage image is no longer present locally).
    password_mode:    ``"unique"`` (its own keychain password, account == ``name``) or
                      ``"inherited"`` (still shares the image's password).
    password_account: the keychain account holding its password (``name`` or ``image``).
    enrollments:      sorted service names it is enrolled in (subset of tailscale/warp/
                      perimeter81); empty when none.
    created_at:       ISO-8601 UTC timestamp the record was created.
    """

    name: str
    profile: str
    family: str
    image: str
    state: str
    freshness: str
    password_mode: str
    password_account: str
    enrollments: list[str]
    created_at: str


@dataclass(frozen=True)
class CloneList:
    """The result of ``clones()``: the trusted clone views plus any rejected records.

    clones:   valid, StrictModes-trusted clone records, one ``Clone`` each.
    problems: human-readable reasons records were ignored (bad mode/owner, schema, or
              name mismatch). Mirrors the ``IGNORED ...`` lines ``rhubarb list`` prints;
              never silently dropped.
    """

    clones: list[Clone]
    problems: list[str]


@dataclass(frozen=True)
class Provenance:
    """A parsed ``out/<vm>.provenance.json`` build record (written by
    ``resolve.py provenance``). Fields mirror that record exactly.

    vm:            the VM name the record describes.
    profile:       the profile id it was built from.
    built_at:      ISO-8601 UTC timestamp the record was written.
    git_commit:    repo HEAD at build time, or ``None`` if not a git checkout.
    git_dirty:     whether the working tree had uncommitted changes at build time.
    toolchain:     pinned host toolchain versions (``toolchain.toolchain()`` output).
    tart_vm:       ``tart get --format json`` for the VM, or ``None`` if unavailable.
    inputs_sha256: identity of the build's inputs (the ``rbt-...`` hash source).
    lock_sha256:   sha256 of the committed lock file.
    lock:          the full parsed lock (``locks/<profile>.lock.json``).
    build_files:   ``{repo-relative path: sha256}`` for every tracked build file.
    """

    vm: str
    profile: str
    built_at: str
    git_commit: str | None
    git_dirty: bool
    toolchain: dict
    tart_vm: dict | None
    inputs_sha256: str
    lock_sha256: str
    lock: dict
    build_files: dict[str, str]


@dataclass(frozen=True)
class NewResult:
    """The result of ``new()`` (and of ``reset()``, which re-clones): the fresh clone and
    what happened to its password.

    name:          the clone's name.
    image:         the ``rbt-...`` image it was cloned from.
    profile:       the profile id.
    rotated:       ``True`` if a per-clone password was set and proven via PAM (old
                   rejected, new accepted); ``False`` if the image password was kept.
    password_mode: ``"unique"`` when ``rotated`` else ``"inherited"``.
    note:          a human-readable explanation when rotation was skipped or could not
                   complete (e.g. ``--no-rotate``, or SSH not reachable / key denied so the
                   image password was kept); ``None`` on a clean rotation.
    """

    name: str
    image: str
    profile: str
    rotated: bool
    password_mode: str
    note: str | None


@dataclass(frozen=True)
class RunResult:
    """The result of ``run()``. The core never replaces its own process; it either starts
    the VM detached or hands back the argv for the caller to exec in the foreground.

    name:     the clone that was (or is to be) run.
    detached: whether the VM was started in the background by the core.
    argv:     when ``detached`` is ``False``, the ``["tart", "run", ...]`` argv (including
              ``--rosetta=rosetta`` / ``--no-graphics`` as the record and flags dictate) for
              the caller to ``exec``; ``None`` when detached.
    log_path: when ``detached`` is ``True``, the path to the background run log; ``None``
              when foreground.
    """

    name: str
    detached: bool
    argv: list[str] | None
    log_path: str | None


@dataclass(frozen=True)
class SSHArgs:
    """The result of ``ssh_args()``: a ready-to-exec interactive SSH invocation for a clone.

    name:     the clone.
    username: the guest admin user to connect as.
    ip:       the VM's current IP (resolved via ``tart ip``, waiting for it to appear).
    argv:     the full ``ssh`` argv — host key pinned per VM name, per-VM known-hosts file,
              no agent/X11 forwarding, interactive (``BatchMode`` off). A caller appends any
              remote command and execs it.
    """

    name: str
    username: str
    ip: str
    argv: list[str]


@dataclass(frozen=True)
class EnrollResult:
    """The result of ``enroll()``.

    name:        the clone.
    service:     the service enrolled — ``"tailscale"`` | ``"warp"`` | ``"perimeter81"``.
    recorded:    whether the enrollment was written to the clone record. ``False`` for
                 ``perimeter81`` (manual — the script only prints instructions), ``True``
                 otherwise.
    enrolled_at: the ISO-8601 UTC timestamp recorded, or ``None`` when not recorded.
    """

    name: str
    service: str
    recorded: bool
    enrolled_at: str | None


@dataclass(frozen=True)
class RemoveResult:
    """The result of ``rm()``: what was torn down.

    name:             the clone that was removed.
    image:            the ``rbt-...`` image it had been cloned from (its lineage).
    keychain_deleted: whether a per-clone keychain entry was deleted (only when the clone
                      had its own unique password, i.e. account == ``name``).
    """

    name: str
    image: str
    keychain_deleted: bool


# ---- internal helpers ----------------------------------------------------------------------
# These mirror the private orchestration ``cli.py`` used to hold; they call ``clones`` and
# ``hostops`` for every record/keychain/tart/GUI-session action and never re-implement it.


def _current_image(pid: str) -> str | None:
    """The verified image the profile's committed lock produces (None if no valid lock)."""
    try:
        return image_name(load_profile(pid))
    except VerifyError:
        return None


def _profile_of(image: str) -> str | None:
    for pid in sorted(list_profiles(), key=len, reverse=True):
        if image.startswith(f"rbt-{pid}-") and _clones.IMAGE_RE.match(image):
            return pid
    return None


def _source_image(profile: str | None, image: str | None) -> tuple[str, dict]:
    """Resolve (verified image name, merged profile) from exactly one of profile/image."""
    if bool(profile) == bool(image):
        raise VerifyError("give exactly one of --profile or --image")
    if profile:
        prof = load_profile(profile)
        img = image_name(prof)
    else:
        img = image
        if not _clones.IMAGE_RE.match(img):
            raise VerifyError(f"{img!r} is not a verified RhubarbTart image name (rbt-PROFILE-SHA)")
        pid = _profile_of(img)
        if not pid:
            raise VerifyError(f"{img}: no matching profile in profiles/")
        prof = load_profile(pid)
    if img not in hostops.local_vms():
        raise VerifyError(f"{img} is not built on this Mac (./scripts/build.sh {prof['id']})")
    if hostops.keychain_get(img) is None:
        raise VerifyError(f"no keychain password for {img}; was it built on this Mac?")
    return img, prof


def _rotate(rec: dict) -> tuple[bool, str | None]:
    """Give the clone its own password (keychain account = clone name), proven via PAM.

    Returns ``(rotated, note)``: ``(True, None)`` on a clean rotation, ``(False, why)`` when
    the clone is unreachable / rejects our key so the image password is kept. Raises
    ``VerifyError`` if the rotation was attempted but could not be completed and verified.
    """
    name = rec["name"]
    old = hostops.keychain_get(rec["password_account"])
    new_pw = hostops.random_password()
    proc = hostops.start_vm(name, rec["rosetta"], headless=True)
    try:
        ip = hostops.vm_ip(name, rec["family"])
        reach = hostops.wait_for_ssh(name, rec["username"], ip)
        if reach != "ok":
            why = ("SSH refused our key: load it with ssh-add, or the image was built for other keys"
                   if reach == "denied" else "SSH not reachable (image built without SSH keys?)")
            return False, (f"{name}: {why}; keeping the image's password. "
                           f"Retry later with: rhubarb reset {name} --same-image")
        hostops.keychain_put(name, new_pw)
        try:
            hostops.rotate_password(name, rec["username"], ip, old, new_pw)
        except VerifyError:
            hostops.keychain_delete(name)
            raise
        rec["password_account"] = name
        _clones.save(rec)
        _clones.log_event("rotate", name)
        return True, None
    finally:
        hostops.shutdown(name, proc)  # never orphan the detached boot process (#17)


def _clone(name: str, image: str, prof: dict, rotate: bool) -> tuple[bool, str | None]:
    """Clone ``image`` to ``name``, write and log the record, then optionally rotate.

    Returns ``(rotated, note)`` describing the password outcome.
    """
    hostops.tart("clone", image, name)
    rec = _clones.new_record(name, prof, image)
    _clones.save(rec)
    _clones.log_event("new", name, image)
    if rotate:
        return _rotate(rec)
    return False, "password: inherited from the image (use without --no-rotate for a per-clone password)"


def _destroy(rec: dict) -> bool:
    """Stop, delete, forget the host key and drop a per-clone keychain entry.

    Returns whether a per-clone keychain entry was deleted (account == clone name).
    """
    name = rec["name"]
    try:
        hostops.stop_vm(name)
    except VerifyError:
        pass  # best-effort: delete by name still works if listing/stop is degraded (see #16)
    hostops.delete_vm(name)
    hostops.forget_host_key(name)
    keychain_deleted = rec["password_account"] == name
    if keychain_deleted:
        hostops.keychain_delete(name)
    return keychain_deleted


# ---- read-only queries ---------------------------------------------------------------------


def images() -> list[Image]:
    """List the RhubarbTart images (and vanilla/unverified precursors) present locally.

    Cross-references local Tart VMs with the committed locks and the clone records to mark
    each image current or outdated and count its clones. Mirrors ``rhubarb images``.

    Returns a list of ``Image``, sorted by name. Empty when no built images exist.
    Raises ``VerifyError`` / ``FileNotFoundError`` as described in the module docstring
    (e.g. ``tart`` not installed).
    """
    vms = hostops.local_vms()
    recs, _ = _clones.all_records()
    out: list[Image] = []
    for name in sorted(vms):
        kind = ("unverified" if name.endswith("-unverified") else
                "vanilla" if name.endswith("-vanilla") else
                "image" if _clones.IMAGE_RE.match(name) else None)
        if kind is None:
            continue
        pid = _profile_of(name.removesuffix("-unverified")) or "-"
        status = kind
        if kind == "image":
            status = "current" if _current_image(pid) == name else "outdated"
        out.append(Image(name=name, profile=pid, kind=kind, status=status,
                         clones=sum(r["image"] == name for r in recs)))
    return out


def clones() -> CloneList:
    """List every research clone, with live state and staleness.

    Loads each StrictModes-trusted clone record, joins it with live Tart state, derives
    image freshness and password mode, and collects any rejected records. Mirrors
    ``rhubarb list``.

    Returns a ``CloneList`` (trusted ``clones`` + ``problems``). Raises ``VerifyError`` /
    ``FileNotFoundError`` per the module docstring; individual bad records are surfaced in
    ``problems`` rather than raised.
    """
    vms = hostops.local_vms()
    recs, problems = _clones.all_records()
    out: list[Clone] = []
    for r in recs:
        vm = vms.get(r["name"])
        state = vm.get("State", "?") if vm else "MISSING"
        cur = _current_image(r["profile"])
        fresh = ("image-deleted" if r["image"] not in vms else
                 "current" if cur == r["image"] else "outdated")
        pw = "unique" if r["password_account"] == r["name"] else "inherited"
        out.append(Clone(
            name=r["name"], profile=r["profile"], family=r["family"], image=r["image"],
            state=state, freshness=fresh, password_mode=pw,
            password_account=r["password_account"], enrollments=sorted(r["enrollments"]),
            created_at=r["created_at"]))
    return CloneList(clones=out, problems=list(problems))


def provenance(vm: str) -> Provenance:
    """Read and parse the provenance record for ``vm`` (``out/<vm>.provenance.json``).

    Read-only: it does not (re)generate the record. Returns a ``Provenance``.
    Raises ``FileNotFoundError`` if no record exists for ``vm``, and ``VerifyError`` if the
    record is malformed.
    """
    if "/" in vm or "\\" in vm or vm in ("", ".", ".."):
        raise VerifyError(f"invalid vm name {vm!r}")
    path = ROOT / "out" / f"{vm}.provenance.json"
    text = path.read_text()  # FileNotFoundError propagates (no record) per the contract
    try:
        rec = json.loads(text)
    except json.JSONDecodeError as e:
        raise VerifyError(f"{path.name}: malformed provenance record ({e})") from None
    try:
        return Provenance(
            vm=rec["vm"], profile=rec["profile"], built_at=rec["built_at"],
            git_commit=rec["git_commit"], git_dirty=rec["git_dirty"],
            toolchain=rec["toolchain"], tart_vm=rec["tart_vm"],
            inputs_sha256=rec["inputs_sha256"], lock_sha256=rec["lock_sha256"],
            lock=rec["lock"], build_files=rec["build_files"])
    except (KeyError, TypeError) as e:
        raise VerifyError(f"{path.name}: malformed provenance record ({e})") from None


# ---- operations ----------------------------------------------------------------------------


def new(name: str, profile: str | None = None, image: str | None = None,
        rotate: bool = True) -> NewResult:
    """Create a research clone named ``name`` from a verified image.

    Give exactly one of ``profile`` (clone the image its committed lock produces) or
    ``image`` (an explicit ``rbt-PROFILE-SHA`` name that must exist locally and match a
    profile). The source image must be built on this host and have a keychain password.
    Clones via ``tart clone``, writes the clone record, and — unless ``rotate`` is False —
    boots the clone headless to give it its own per-clone password, proven via PAM. If the
    clone is unreachable or rejects the key, the image password is kept (reported in the
    result's ``note``). Mirrors ``rhubarb new``.

    Returns a ``NewResult``. Raises ``VerifyError`` (not exactly one of profile/image, bad
    name, name already taken, image not built here, no keychain password, or a rotation
    that could not be completed and verified) / ``FileNotFoundError`` per the module
    docstring.
    """
    name = _clones.check_clone_name(name)
    if name in hostops.local_vms():
        raise VerifyError(f"a VM named {name} already exists")
    img, prof = _source_image(profile, image)
    rotated, note = _clone(name, img, prof, rotate)
    return NewResult(name=name, image=img, profile=prof["id"], rotated=rotated,
                     password_mode="unique" if rotated else "inherited", note=note)


def run(name: str, headless: bool = False, detach: bool = False) -> RunResult:
    """Start the clone ``name``, or return the argv to run it in the foreground.

    With ``detach`` the VM is started in the background (own session, output to a per-clone
    log) and the result carries ``log_path``. Without ``detach`` the result carries the
    ``argv`` for the caller to exec (the core does not replace its own process). ``headless``
    adds ``--no-graphics``; Rosetta is applied per the clone's record. Mirrors ``rhubarb
    run``.

    Returns a ``RunResult``. Raises ``VerifyError`` (unknown/untrusted clone, or already
    running) / ``FileNotFoundError`` per the module docstring.
    """
    rec = _clones.load(name)
    if hostops.is_running(rec["name"]):
        raise VerifyError(f"{rec['name']} is already running")
    if detach:
        logs = _clones._secure_dir(_clones.state_dir() / "logs")
        log = logs / f"{rec['name']}.log"
        hostops.start_vm(rec["name"], rec["rosetta"], headless, log)
        return RunResult(name=rec["name"], detached=True, argv=None, log_path=str(log))
    argv = ["tart", "run", *(["--rosetta=rosetta"] if rec["rosetta"] else []),
            *(["--no-graphics"] if headless else []), rec["name"]]
    return RunResult(name=rec["name"], detached=False, argv=argv, log_path=None)


def ssh_args(name: str) -> SSHArgs:
    """Resolve the clone's IP and build a ready-to-exec interactive SSH invocation.

    Waits (bounded) for the VM's IP via ``tart ip``, then assembles the pinned, no-forward
    SSH argv used by ``rhubarb ssh``. Does not connect; the caller execs ``argv`` (appending
    any remote command).

    Returns an ``SSHArgs``. Raises ``VerifyError`` (unknown/untrusted clone, or no IP — not
    running) / ``FileNotFoundError`` per the module docstring.
    """
    rec = _clones.load(name)
    ip = hostops.vm_ip(rec["name"], rec["family"], wait=60)
    argv = hostops.ssh_args(rec["name"], rec["username"], ip, batch=False)
    return SSHArgs(name=rec["name"], username=rec["username"], ip=ip, argv=argv)


def enroll(name: str, service: str, org: str | None = None) -> EnrollResult:
    """Enroll the clone into a VPN/ZTNA service via ``scripts/enroll.sh``.

    ``service`` is one of ``tailscale`` | ``warp`` | ``perimeter81``; ``org`` is the optional
    team/organization. The enrollment secret is handled by the core (keychain), never by the
    caller. On success the enrollment is recorded in the clone's record — except
    ``perimeter81``, which is manual (the script only prints instructions), so nothing is
    recorded. Mirrors ``rhubarb enroll``.

    Returns an ``EnrollResult``. Raises ``VerifyError`` (unknown/untrusted clone, unknown
    service, or the enrollment script failed) / ``FileNotFoundError`` per the module
    docstring.
    """
    rec = _clones.load(name)
    if service not in _clones.SERVICES:
        raise VerifyError(f"unknown service {service!r}; choose one of {sorted(_clones.SERVICES)}")
    cmd = [str(ROOT / "scripts" / "enroll.sh"), rec["name"], service, "--image", rec["password_account"]]
    if org:
        cmd += ["--org", org]
    res = subprocess.run(cmd, env=hostops.env_with(RHUBARB_USER=rec["username"]))
    if res.returncode != 0:
        raise VerifyError(f"enrollment failed ({service})")
    if service == "perimeter81":  # P81 is manual; the script only prints instructions
        return EnrollResult(name=rec["name"], service=service, recorded=False, enrolled_at=None)
    ts = _clones.now()
    rec["enrollments"][service] = ts
    _clones.save(rec)
    _clones.log_event("enroll", rec["name"], service)
    return EnrollResult(name=rec["name"], service=service, recorded=True, enrolled_at=ts)


def reset(name: str, same_image: bool = False, rotate: bool = True) -> NewResult:
    """Reset the clone to a clean state: destroy it and re-clone under the same name.

    Tears the clone down (stops it, ``tart delete``, forgets its host key, and deletes its
    per-clone keychain entry if it had one), deletes its record, then re-clones — from the
    profile's current image, or from its existing lineage image when ``same_image`` is True
    (which must still be built here). Enrollment and identity are gone. The re-clone rotates
    to a fresh per-clone password by default (as ``rhubarb reset`` does); pass
    ``rotate=False`` to keep the image password (the CLI's ``--no-rotate``). Mirrors
    ``rhubarb reset``.

    Returns a ``NewResult`` describing the fresh clone. Raises ``VerifyError`` (unknown/
    untrusted clone, or the target image is not built here) / ``FileNotFoundError`` per the
    module docstring.
    """
    rec = _clones.load(name)
    prof = load_profile(rec["profile"])
    img = rec["image"] if same_image else image_name(prof)
    if img not in hostops.local_vms():
        raise VerifyError(f"{img} is not built on this Mac")
    _destroy(rec)
    _clones.delete(rec["name"])
    _clones.log_event("reset", rec["name"], img)
    rotated, note = _clone(rec["name"], img, prof, rotate)
    return NewResult(name=rec["name"], image=img, profile=prof["id"], rotated=rotated,
                     password_mode="unique" if rotated else "inherited", note=note)


def rm(name: str) -> RemoveResult:
    """Remove the clone ``name`` and its per-clone secrets.

    Stops the VM, ``tart delete``s it, forgets its pinned host key, and deletes its keychain
    entry if it had a unique password, then deletes the record. Confirmation is the
    frontend's responsibility; the core just removes. Mirrors ``rhubarb rm`` (minus the
    interactive prompt).

    Returns a ``RemoveResult``. Raises ``VerifyError`` (unknown/untrusted clone) /
    ``FileNotFoundError`` per the module docstring.
    """
    rec = _clones.load(name)
    keychain_deleted = _destroy(rec)
    _clones.delete(rec["name"])
    _clones.log_event("rm", rec["name"])
    return RemoveResult(name=rec["name"], image=rec["image"], keychain_deleted=keychain_deleted)
