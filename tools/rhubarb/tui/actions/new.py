"""``new`` action — create a fresh clone from a verified image.

Contract: see ``rhubarb/tui/actions/__init__.py`` and the ``rm`` exemplar.

This action does NOT target an existing clone (``REQUIRES_CLONE = False``):
``ctx.name`` is the *new* clone's name and ``ctx.params`` carries the source
(``profile`` or ``image``) and ``rotate``. The app prompts the operator for the
name + source before dispatch (a UI concern — ``handle`` runs off the UI thread and
cannot prompt) and passes them via ``dispatch_action(new, name=..., params=...)``.

``handle`` calls ``api.new(ctx.name, profile=..., image=..., rotate=...,
progress=ctx.progress)``. ``ctx.progress`` is forwarded straight into the core so
its #19 milestones — ``"cloned <image> -> <name>"``, ``"<name>: booting headless to
rotate its password"``, ``"<name>: unique password set ..."`` — stream live into the
action log while the (long) clone + headless rotation runs. This is the long,
live-feedback case; the closing ``NewResult.note`` (rotation skipped / kept the image
password) is folded into the returned summary, as ``cli._say_clone`` reports it.
"""

from . import ActionContext, ActionOutcome

ID = "new"
LABEL = "New clone"
DESTRUCTIVE = False
REQUIRES_CLONE = False


def handle(ctx: ActionContext) -> ActionOutcome:
    """Create the clone via ``api.new`` (off the UI thread), streaming progress.

    Returns an ``ActionOutcome``; catches the core's typed errors and reports them
    rather than raising, so a failed create never crashes the app. The core validates
    the name, the "exactly one of profile/image" rule and image availability and
    raises ``VerifyError`` on any violation, which is surfaced here — the UI does not
    duplicate those rules (they stay in the core).
    """
    from rhubarb import api  # lazy: importing this package must not require tart

    if not ctx.name:
        return ActionOutcome(ok=False, summary="new: a clone name is required",
                             needs_refresh=False)

    ctx.progress(f"creating {ctx.name}…")
    try:
        res = api.new(
            ctx.name,
            profile=ctx.params.get("profile"),
            image=ctx.params.get("image"),
            rotate=ctx.params.get("rotate", True),
            progress=ctx.progress,  # #19 milestones stream into the action log
        )
    except api.VerifyError as e:
        return ActionOutcome(ok=False, summary=f"new {ctx.name} failed: {e}", needs_refresh=False)
    except FileNotFoundError as e:
        tool = getattr(e, "filename", None) or str(e)
        return ActionOutcome(ok=False, summary=f"new {ctx.name} failed: {tool} not installed",
                             needs_refresh=False)

    note = f" — {res.note}" if res.note else ""
    return ActionOutcome(
        ok=True,
        summary=f"created {res.name} from {res.image} ({res.password_mode} password){note}; "
                f"ready: rhubarbtart run {res.name}",
    )
