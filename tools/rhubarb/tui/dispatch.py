"""Operator-action dispatch for the RhubarbTart TUI (Stage C), as an ``App`` mixin (#122).

The shell owns action dispatch so the read-only panes stay read-only. The flow: resolve the
target, guard it, confirm if destructive, run the action module's ``handle`` in a worker while
streaming its progress into the action log, then apply the outcome (log, hand the terminal to
an interactive command, refresh). Input-driven actions (``new``/``enroll``/``build``) first
gather a profile, service or name through the prompt modals.

Mixed into :class:`rhubarb.tui.app.RhubarbTUI`; it relies on the app's ``query_one``,
``push_screen``, ``call_from_thread``, ``suspend`` and ``action_refresh_all``.
"""

from rich.text import Text
from textual import work
from textual.widgets import DataTable, RichLog

from . import actions
from .confirm import ConfirmScreen
from .prompt import InputScreen, SelectScreen


class ActionDispatch:
    """Action bindings and their dispatch machinery for the TUI app."""

    # -- action infrastructure (Stage C) --------------------------------------
    # The shell owns action dispatch so the read-only panes stay read-only. The
    # flow: resolve target -> guard -> (confirm if destructive) -> run in a worker,
    # streaming progress into the action log -> apply the outcome (log + refresh).

    def action_delete_clone(self) -> None:
        """Remove the highlighted clone (with confirm)."""
        from rhubarb.tui.actions import rm

        self.dispatch_action(rm)

    def action_run_clone(self) -> None:
        """Start the highlighted clone (GUI window, in the background)."""
        from rhubarb.tui.actions import run

        self.dispatch_action(run, params={"headless": False, "detach": True})

    def action_ssh_clone(self) -> None:
        """SSH into the highlighted clone: the core returns argv, the shell suspends and execs it."""
        from rhubarb.tui.actions import ssh

        self.dispatch_action(ssh)

    def action_reset_clone(self) -> None:
        """Destroy + re-clone the highlighted clone from the current image (with confirm)."""
        from rhubarb.tui.actions import reset

        self.dispatch_action(reset, params={"same_image": False, "rotate": True})

    # -- input-driven write actions (profile/service/name gathered via prompt modals) ----
    # These need operator input before dispatch, so they chain the prompt modals via
    # push_screen callbacks (like ConfirmScreen). Cancelling any modal (Escape) aborts
    # cleanly: log a line, make no api call. The action handlers still validate in the core.

    def _profile_choices(self) -> list[str] | None:
        """The known profile ids for a picker; None (with a log line) when unavailable."""
        try:
            from rhubarb import api

            return sorted(api.list_profiles())
        except Exception as e:  # a missing/broken profiles dir must not crash the app
            self._log_action(f"could not list profiles: {e}", ok=False)
            return None

    def action_new_clone(self) -> None:
        """Create a fresh clone: pick a profile, type a name, then dispatch ``new``."""
        from rhubarb.tui.actions import new

        profiles = self._profile_choices()
        if not profiles:
            if profiles is not None:
                self._log_action(f"{new.LABEL}: no profiles found to build a clone from")
            return

        def got_profile(profile: str | None) -> None:
            if not profile:
                self._log_action(f"{new.LABEL}: cancelled")
                return

            def got_name(name: str | None) -> None:
                if not name:
                    self._log_action(f"{new.LABEL}: cancelled")
                    return
                self.dispatch_action(new, name=name,
                                     params={"profile": profile, "rotate": True})

            self.push_screen(InputScreen(f"New clone name (profile {profile})"), got_name)

        self.push_screen(SelectScreen("Select a profile for the new clone", profiles),
                         got_profile)

    def action_enroll_clone(self) -> None:
        """Enroll the highlighted clone: pick a service (and, for warp, an optional org)."""
        from rhubarb.tui.actions import enroll

        name = self._selected_clone_name()
        if not name:
            self._log_action(f"{enroll.LABEL}: select a clone in the Clones tab first")
            return

        def got_service(service: str | None) -> None:
            if not service:
                self._log_action(f"{enroll.LABEL}: cancelled")
                return
            if service == "warp":
                def got_org(org: str | None) -> None:
                    if org is None:  # Escape aborts
                        self._log_action(f"{enroll.LABEL}: cancelled")
                        return
                    if not org.strip():  # WARP requires a team/org (enroll.sh: "warp needs --org")
                        self._log_action(f"{enroll.LABEL}: WARP needs a team/org name — cancelled",
                                         ok=False)
                        return
                    self.dispatch_action(enroll, name=name,
                                         params={"service": service, "org": org.strip()})

                self.push_screen(
                    InputScreen("Cloudflare WARP team/org (required)"), got_org)
            else:
                self.dispatch_action(enroll, name=name,
                                     params={"service": service, "org": None})

        self.push_screen(
            SelectScreen(f"Enroll {name} in which service?", list(enroll.SERVICES)),
            got_service)

    def action_build(self) -> None:
        """Build a profile image: pick a profile, then dispatch ``build``.

        The build handler streams the GUI-session caveat and refuses over SSH itself,
        so the shell does not duplicate that here.
        """
        from rhubarb.tui.actions import build

        profiles = self._profile_choices()
        if not profiles:
            if profiles is not None:
                self._log_action(f"{build.LABEL}: no profiles found to build")
            return

        def got_profile(profile: str | None) -> None:
            if not profile:
                self._log_action(f"{build.LABEL}: cancelled")
                return
            self.dispatch_action(build, params={"profile": profile})

        self.push_screen(SelectScreen("Build which profile?", profiles), got_profile)

    def dispatch_action(self, module, *, name: str | None = None,
                        clone: object | None = None, params: dict | None = None) -> None:
        """Run an action module for a target, confirming first if it is destructive.

        ``name``/``clone`` default to the highlighted clone in the read-only clones
        table. Applies the ``require_clone`` guard for clone-targeted actions so a
        built image (``rbt-*``) or a ``*-vanilla``/``*-unverified`` precursor is never
        a target — the core enforces this too, this is the friendly UI-side gate.
        """
        if module.REQUIRES_CLONE:
            if name is None:
                name = self._selected_clone_name()
            if not name:
                self._log_action(f"{module.LABEL}: select a clone in the Clones tab first")
                return
            try:
                actions.require_clone(name)
            except actions.NotAClone as e:
                self._log_action(f"refused: {e}", ok=False)
                return
            if clone is None:
                clone = self._lookup_clone(name)

        ctx = actions.ActionContext(name=name or "", clone=clone,
                                    params=params or {}, progress=self._action_progress)

        # DESTRUCTIVE actions (reset/rm) confirm before acting; REQUIRES_ACK actions (build)
        # aren't destructive but must surface an acknowledgement first (the GUI-session caveat).
        if module.DESTRUCTIVE or getattr(module, "REQUIRES_ACK", False):
            def on_confirm(confirmed: bool | None) -> None:
                if confirmed:
                    self._run_action(module, ctx)
                else:
                    self._log_action(f"{module.LABEL}: cancelled")

            self.push_screen(ConfirmScreen(module.confirm_prompt(ctx),
                                           action_label=module.LABEL), on_confirm)
        else:
            self._run_action(module, ctx)

    @work(thread=True, group="rhubarb-action")
    def _run_action(self, module, ctx: "actions.ActionContext") -> None:
        """Run ``module.handle(ctx)`` off the UI thread; report the outcome.

        Runs in a Textual thread worker so the (blocking) core API never freezes the
        UI. All UI updates are marshalled back with ``call_from_thread``. While it runs, the
        poller leaves `tart`-backed panes alone (see ``_refresh_active``).
        """
        self._action_running = True
        try:
            try:
                outcome = module.handle(ctx)
            except NotImplementedError as e:
                self.call_from_thread(self._log_action, f"{module.LABEL}: {e}", False)
                return
            except Exception as e:  # a handler bug must not take the app down
                self.call_from_thread(self._log_action,
                                      f"{module.LABEL} FAILED: {type(e).__name__}: {e}", False)
                return
        finally:
            self._action_running = False
        self.call_from_thread(self._apply_outcome, outcome)

    def _apply_outcome(self, outcome: "actions.ActionOutcome") -> None:
        """Apply an action's result on the UI thread: log it; hand off an interactive
        session if the core returned one; then refresh."""
        self._log_action(outcome.summary, ok=outcome.ok)
        if outcome.exec_argv:
            self._exec_interactive(outcome.exec_argv)
        elif outcome.needs_refresh:
            self.action_refresh_all()

    def _exec_interactive(self, argv: list[str]) -> None:
        """Hand the terminal to an interactive command the core returned as argv (ssh, or a
        foreground run) — the TUI can't host it — then resume the app and refresh. argv is built
        by the core (api.ssh_args / api.run), never from user text."""
        import subprocess

        self._log_action("handing terminal over: " + " ".join(argv))
        with self.suspend():
            subprocess.run(argv)  # noqa: S603
        self.action_refresh_all()

    def _action_progress(self, msg: str) -> None:
        """Progress sink handed to action handlers (and forwarded to the core).

        Called from the worker thread, so it marshals the line onto the UI thread.
        """
        self.call_from_thread(self._log_action, msg)

    def _log_action(self, msg: str, ok: bool | None = None) -> None:
        """Write one line to the action log (UI thread). ``ok`` colours the line."""
        style = "" if ok is None else ("green" if ok else "red bold")
        try:
            self.query_one("#action-log", RichLog).write(Text(msg, style=style))
        except Exception:
            pass

    def _selected_clone_name(self) -> str | None:
        """Read the highlighted clone's name from the read-only clones table.

        Read-only: it only inspects the DataTable cursor; it never mutates the pane.
        """
        try:
            table = self.query_one("#clones-table", DataTable)
            if not table.row_count or not table.is_valid_coordinate(table.cursor_coordinate):
                return None
            return table.coordinate_to_cell_key(table.cursor_coordinate).row_key.value
        except Exception:
            return None

    def _lookup_clone(self, name: str):
        """Find the ``api.Clone`` row for ``name`` (for prompts/messages); best-effort."""
        try:
            from rhubarb import api

            for c in api.clones().clones:
                if c.name == name:
                    return c
        except Exception:
            pass
        return None
