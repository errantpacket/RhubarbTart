"""The structured values the typed core returns (#128).

Every public ``rhubarb.api`` function returns one of these frozen dataclasses instead of printing
or returning argv. They live here so ``api.py`` holds behaviour only; ``api`` re-exports each of
them, so callers keep using ``api.Image``, ``api.CloneList`` and so on.
"""

from dataclasses import dataclass


@dataclass(frozen=True)
class Image:
    """A local Tart VM that is a RhubarbTart image (or a vanilla/unverified precursor).

    Mirrors one row of ``rhubarbtart images``.

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
    """A research clone created by ``rhubarbtart new``. Mirrors one row of ``rhubarbtart list``,
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
              name mismatch). Mirrors the ``IGNORED ...`` lines ``rhubarbtart list`` prints;
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
class LogRef:
    """One readable log, as listed by ``list_logs()`` for the TUI's Logs tab (#120).

    id:     the key ``read_log()`` accepts: ``logs/<file>.log`` or ``events.log``.
    label:  a short display name (the clone, ``build <profile>``, or ``events``).
    kind:   ``"clone"`` (a clone's run log), ``"build"`` (a build's output) or ``"events"``
            (the append-only lifecycle trail).
    size:   bytes on disk.
    mtime:  ISO-8601 UTC time of the last write.
    """

    id: str
    label: str
    kind: str
    size: int
    mtime: str


@dataclass(frozen=True)
class LogTail:
    """What ``tail_log()`` read (#122): the new text of a log since the last read.

    text:    complete lines, then the log's unfinished last line if it has one.
    cursor:  pass back to the next ``tail_log()`` call to get only what was written since.
    reset:   True when ``text`` replaces everything shown so far: a first read, or the log was
             truncated or replaced. Otherwise ``text`` continues the previous read.
    partial: the last line of ``text`` is unfinished; the next read sends it again, completed.
    """

    text: str
    cursor: tuple
    reset: bool
    partial: bool


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
               clone's pinned host key and the operator's key, like ``rhubarbtart ssh``.
    """

    src: str
    dst: str
    dst_ip: str
    ports: tuple[int, ...]
    argv: list[str]


@dataclass(frozen=True)
class ExecResult:
    """The result of ``exec()``: one command run in a clone over its pinned SSH (#85).

    exit_code:    the remote exit status (255: ssh itself failed; -1: timed out; 126: held for
                  approval, not run).
    stdout/stderr: the full output, as bytes.
    evidence_seq: the journal entry that recorded it, or ``None`` (ad-hoc clone, or held).
    approval_required: True when a tiered command was held for operator approval (#108); it did
                  not run. request_id is the approval to grant.
    """

    name: str
    command: str
    exit_code: int
    stdout: bytes
    stderr: bytes
    timed_out: bool
    evidence_seq: int | None
    approval_required: bool = False
    request_id: str | None = None


@dataclass(frozen=True)
class CollectResult:
    """``collect()`` for one clone: artifacts pulled from its ``~/evidence``.

    new:       paths recorded this time (new, or changed since the last collect).
    unchanged: paths already recorded with the same content.
    skipped:   entries refused, with why (not a regular file, unsafe path, too large).
    note:      why nothing was pulled (not running, no ``~/evidence``), else ``None``.
    ground_truth: the ground-truth snapshots taken (package ids), e.g. Juice Shop's challenges.
    """

    name: str
    new: list[str]
    unchanged: list[str]
    skipped: list[str]
    note: str | None = None
    ground_truth: tuple[str, ...] = ()
