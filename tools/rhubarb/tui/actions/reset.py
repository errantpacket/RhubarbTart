"""``reset`` action — destroy a clone and re-clone it clean (destructive).

Contract: see ``rhubarb/tui/actions/__init__.py`` and the ``rm`` exemplar.

Destructive: the app confirms via ``confirm_prompt`` before ``handle`` runs, and
``require_clone`` has already refused any non-clone / ``rbt-*`` / ``*-vanilla`` target
(the core enforces the same rules authoritatively).

``handle`` calls ``api.reset(ctx.name, same_image=..., rotate=...,
progress=ctx.progress)`` with options from ``ctx.params``. ``ctx.progress`` is
forwarded straight into the core so the teardown → re-clone → rotate milestones (#19)
— including ``"<name>: destroyed ...; re-cloning from <image>"`` — stream live into
the action log. The closing ``NewResult.note`` (rotation skipped / kept the image
password) is folded into the returned summary, as ``cli._say_clone`` reports it.
"""

from . import ActionContext, ActionOutcome

ID = "reset"
LABEL = "Reset clone"
DESTRUCTIVE = True
REQUIRES_CLONE = True


def confirm_prompt(ctx: ActionContext) -> str:
    same_image = ctx.params.get("same_image", False)
    lineage = getattr(ctx.clone, "image", None)
    if same_image and lineage:
        source = f"\n(re-clones from its current image {lineage})"
    elif same_image:
        source = "\n(re-clones from its current lineage image)"
    else:
        source = "\n(re-clones from the profile's current image)"
    return (
        f"Reset clone {ctx.name}?{source}\n\n"
        "This destroys the clone (its enrollment and identity are lost) and re-clones "
        "it from a clean image. This cannot be undone."
    )


def handle(ctx: ActionContext) -> ActionOutcome:
    """Reset the clone via ``api.reset`` (off the UI thread), streaming progress.

    Returns an ``ActionOutcome``; catches the core's typed errors and reports them
    rather than raising, so a failed reset never crashes the app. The core tears the
    clone down, deletes its record + per-clone secret, then re-clones and rotates;
    it raises ``VerifyError`` for an unknown/untrusted clone or a target image not
    built here, which is surfaced here.
    """
    from rhubarb import api  # lazy: importing this package must not require tart

    ctx.progress(f"resetting {ctx.name}…")
    try:
        res = api.reset(
            ctx.name,
            same_image=ctx.params.get("same_image", False),
            rotate=ctx.params.get("rotate", True),
            progress=ctx.progress,  # #19 milestones (incl. teardown) stream into the log
        )
    except api.VerifyError as e:
        return ActionOutcome(ok=False, summary=f"reset {ctx.name} failed: {e}")
    except FileNotFoundError as e:
        tool = getattr(e, "filename", None) or str(e)
        return ActionOutcome(ok=False, summary=f"reset {ctx.name} failed: {tool} not installed")

    note = f" — {res.note}" if res.note else ""
    return ActionOutcome(
        ok=True,
        summary=f"reset {res.name}: re-cloned from {res.image} ({res.password_mode} password){note}",
    )
