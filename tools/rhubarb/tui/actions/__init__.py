"""Operator **actions** for the RhubarbTart TUI (Phase 0.5, Stage C).

Stage B panes are strictly read-only. Stage C adds *write* operations, and this
package is where they live — one module per action, each a thin dispatcher over
the typed core API (``tools/rhubarb/api.py``). Actions NEVER touch ``tart`` or the
keychain directly, and NEVER re-implement the StrictModes/keychain/GUI-session
rules; those stay inside the core. The read-only panes stay read-only — actions
are dispatched by the app shell, not by the panes.

## The action-module contract (what every module in this package exposes)

Each module (``run`` / ``ssh`` / ``enroll`` / ``new`` / ``reset`` / ``rm`` /
``build``) exposes:

    ID: str            # stable action id, e.g. "rm"
    LABEL: str         # short human label for logs/menus, e.g. "Remove clone"
    DESTRUCTIVE: bool  # True => the app MUST confirm (ConfirmScreen) before running
    REQUIRES_CLONE: bool  # True => target must be an existing managed clone; the app
                          #         applies ``require_clone`` before dispatch
    def handle(ctx: ActionContext) -> ActionOutcome
        # Runs the action. Called by the app **off the UI thread** in a Textual
        # worker, so it may block on the core API (subprocess/SSH/etc.). It streams
        # milestones by calling ``ctx.progress(msg)`` (already marshalled onto the
        # UI thread by the app) and returns an ``ActionOutcome``. It imports the
        # core API lazily (``from rhubarb import api``) so importing this package
        # never requires ``tart`` present (headless test / off-Mac dev).

    # DESTRUCTIVE modules additionally expose:
    def confirm_prompt(ctx: ActionContext) -> str
        # The exact text shown in the confirmation modal.

``handle`` raises nothing for control flow: it catches the core's typed errors
(``api.VerifyError`` / ``FileNotFoundError``) and returns ``ActionOutcome(ok=False,
...)``. Any other exception propagates and the app's worker wrapper reports it.

## Dispatch pattern (implemented by the app shell, ``rhubarb/tui/dispatch.py``)

    1. Resolve the target (the highlighted clone in the read-only clones table).
    2. If ``REQUIRES_CLONE``: ``require_clone(name)`` — refuses non-clones
       (``rbt-*`` images, ``*-vanilla`` / ``*-unverified`` precursors).
    3. Build an ``ActionContext``.
    4. If ``DESTRUCTIVE``: push ``ConfirmScreen(module.confirm_prompt(ctx))``; run
       only on an explicit yes.
    5. Run ``module.handle(ctx)`` in a ``@work(thread=True)`` worker, streaming
       ``ctx.progress`` into the action log; on return, apply the ``ActionOutcome``
       (log summary, surface ``exec_argv``, refresh panes).

Every action module (``run``/``ssh``/``enroll``/``new``/``reset``/``rm``/``build``) is wired to the
typed core through the same ``handle(ctx)`` signature; ``rm`` was the original exemplar.
"""

from collections.abc import Callable
from dataclasses import dataclass, field

# Suffixes that mark a precursor image, never a managed clone.
_PRECURSOR_SUFFIXES = ("-vanilla", "-unverified")


class NotAClone(Exception):
    """A write action was aimed at something that is not a managed clone.

    Raised by :func:`require_clone` as a UI-side guard so the app refuses the
    target before ever calling the core. The core enforces the same rule
    authoritatively (``clones.load`` / StrictModes); this is defense in depth and
    a friendly message.
    """


def is_managed_clone(name: str) -> bool:
    """True only for a name shaped like a managed clone.

    Managed clones are what ``rhubarbtart new`` creates. This rejects the two things
    Stage C must never act on: built images (``rbt-PROFILE-SHA...``) and the
    ``*-vanilla`` / ``*-unverified`` precursors. It is a name-shape gate only; the
    core still verifies the clone's StrictModes record before doing anything.
    """
    if not name:
        return False
    if name.startswith("rbt-"):
        return False
    if name.endswith(_PRECURSOR_SUFFIXES):
        return False
    return True


def require_clone(name: str) -> str:
    """Return ``name`` if it is a managed-clone name, else raise :class:`NotAClone`."""
    if not is_managed_clone(name):
        raise NotAClone(
            f"{name!r} is not a managed clone — actions never touch built images "
            "(rbt-*) or vanilla/unverified precursors"
        )
    return name


@dataclass(frozen=True)
class ActionContext:
    """Everything an action ``handle`` needs, resolved by the app before dispatch.

    name:     the target clone's name (or, for ``new``, the clone to create).
    clone:    the ``api.Clone`` row for the target when acting on an existing clone
              (its lineage/state/etc. for prompts and messages); ``None`` for
              actions that do not act on an existing clone (``new``, ``build``).
    params:   action-specific options, e.g. ``{"service": "tailscale", "org": ...}``
              for ``enroll``, ``{"same_image": bool, "rotate": bool}`` for ``reset``,
              ``{"headless": bool, "detach": bool}`` for ``run``.
    progress: milestone sink — ``handle`` calls ``progress(msg)`` as each step
              completes. The app supplies one already marshalled onto the UI thread,
              and forwards it straight to the core's ``progress=`` parameter, so the
              live ``new``/``reset`` milestones (#19) flow to the action log.
    """

    name: str
    clone: object | None = None
    params: dict = field(default_factory=dict)
    progress: Callable[[str], None] = lambda _msg: None


@dataclass(frozen=True)
class ActionOutcome:
    """What an action ``handle`` returns to the app.

    ok:            whether the action succeeded.
    summary:       one-line closing status for the action log.
    needs_refresh: whether the app should refresh the read-only panes afterwards
                   (default True — most writes change what the panes show).
    exec_argv:     for ``run``/``ssh``, the ``argv`` the operator must exec *outside*
                   the TUI (a TUI cannot hand its terminal to an interactive VM/SSH
                   session); the app surfaces it instead of exec-ing. ``None`` otherwise.
    """

    ok: bool
    summary: str
    needs_refresh: bool = True
    exec_argv: list[str] | None = None


def registry() -> dict:
    """Map every action ``ID`` to its module (imported lazily).

    A convenience for the app / future menu wiring; importing the modules here does
    not require ``tart`` (they import the core API lazily inside ``handle``).
    """
    from . import build, enroll, new, reset, rm, run, ssh

    return {m.ID: m for m in (run, ssh, enroll, new, reset, rm, build)}


__all__ = [
    "ActionContext",
    "ActionOutcome",
    "NotAClone",
    "is_managed_clone",
    "require_clone",
    "registry",
]
