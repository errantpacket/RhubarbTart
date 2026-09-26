"""``build`` action — launch a profile build via ``scripts/build.sh PROFILE``.

Contract: see ``rhubarb/tui/actions/__init__.py`` and the ``rm`` exemplar.

Unlike the other actions, building is deliberately **not** part of the typed core API
(see docs/INTERFACE-PLAN.md, Stage A — the core owns clone/keychain/GUI-session logic,
not the Packer build). This action shells out to the audited ``scripts/build.sh`` and
streams its output, line by line, into the action log via ``ctx.progress``. It runs in
the app's worker (``@work(thread=True)``), so the long-blocking build never freezes the UI.

``REQUIRES_CLONE = False`` — the target is a profile id in ``ctx.params["profile"]``,
not an existing clone.

## The GUI-session caveat (why this action shouts before it runs)

A RhubarbTart build MUST be launched from a **GUI Terminal.app session**, never over SSH:
macOS Local Network Privacy blocks Packer/VM networking from SSH-launched processes, and
the build's keychain writes need the GUI login session. A build kicked off over SSH will
hang or fail confusingly after a long wait. So this handler:

  * refuses up front when it can positively detect an SSH session
    (``SSH_CONNECTION`` / ``SSH_TTY`` set), with an actionable message; and
  * streams :data:`GUI_SESSION_CAVEAT` as the first lines of the action log otherwise.

The shell should additionally surface :func:`confirm_prompt` in a (non-danger)
acknowledgement modal before dispatch — see this module's note to the app.
"""

import os
import subprocess

from . import ActionContext, ActionOutcome

ID = "build"
LABEL = "Build image"
DESTRUCTIVE = False
REQUIRES_CLONE = False
# Advisory (non-destructive) acknowledgement: the shell should confirm via ``confirm_prompt``
# before dispatch so the GUI-session caveat is seen. The core has no build guard to fall back
# on, so this is the operator's only warning.
REQUIRES_ACK = True

GUI_SESSION_CAVEAT = (
    "Builds MUST run from a GUI Terminal.app session, NOT over SSH.\n"
    "macOS Local Network Privacy blocks Packer/VM networking from SSH-launched "
    "processes, and the build's keychain writes need the GUI login session — a build "
    "started over SSH will hang or fail after a long wait."
)


def confirm_prompt(ctx: ActionContext) -> str:
    """The acknowledgement text the shell should show before launching a build."""
    profile = (ctx.params or {}).get("profile", "?")
    return (
        f"Build profile {profile}?\n\n"
        f"{GUI_SESSION_CAVEAT}\n\n"
        "This launches scripts/build.sh and can take a long time. Proceed?"
    )


def _in_ssh_session() -> bool:
    """True when this process was (positively) launched over SSH — a build will fail."""
    return bool(os.environ.get("SSH_CONNECTION") or os.environ.get("SSH_TTY"))


def handle(ctx: ActionContext) -> ActionOutcome:
    """Run ``scripts/build.sh <profile>`` off the UI thread, streaming its output.

    Reads the profile id from ``ctx.params["profile"]``, refuses an SSH session up front,
    and otherwise streams every build line into the action log via ``ctx.progress``.
    Returns an ``ActionOutcome``; catches missing-tool errors rather than raising so a
    failed launch never crashes the app (mirrors the ``rm`` exemplar).
    """
    from rhubarb import api  # lazy: importing this package must not require tart

    profile = (ctx.params or {}).get("profile")
    if not profile:
        return ActionOutcome(ok=False, needs_refresh=False,
                             summary="build: pick a profile first")

    # Friendly early validation; scripts/build.sh validates authoritatively too.
    try:
        known = api.list_profiles()
    except Exception:
        known = None
    if known is not None and profile not in known:
        return ActionOutcome(
            ok=False, needs_refresh=False,
            summary=f"build: unknown profile {profile!r} (have: {', '.join(sorted(known))})")

    # The caveat, always loud in the log. Refuse outright when we can see we are over SSH.
    for line in GUI_SESSION_CAVEAT.splitlines():
        ctx.progress(f"NOTE: {line}")
    if _in_ssh_session():
        return ActionOutcome(
            ok=False, needs_refresh=False,
            summary=f"build {profile} refused: this looks like an SSH session — relaunch the "
                    "TUI from a GUI Terminal.app session, then build")

    script = api.ROOT / "scripts" / "build.sh"
    ctx.progress(f"building {profile}: {script} {profile}")
    try:
        proc = subprocess.Popen(
            [str(script), profile], cwd=str(api.ROOT),
            stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, bufsize=1)
    except FileNotFoundError as e:
        tool = getattr(e, "filename", None) or str(e)
        return ActionOutcome(ok=False, needs_refresh=False,
                             summary=f"build {profile} failed: {tool} not found")

    assert proc.stdout is not None
    for raw in proc.stdout:
        ctx.progress(raw.rstrip("\n"))
    rc = proc.wait()

    if rc != 0:
        return ActionOutcome(ok=False, summary=f"build {profile} FAILED (exit {rc}) — see the log above")
    return ActionOutcome(ok=True, summary=f"build {profile} finished — a new image should now appear")
