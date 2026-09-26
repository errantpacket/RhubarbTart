"""``rm`` action — remove a managed clone and its per-clone secrets.

This is the **fully wired exemplar** for Stage C: the app dispatches it through a
confirmation modal, runs ``handle`` in a worker thread, and refreshes the panes on
return. Every other action module mirrors this shape (see the package docstring).

Destructive: the app confirms via ``confirm_prompt`` before calling ``handle``.
The core (``api.rm`` -> ``clones.load``) independently refuses anything that is not
a StrictModes-trusted managed clone, so images/precursors can never be removed even
if the UI guard were bypassed.
"""

from . import ActionContext, ActionOutcome

ID = "rm"
LABEL = "Remove clone"
DESTRUCTIVE = True
REQUIRES_CLONE = True


def confirm_prompt(ctx: ActionContext) -> str:
    """The confirmation text shown before removing the clone."""
    lineage = getattr(ctx.clone, "image", None)
    from_clause = f"\n(cloned from {lineage})" if lineage else ""
    return (
        f"Remove clone {ctx.name}?{from_clause}\n\n"
        "This stops and deletes the VM, forgets its pinned host key, and deletes its "
        "keychain password if it had a unique one. This cannot be undone."
    )


def handle(ctx: ActionContext) -> ActionOutcome:
    """Remove the clone via ``api.rm`` (off the UI thread), streaming progress.

    Returns an ``ActionOutcome``; catches the core's typed errors and reports them
    rather than raising, so a failed removal never crashes the app.
    """
    from rhubarb import api  # lazy: importing this package must not require tart

    ctx.progress(f"removing {ctx.name}…")
    try:
        res = api.rm(ctx.name)
    except api.VerifyError as e:
        return ActionOutcome(ok=False, summary=f"rm {ctx.name} failed: {e}")
    except FileNotFoundError as e:
        tool = getattr(e, "filename", None) or str(e)
        return ActionOutcome(ok=False, summary=f"rm {ctx.name} failed: {tool} not installed")

    kc = " (deleted its keychain password)" if res.keychain_deleted else ""
    ctx.progress(f"removed {res.name}{kc}")
    return ActionOutcome(ok=True, summary=f"removed {res.name} (was cloned from {res.image}){kc}")
