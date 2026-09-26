"""``run`` action — start a managed clone.

Contract: see ``rhubarb/tui/actions/__init__.py`` and the ``rm`` exemplar.

Two modes, both through the typed core (``api.run`` — never ``tart`` directly):

* **detached** (``params["detach"]``): the core starts the VM in the background with
  its own session and a per-clone log; ``handle`` reports the log path and asks for a
  pane refresh (the clone's state changes to running).
* **foreground** (default): a TUI cannot hand its terminal to a graphical/console VM,
  so the core returns the ``["tart", "run", ...]`` argv and ``handle`` passes it back
  in ``ActionOutcome.exec_argv`` for the operator to exec outside the TUI — mirroring
  how ``cli.cmd_run`` execs it. Nothing has started yet, so no refresh is requested.

Not destructive; ``api.run`` still refuses anything that is not a StrictModes-trusted
managed clone (and errors if the clone is already running).
"""

from . import ActionContext, ActionOutcome

ID = "run"
LABEL = "Run clone"
DESTRUCTIVE = False
REQUIRES_CLONE = True


def handle(ctx: ActionContext) -> ActionOutcome:
    """Start the clone via ``api.run`` (off the UI thread), streaming progress.

    Returns an ``ActionOutcome``; catches the core's typed errors and reports them
    rather than raising, so a failed start never crashes the app.
    """
    from rhubarb import api  # lazy: importing this package must not require tart

    headless = bool(ctx.params.get("headless", False))
    detach = bool(ctx.params.get("detach", False))

    mode = "headless" if headless else "graphical"
    where = "in the background" if detach else "in the foreground"
    ctx.progress(f"starting {ctx.name} ({mode}) {where}…")
    try:
        res = api.run(ctx.name, headless=headless, detach=detach)
    except api.VerifyError as e:
        return ActionOutcome(ok=False, summary=f"run {ctx.name} failed: {e}")
    except FileNotFoundError as e:
        tool = getattr(e, "filename", None) or str(e)
        return ActionOutcome(ok=False, summary=f"run {ctx.name} failed: {tool} not installed")

    if res.detached:
        ctx.progress(f"{res.name}: started in the background (log: {res.log_path})")
        return ActionOutcome(ok=True,
                             summary=f"started {res.name} in the background (log: {res.log_path})")

    # Foreground: the core handed back the argv; the TUI cannot host the VM session, so
    # surface it for the operator to exec outside the TUI. Nothing has started -> no refresh.
    return ActionOutcome(ok=True,
                         summary=f"{res.name}: run it outside the TUI (a TUI cannot host the VM window)",
                         needs_refresh=False, exec_argv=res.argv)
