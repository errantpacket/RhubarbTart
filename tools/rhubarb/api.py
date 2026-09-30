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
import os
import subprocess
import time
from collections.abc import Callable
from dataclasses import dataclass

from . import clones as _clones
from . import engagements as _engagements
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
                      ``"outdated"`` (a newer image exists), ``"image-deleted"``
                      (the lineage image is no longer present locally), or, for a stacked
                      clone, ``"base-missing"`` (its registry base blob is gone; it won't boot).
    password_mode:    ``"unique"`` (its own keychain password, account == ``name``) or
                      ``"inherited"`` (still shares the image's password).
    password_account: the keychain account holding its password (``name`` or ``image``).
    enrollments:      sorted service names it is enrolled in (subset of tailscale/warp/
                      warp); empty when none.
    created_at:       ISO-8601 UTC timestamp the record was created.
    engagement:       the engagement id this clone belongs to (its lineage tag), or ``None``
                      for an ad-hoc clone. Holds no secret. Populated from the record.
    base_ref:         for a stacked clone (#31), the registry ``ref@digest`` it is stacked
                      on; ``None`` for an ordinary APFS clone.
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
    engagement: str | None = None
    base_ref: str | None = None


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
    ssh:           ``{"enabled", "key_fingerprints", "from"}`` — the SSH access the image
                   was sealed with — or ``None`` for records written before it was recorded.
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
    ssh: dict | None = None


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
    service:     the service enrolled — ``"tailscale"`` | ``"warp"``.
    recorded:    whether the enrollment was written to the clone record (``True`` for every
                 current service; kept so a manual-only service can report ``False``).
    enrolled_at: the ISO-8601 UTC timestamp recorded, or ``None`` when not recorded.
    detail:      captured enroll.sh output (status lines / manual instructions), for the
                 caller to surface. No secrets (those move over SSH stdin, never printed).
    """

    name: str
    service: str
    recorded: bool
    enrolled_at: str | None
    detail: str | None = None


@dataclass(frozen=True)
class RemoveResult:
    """The result of ``rm()``: what was torn down.

    name:             the clone that was removed.
    image:            the ``rbt-...`` image it had been cloned from (its lineage).
    keychain_deleted: whether a per-clone keychain entry was deleted (only when the clone
                      had its own unique password, i.e. account == ``name``).
    base_released:    for a stacked clone (#31): whether its pulled registry base was dropped
                      because no other clone uses it. Always ``False`` for ordinary clones.
    """

    name: str
    image: str
    keychain_deleted: bool
    base_released: bool = False


@dataclass(frozen=True)
class ProvisionResult:
    """The result of ``provision()``: the engagement's clone set, standing up as one unit.

    engagement: the engagement id that was provisioned.
    created:    a ``NewResult`` per clone actually created this run (in provision order),
                each carrying its image lineage and password outcome.
    skipped:    names that already existed (a VM or a clone record) and so were left
                untouched rather than duplicated — reported, never silently re-created.
    """

    engagement: str
    created: list[NewResult]
    skipped: list[str]


@dataclass(frozen=True)
class Tunnel:
    """One manifest link, made concrete for one source clone by ``connect_plan()`` (#30).

    src / dst: the clone that gets the ports on its loopback, and the clone serving them.
    dst_ip:    the target clone's current vmnet address (the host connects to it).
    ports:     the TCP ports forwarded (same number on both ends).
    argv:      the ``ssh -N -R 127.0.0.1:P:dst_ip:P …`` invocation into ``src``, over the
               clone's pinned host key and the operator's key, like ``rhubarb ssh``.
    """

    src: str
    dst: str
    dst_ip: str
    ports: tuple[int, ...]
    argv: list[str]


# ---- internal helpers ----------------------------------------------------------------------
# These mirror the private orchestration ``cli.py`` used to hold; they call ``clones`` and
# ``hostops`` for every record/keychain/tart/GUI-session action and never re-implement it.


# Progress sink: an optional ``progress(msg)`` callback the long operations (``new`` /
# ``reset`` and the internal clone/rotate steps) call as each milestone completes, so a
# frontend can stream live status. See the module-level ``new``/``reset`` docstrings and
# docs/INTERFACE-PLAN.md (#19). ``None`` (the default) is silent — pure, no side effects —
# which is why existing callers and tests are unaffected.
ProgressFn = Callable[[str], None]


def _emit(progress: ProgressFn | None, msg: str) -> None:
    """Send one milestone line to the progress sink, if a frontend supplied one."""
    if progress is not None:
        progress(msg)


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


def _image_ssh_enabled(image: str) -> bool | None:
    """Whether ``image`` was sealed with SSH access, per its provenance record; ``None`` when
    unknown (no record, or one written before the ``ssh`` field existed)."""
    try:
        ssh = provenance(image).ssh
    except (FileNotFoundError, VerifyError):
        return None
    return None if ssh is None else bool(ssh.get("enabled"))


def _rotate(rec: dict, progress: ProgressFn | None = None) -> tuple[bool, str | None]:
    """Give the clone its own password (keychain account = clone name), proven via PAM.

    Returns ``(rotated, note)``: ``(True, None)`` on a clean rotation, ``(False, why)`` when
    the clone is unreachable / rejects our key so the image password is kept. Raises
    ``VerifyError`` if the rotation was attempted but could not be completed and verified.

    ``progress`` (optional) streams the same milestones the CLI used to print live —
    ``"<name>: booting headless to rotate its password"`` before the headless boot and
    ``"<name>: unique password set ..."`` on a clean rotation; ``None`` is silent.
    """
    name = rec["name"]
    # Rotation runs over SSH; an image sealed without authorized keys has sshd disabled, so
    # booting it would only wait out two full SSH timeouts before soft-failing. (#46)
    if _image_ssh_enabled(rec["image"]) is False:
        return False, (f"{name}: image {rec['image']} was built with SSH disabled "
                       f"(RHUBARB_SSH_PUBKEYS unset), so the password can't be rotated; keeping "
                       f"the image's password. Rebuild with RHUBARB_SSH_PUBKEYS=<pubkey file>.")
    old = hostops.keychain_get(rec["password_account"])
    new_pw = hostops.random_password()
    # A fresh clone's first boot is normally SSH-reachable in seconds, but that first boot can
    # occasionally be slow, or hand back a stale DHCP lease right after clone — making a single
    # wait_for_ssh time out and soft-fail the rotation. A second boot reliably comes up, so retry
    # the boot on "unreachable" before giving up; "denied" (a key problem) won't improve, so bail. (#23)
    note = (f"{name}: SSH not reachable after 2 boots; keeping the image's password. "
            f"Retry later with: rhubarb reset {name} --same-image (if the image was built "
            f"without RHUBARB_SSH_PUBKEYS, its SSH is disabled and a retry won't help)")
    for attempt in range(2):
        _emit(progress, f"{name}: booting headless to rotate its password"
              + (" (retry)" if attempt else ""))
        proc = hostops.start_vm(name, rec["rosetta"], headless=True)
        try:
            try:
                ip = hostops.vm_ip(name, rec["family"])
                _emit(progress, f"{name}: booted at {ip}; waiting for SSH to come up…")
                reach = hostops.wait_for_ssh(name, rec["username"], ip, progress=progress)
            except VerifyError:
                _emit(progress, f"{name}: no IP on boot {attempt + 1}")
                reach = "unreachable"  # no IP this boot — reboot and retry
            if reach == "denied":
                return False, (f"{name}: SSH refused our key (load it with ssh-add, or the image "
                               f"was built for other keys); keeping the image's password.")
            if reach != "ok":
                _emit(progress, f"{name}: SSH unreachable on boot {attempt + 1}"
                      + (" — rebooting to retry" if attempt == 0 else ""))
                continue  # transient: the finally reaps this boot, then we try once more
            hostops.keychain_put(name, new_pw)
            try:
                hostops.rotate_password(name, rec["username"], ip, old, new_pw)
            except VerifyError:
                hostops.keychain_delete(name)
                raise
            rec["password_account"] = name
            _clones.save(rec)
            _clones.log_event("rotate", name)
            _emit(progress, f"{name}: unique password set (keychain account {name}); old password rejected")
            return True, None
        finally:
            hostops.shutdown(name, proc)  # never orphan the detached boot process (#17)
    return False, note


# ---- registry-backed stacked clones (#31) ----------------------------------------------
# Tart can stack a clone on an immutable base pulled from a registry, but only for macOS images.
# The base disk lands in Tart's content store; the clone's overlay depends on that blob, which
# `tart list` stops showing once the OCI entry is pruned, so its digest is recorded and checked.
TART_CONTENT = hostops.VMS_DIR.parent / "cache" / "content" / "sha256"
_DISK_DIGEST_KEY = "org.cirruslabs.tart.disk-file-content-digest"


def _published_ref(image: str) -> tuple[str, str]:
    """(ref@digest, registry) this host published ``image`` as (scripts/publish.sh)."""
    path = ROOT / "out" / f"{image}.published.json"
    try:
        rec = json.loads(path.read_text())
    except FileNotFoundError:
        raise VerifyError(f"{image} is not published; run ./scripts/publish.sh publish {image}") from None
    except json.JSONDecodeError as e:
        raise VerifyError(f"{path.name}: malformed publication record ({e})") from None
    ref, registry = str(rec.get("ref", "")), str(rec.get("registry", ""))
    if rec.get("image") != image or not _clones.BASE_REF_RE.match(ref) or not ref.startswith(f"{registry}/"):
        raise VerifyError(f"{path.name}: malformed publication record")
    return ref, registry


def _verify_published(ref: str, registry: str) -> None:
    """Signature + provenance attestation against the committed key, via the same
    ``publish.sh verify`` path consumers use (cosign egress pinned to the registry)."""
    res = subprocess.run([str(ROOT / "scripts" / "publish.sh"), "verify", ref], capture_output=True,
                         text=True, env={**os.environ, "RHUBARB_REGISTRY": registry})
    if res.returncode != 0:
        why = (res.stderr.strip().splitlines() or ["verification failed"])[-1]
        raise VerifyError(f"registry image failed verification, not cloning: {why}")


def _stacked_disk_digest(name: str) -> str:
    """The base disk blob a stacked clone's overlay depends on (from its OCI manifest)."""
    try:
        manifest = json.loads((hostops.VMS_DIR / name / "manifest.json").read_text())
    except (FileNotFoundError, json.JSONDecodeError) as e:
        raise VerifyError(f"{name}: stacked clone has no readable manifest ({e})") from None
    digests = {(layer.get("annotations") or {}).get(_DISK_DIGEST_KEY) for layer in manifest.get("layers", [])}
    digests.discard(None)
    if len(digests) != 1 or not _clones.DIGEST_RE.match(next(iter(digests))):
        raise VerifyError(f"{name}: cannot tell which base disk the stacked clone uses ({sorted(digests)})")
    return digests.pop()


def base_present(rec: dict) -> bool:
    """False only for a stacked clone whose base blob is gone from Tart's content store."""
    base = rec.get("base")
    return base is None or (TART_CONTENT / base["disk_digest"].removeprefix("sha256:")).is_file()


def _release_base(base: dict, removed: str) -> bool:
    """After the last stacked clone on ``base`` is removed, drop the pulled base (Tart's OCI
    entry + the content blob) so it doesn't linger invisibly (26 GB for a macOS image).
    Returns whether it was released; keeps it while any record or VM still references it."""
    recs, _problems = _clones.all_records()
    if any((r.get("base") or {}).get("disk_digest") == base["disk_digest"]
           for r in recs if r["name"] != removed):
        return False
    hexd = base["disk_digest"].removeprefix("sha256:")
    for manifest in hostops.VMS_DIR.glob("*/manifest.json"):
        if manifest.parent.name != removed and hexd in manifest.read_text():
            return False
    hostops.tart("delete", base["ref"], capture=True, check=False)  # the OCI cache entry, if any
    (TART_CONTENT / hexd).unlink(missing_ok=True)
    return True


def _clone(name: str, image: str, prof: dict, rotate: bool,
           progress: ProgressFn | None = None, engagement: str | None = None,
           from_registry: bool = False) -> tuple[bool, str | None]:
    """Clone ``image`` to ``name``, write and log the record, then optionally rotate.

    ``from_registry`` (#31, macOS only): verify the image's published copy (signature +
    provenance, by digest) and stack the clone on it instead of an APFS copy of the local image.

    Returns ``(rotated, note)`` describing the password outcome. ``progress`` (optional)
    streams milestones live (``"cloned <image> -> <name>"``, then the rotation lines); it is
    forwarded to ``_rotate``. ``None`` is silent. ``engagement`` (optional) tags the clone
    record with the engagement it belongs to (``None`` for an ad-hoc clone).
    """
    base = None
    if from_registry:
        if prof["family"] != "macos":
            raise VerifyError("--from-registry: Tart supports stacked clones only for macOS images")
        ref, registry = _published_ref(image)
        _emit(progress, f"verifying {ref}")
        _verify_published(ref, registry)
        net = ["--insecure"] if registry.startswith(("127.0.0.1:", "localhost:")) else []
        hostops.tart("clone", *net, "--stacked", ref, name)
        try:
            base = {"ref": ref, "disk_digest": _stacked_disk_digest(name)}
        except VerifyError:
            hostops.delete_vm(name)  # never leave an unrecorded half-made clone behind
            raise
        _emit(progress, f"stacked {name} on {ref}")
    else:
        hostops.tart("clone", image, name)
        _emit(progress, f"cloned {image} -> {name}")
    rec = _clones.new_record(name, prof, image, engagement=engagement, base=base)
    _clones.save(rec)
    _clones.log_event("new", name, image)
    if rotate:
        return _rotate(rec, progress)
    return False, "password: inherited from the image (use without --no-rotate for a per-clone password)"


def _destroy(rec: dict) -> bool:
    """Stop, delete, forget the host key and drop a per-clone keychain entry.

    Returns whether a per-clone keychain entry was deleted (account == clone name).
    """
    name = rec["name"]
    hostops.shutdown(name)  # stop + reap any lingering `tart run <name>` so rm never orphans (#18)
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
        if r.get("base"):   # stacked (#31): what matters is the base blob, not the local image
            fresh = ("base-missing" if not base_present(r) else
                     "current" if cur == r["image"] else "outdated")
        else:
            fresh = ("image-deleted" if r["image"] not in vms else
                     "current" if cur == r["image"] else "outdated")
        pw = "unique" if r["password_account"] == r["name"] else "inherited"
        out.append(Clone(
            name=r["name"], profile=r["profile"], family=r["family"], image=r["image"],
            state=state, freshness=fresh, password_mode=pw,
            password_account=r["password_account"], enrollments=sorted(r["enrollments"]),
            created_at=r["created_at"], engagement=r["engagement"],
            base_ref=(r.get("base") or {}).get("ref")))
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
            lock=rec["lock"], build_files=rec["build_files"], ssh=rec.get("ssh"))
    except (KeyError, TypeError) as e:
        raise VerifyError(f"{path.name}: malformed provenance record ({e})") from None


# ---- operations ----------------------------------------------------------------------------


def new(name: str, profile: str | None = None, image: str | None = None,
        rotate: bool = True, progress: ProgressFn | None = None,
        engagement: str | None = None, from_registry: bool = False) -> NewResult:
    """Create a research clone named ``name`` from a verified image.

    Give exactly one of ``profile`` (clone the image its committed lock produces) or
    ``image`` (an explicit ``rbt-PROFILE-SHA`` name that must exist locally and match a
    profile). The source image must be built on this host and have a keychain password.
    Clones via ``tart clone``, writes the clone record, and — unless ``rotate`` is False —
    boots the clone headless to give it its own per-clone password, proven via PAM. If the
    clone is unreachable or rejects the key, the image password is kept (reported in the
    result's ``note``). Mirrors ``rhubarb new``.

    ``progress`` (optional) is a ``Callable[[str], None]`` the operation calls with each
    milestone as it completes (``"cloned <image> -> <name>"``, ``"<name>: booting headless
    to rotate its password"``, ``"<name>: unique password set ..."``), letting a frontend
    stream live status. Omit it (the default ``None``) for pure, silent behavior — existing
    callers and tests are unaffected.

    ``from_registry`` (#31, macOS only): stack the clone on the image's published registry copy
    (``tart clone --stacked``) after verifying its signature and provenance by digest, instead
    of an APFS copy of the local image. Needs ``scripts/publish.sh publish`` first.

    ``engagement`` (optional) tags the clone's record with the engagement id it belongs to;
    ``None`` (the default) is an ad-hoc clone. It only records lineage — no other behavior
    changes. Stage 1B's ``provision`` passes it; ``rhubarb new`` leaves it ``None``.

    Returns a ``NewResult``. Raises ``VerifyError`` (not exactly one of profile/image, bad
    name, name already taken, image not built here, no keychain password, or a rotation
    that could not be completed and verified) / ``FileNotFoundError`` per the module
    docstring.
    """
    name = _clones.check_clone_name(name)
    if name in hostops.local_vms():
        raise VerifyError(f"a VM named {name} already exists")
    img, prof = _source_image(profile, image)
    rotated, note = _clone(name, img, prof, rotate, progress, engagement=engagement,
                           from_registry=from_registry)
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

    ``service`` is one of ``tailscale`` | ``warp``; ``org`` is the optional team/organization.
    The enrollment secret is handled by the core (keychain), never by the caller. On success
    the enrollment is recorded in the clone's record. Mirrors ``rhubarb enroll``.

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
    # Capture so callers (the TUI in particular, whose subprocess has no terminal) get the
    # real reason on failure and the status/instructions on success. enroll.sh prints only
    # status/errors — the secrets travel over SSH stdin and are never echoed.
    res = subprocess.run(cmd, env=hostops.env_with(RHUBARB_USER=rec["username"]),
                         capture_output=True, text=True)
    out = "\n".join(p.strip() for p in (res.stdout, res.stderr) if p and p.strip()).strip()
    if res.returncode != 0:
        reason = next((ln for ln in reversed(out.splitlines()) if ln.strip()), "enroll.sh failed")
        raise VerifyError(f"enrollment failed ({service}): {reason}")
    ts = _clones.now()
    rec["enrollments"][service] = ts
    _clones.save(rec)
    _clones.log_event("enroll", rec["name"], service)
    return EnrollResult(name=rec["name"], service=service, recorded=True,
                        enrolled_at=ts, detail=out or None)


def reset(name: str, same_image: bool = False, rotate: bool = True,
          progress: ProgressFn | None = None) -> NewResult:
    """Reset the clone to a clean state: destroy it and re-clone under the same name.

    Tears the clone down (stops it, ``tart delete``, forgets its host key, and deletes its
    per-clone keychain entry if it had one), deletes its record, then re-clones — from the
    profile's current image, or from its existing lineage image when ``same_image`` is True
    (which must still be built here). Enrollment and identity are gone. The re-clone rotates
    to a fresh per-clone password by default (as ``rhubarb reset`` does); pass
    ``rotate=False`` to keep the image password (the CLI's ``--no-rotate``). Mirrors
    ``rhubarb reset``.

    ``progress`` (optional) streams the same live milestones as ``new`` — plus the teardown
    line ``"<name>: destroyed ...; re-cloning from <image>"`` before the re-clone, so the
    stream stays in order. Omit it (default ``None``) for pure, silent behavior.

    Returns a ``NewResult`` describing the fresh clone. Raises ``VerifyError`` (unknown/
    untrusted clone, or the target image is not built here) / ``FileNotFoundError`` per the
    module docstring.
    """
    rec = _clones.load(name)
    prof = load_profile(rec["profile"])
    img = rec["image"] if same_image else image_name(prof)
    if img not in hostops.local_vms():
        raise VerifyError(f"{img} is not built on this Mac")
    stacked = rec.get("base") is not None   # a stacked clone stays stacked (#31)
    if stacked:
        _published_ref(img)   # fail before destroying anything if the target isn't published
    _destroy(rec)
    _clones.delete(rec["name"])
    _clones.log_event("reset", rec["name"], img)
    _emit(progress, f"{rec['name']}: destroyed (enrollment and identity are gone); re-cloning from {img}")
    # Keep the engagement tag: a reset clone is still part of its engagement (#89).
    rotated, note = _clone(rec["name"], img, prof, rotate, progress, engagement=rec.get("engagement"),
                           from_registry=stacked)
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
    released = bool(rec.get("base")) and _release_base(rec["base"], rec["name"])
    return RemoveResult(name=rec["name"], image=rec["image"], keychain_deleted=keychain_deleted,
                        base_released=released)


# ---- engagements ---------------------------------------------------------------------------
# An engagement (engagements/<id>.json, loaded + strictly validated by ``engagements.py``) is
# the scoped unit: its ranges build and tear down as one. These functions act only on identity
# + ranges — they clone verified images via ``new`` and remove tagged clones via ``rm``, never
# re-implementing any tart/keychain/record logic. See docs/ENGAGEMENT-PLAN.md (Stage 1B).


def engagements() -> list[str]:
    """The defined engagement ids (``engagements/<id>.json``), sorted. Read-only."""
    return _engagements.list_engagements()


def engagement_clones(engagement: str) -> list[Clone]:
    """Every trusted clone tagged to ``engagement`` (record ``engagement`` == this id).

    A view over ``clones()`` filtered by the lineage tag — same StrictModes trust and live
    state. Untagged (ad-hoc) clones and clones of other engagements are excluded. Read-only.
    """
    return [c for c in clones().clones if c.engagement == engagement]


def _range_names(engagement: str, rng: _engagements.Range) -> list[str]:
    """The clone names a range stands up: its ``prefix`` (or ``<engagement>-<profile>``),
    bare when ``count == 1`` else suffixed ``-1``..``-count``. Each is validated as a clone
    name (never ``rbt-``); an over-long/invalid name raises ``VerifyError`` before any clone."""
    base = _engagements.range_stem(engagement, rng)
    names = [base] if rng.count == 1 else [f"{base}-{i}" for i in range(1, rng.count + 1)]
    for name in names:
        _clones.check_clone_name(name)
    return names


def provision(engagement: str) -> ProvisionResult:
    """Stand up an engagement's clone set from its ranges, as one unit.

    Loads + strictly validates ``engagements/<engagement>.json``, then for each range resolves
    the profile's current verified image and REFUSES (``VerifyError``) if that image is not
    built + keychain-backed on this Mac — checking every range up front, so a missing image
    provisions nothing. Each range then clones ``count`` copies via ``new`` (rotating a
    per-clone password, like ``rhubarb new``), tagged with the engagement id, named from the
    range ``prefix`` or ``<engagement>-<profile>`` (bare for one, ``-1``..``-count`` for more).
    A name that already exists (a VM or a clone record) is reported in ``skipped``, never
    duplicated.

    Returns a ``ProvisionResult`` (``created`` NewResults + ``skipped`` names). Raises
    ``VerifyError`` (bad/absent manifest, an unbuilt/unverified range image, an invalid clone
    name) / ``FileNotFoundError`` per the module docstring.
    """
    eng = _engagements.load_engagement(engagement)
    # Resolve + verify every range's image first (each raises VerifyError if not built here),
    # so we refuse a partial provision rather than clone some ranges and then fail on another.
    plan: list[tuple[str, str]] = []   # (clone name, verified image)
    for rng in eng.ranges:
        img, _prof = _source_image(rng.profile, None)
        for name in _range_names(eng.id, rng):
            plan.append((name, img))
    # Skip any name already taken by a VM or an existing clone record (reported, not re-created);
    # track created names too, so two ranges can't collide within one provision.
    existing = set(hostops.local_vms())
    recs, _ = _clones.all_records()
    existing |= {r["name"] for r in recs}
    created: list[NewResult] = []
    skipped: list[str] = []
    for name, img in plan:
        if name in existing:
            skipped.append(name)
            continue
        created.append(new(name, image=img, engagement=eng.id))
        existing.add(name)
    return ProvisionResult(engagement=eng.id, created=created, skipped=skipped)


def teardown(engagement: str) -> list[str]:
    """Tear down every clone tagged to ``engagement`` via ``rm`` (stop + delete + forget host
    key + drop any per-clone keychain entry, reaping strays so nothing is orphaned — #18).

    Idempotent: an engagement with no tagged clones removes nothing and returns ``[]``. Other
    engagements' clones and ad-hoc clones are untouched. Returns the removed names, in the
    order removed. Raises ``VerifyError`` / ``FileNotFoundError`` per the module docstring.
    """
    removed: list[str] = []
    for clone in engagement_clones(engagement):
        rm(clone.name)
        removed.append(clone.name)
    return removed


# ---- engagement links (#30) ----------------------------------------------------------------
# Clones can't reach each other: Tart's vmnet bridge isolation drops VM-to-VM traffic. A
# manifest's ``links`` are the only sanctioned path, and they run through the host: an SSH
# remote forward into each source clone puts the target's declared ports on the source's own
# loopback. Nothing else crosses, and the path exists only while ``connect`` runs.

def connect_plan(engagement: str) -> list[Tunnel]:
    """The tunnels ``connect`` would open: one per (link, source clone). Read-only.

    Raises ``VerifyError`` when the manifest declares no links, or a linked clone isn't
    provisioned (tagged to this engagement) or isn't running (no IP).
    """
    eng = _engagements.load_engagement(engagement)
    if not eng.links:
        raise VerifyError(f"engagement {eng.id} declares no links (add \"links\" to "
                          f"engagements/{eng.id}.json)")
    names = {_engagements.range_stem(eng.id, r): _range_names(eng.id, r) for r in eng.ranges}
    tagged = {c.name for c in engagement_clones(eng.id)}

    def running(name: str) -> tuple[dict, str]:
        if name not in tagged:
            raise VerifyError(f"{name} is not provisioned; run: rhubarb engagement provision {eng.id}")
        rec = _clones.load(name)
        return rec, hostops.vm_ip(name, rec["family"], wait=30)

    plan: list[Tunnel] = []
    for link in eng.links:
        dst = names[link.dst][0]
        _drec, dst_ip = running(dst)
        forwards = [a for p in link.ports for a in ("-R", f"127.0.0.1:{p}:{dst_ip}:{p}")]
        for src in names[link.src]:
            srec, src_ip = running(src)
            base = hostops.ssh_args(src, srec["username"], src_ip)
            argv = [*base[:-1], "-N", "-o", "ExitOnForwardFailure=yes",
                    "-o", "ServerAliveInterval=15", "-o", "ServerAliveCountMax=3",
                    *forwards, base[-1]]
            plan.append(Tunnel(src=src, dst=dst, dst_ip=dst_ip, ports=link.ports, argv=argv))
    return plan


def connect(engagement: str, progress: ProgressFn | None = None) -> None:
    """Open every link of the engagement and hold them until one drops or the caller
    interrupts (KeyboardInterrupt propagates). Always closes all tunnels on the way out.

    Raises ``VerifyError`` per ``connect_plan``, or when a tunnel closes (ssh's own error
    is on stderr: e.g. the port is already bound on the source clone).
    """
    plan = connect_plan(engagement)
    procs: list[subprocess.Popen] = []
    try:
        for t in plan:
            procs.append(subprocess.Popen(t.argv, stdin=subprocess.DEVNULL))
            ports = ", ".join(f"127.0.0.1:{p}" for p in t.ports)
            _emit(progress, f"{t.src}: {ports} -> {t.dst} ({t.dst_ip})")
        while True:
            for t, proc in zip(plan, procs, strict=True):
                rc = proc.poll()
                if rc is not None:
                    raise VerifyError(f"link {t.src} -> {t.dst} closed (ssh exit {rc})")
            time.sleep(1)
    finally:
        for proc in procs:
            if proc.poll() is None:
                proc.terminate()
        for proc in procs:
            try:
                proc.wait(timeout=10)
            except subprocess.TimeoutExpired:
                proc.kill()
