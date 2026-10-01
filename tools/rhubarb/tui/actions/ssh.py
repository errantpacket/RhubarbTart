"""``ssh`` action — open an interactive SSH session to a managed clone.

Contract: see ``rhubarb/tui/actions/__init__.py`` and the ``rm`` exemplar.

``handle`` calls ``api.ssh_args(name)`` — which (via the core) resolves the clone's IP
(``tart ip``, bounded wait) and builds the pinned, no-forward, interactive ``ssh`` argv
used by ``rhubarbtart ssh``. It does **not** connect: a TUI cannot host an interactive shell
inside a pane, so ``handle`` returns the argv in ``ActionOutcome.exec_argv`` (with
``needs_refresh=False`` — SSHing changes nothing on the host) for the app to hand off,
mirroring how ``cli.cmd_ssh`` execs it.

Not destructive; ``api.ssh_args`` still refuses anything that is not a StrictModes-trusted
managed clone (and errors if the clone has no IP, i.e. it is not running).
"""

from . import ActionContext, ActionOutcome

ID = "ssh"
LABEL = "SSH to clone"
DESTRUCTIVE = False
REQUIRES_CLONE = True


def handle(ctx: ActionContext) -> ActionOutcome:
    """Resolve the pinned SSH argv via ``api.ssh_args`` (off the UI thread).

    Returns an ``ActionOutcome`` carrying ``exec_argv``; catches the core's typed errors
    and reports them rather than raising, so a failed resolve never crashes the app.
    """
    from rhubarb import api  # lazy: importing this package must not require tart

    ctx.progress(f"resolving SSH connection to {ctx.name}…")
    try:
        res = api.ssh_args(ctx.name)
    except api.VerifyError as e:
        return ActionOutcome(ok=False, summary=f"ssh {ctx.name} failed: {e}")
    except FileNotFoundError as e:
        tool = getattr(e, "filename", None) or str(e)
        return ActionOutcome(ok=False, summary=f"ssh {ctx.name} failed: {tool} not installed")

    ctx.progress(f"{res.name}: {res.username}@{res.ip} — ready")
    return ActionOutcome(ok=True,
                         summary=f"ssh to {res.name} as {res.username}@{res.ip}: run it outside the TUI",
                         needs_refresh=False, exec_argv=res.argv)
