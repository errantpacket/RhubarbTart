"""The RhubarbTart TUI application: layout, polling and provenance routing (#122).

``RhubarbTUI`` is a thin client of the typed core (``rhubarb.api``): its panes are read-only and
its operator actions are dispatched by :class:`rhubarb.tui.dispatch.ActionDispatch`, which it
mixes in. ``tools/rhubarb_tui.py`` is only the launcher (it carries the pinned Textual
dependency); import the app from here.
"""

import sys

from textual import work
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.widgets import DataTable, Footer, Header, RichLog, TabbedContent, TabPane

from .clones_pane import ClonesPane
from .dispatch import ActionDispatch
from .images_pane import ImagesPane
from .logs_pane import LogsPane
from .provenance_pane import ProvenancePane


class RhubarbTUI(ActionDispatch, App):
    """A tabbed, keyboard-driven view of images, clones, provenance and logs.

    The four panes are strictly read-only (Stage B). Operator *actions* (Stage C) are
    dispatched by :class:`ActionDispatch` — never by the panes — through the action modules in
    ``rhubarb.tui.actions``: each runs in a Textual worker off the UI thread, streams its progress
    into the action log, and destructive actions confirm first via ``ConfirmScreen``.
    """

    TITLE = "🍎 RhubarbTart"
    SUB_TITLE = "control plane"

    CSS = """
    #action-log {
        height: 8;
        border-top: solid $panel;
        padding: 0 1;
        background: $surface;
    }
    """

    # Polling (#120). Only the VISIBLE pane refreshes, and its blocking read runs in a worker
    # thread so a slow `tart list` never freezes the UI. The Logs tab tails every tick (plain
    # file reads); panes that run `tart list` poll every SLOW_EVERY ticks.
    TICK = 2.0
    SLOW_EVERY = 4


    BINDINGS = [
        Binding("q", "quit", "Quit"),
        Binding("r", "refresh_all", "Refresh"),
        Binding("question_mark", "toggle_help", "Keys", key_display="?"),
        # The tab labels show these number keys, so they stay out of the footer.
        Binding("1", "show_tab('images')", "Images tab", show=False),
        Binding("2", "show_tab('clones')", "Clones tab", show=False),
        Binding("3", "show_tab('provenance')", "Provenance tab", show=False),
        Binding("4", "show_tab('logs')", "Logs tab", show=False),
        # Actions on the highlighted clone: offered only on the Clones tab, so a key can never
        # act on a clone you can't see (see check_action). Destructive ones confirm first.
        Binding("b", "run_clone", "Run"),
        Binding("s", "ssh_clone", "SSH"),
        Binding("e", "enroll_clone", "Enroll"),
        Binding("x", "reset_clone", "Reset"),
        Binding("d", "delete_clone", "Remove"),
        # Profile-driven write actions, from any tab: they gather a profile/name via the
        # prompt modals (rhubarb.tui.prompt) before dispatch.
        Binding("n", "new_clone", "New clone"),
        Binding("B", "build", "Build"),
    ]

    # Actions that act on the highlighted clone, and every action that writes (one at a time).
    CLONE_ACTIONS = frozenset({"run_clone", "ssh_clone", "enroll_clone", "reset_clone",
                               "delete_clone"})
    WRITE_ACTIONS = CLONE_ACTIONS | {"new_clone", "build"}

    def __init__(self, *args, theme_choice=None, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        # A Textual theme name or Theme to use (see rhubarb.tui.theme.choose_theme); None keeps
        # Textual's default.
        self._theme_choice = theme_choice
        self._tick_n = 0
        self._polling = False          # a background refresh is in flight: skip the next one
        self._refreshing = False       # a full refresh (start-up, `r`, after an action) is running
        self._action_running = False   # an action worker is running: leave `tart` alone meanwhile
        self._action_label: str | None = None   # ... and which one, for the header
        # The row whose provenance to show when the Provenance tab is visible:
        # ("image"|"clone", name). Resolved lazily (a clone -> its source image).
        self._prov_source: tuple[str, str] | None = None

    def compose(self) -> ComposeResult:
        yield Header()
        with TabbedContent(initial="images"):
            # The number in each label is the key that opens the tab.
            with TabPane("1 Images", id="images"):
                yield ImagesPane(id="images-pane")
            with TabPane("2 Clones", id="clones"):
                yield ClonesPane(id="clones-pane")
            with TabPane("3 Provenance", id="provenance"):
                yield ProvenancePane(id="provenance-pane")
            with TabPane("4 Logs", id="logs"):
                yield LogsPane(id="logs-pane")
        yield RichLog(id="action-log", markup=False, wrap=True, highlight=False)
        yield Footer()

    def _panes(self):
        """The pane widgets, in tab order."""
        return (
            self.query_one("#images-pane", ImagesPane),
            self.query_one("#clones-pane", ClonesPane),
            self.query_one("#provenance-pane", ProvenancePane),
            self.query_one("#logs-pane", LogsPane),
        )

    def on_mount(self) -> None:
        self._apply_theme()
        self.sub_title = "control plane · loading…"
        self._log_action("Ready. Press ? for every key. Action output appears here; a build's "
                         "full output is also in the Logs tab (4).")
        self.action_refresh_all()
        self.set_interval(self.TICK, self._tick)

    def _apply_theme(self) -> None:
        """Use the chosen theme (herdr's, or --theme / RHUBARB_TUI_THEME). A bad choice is
        reported in the action log and the default theme stays; it never stops the TUI."""
        from textual.theme import Theme

        choice = self._theme_choice
        try:
            if isinstance(choice, Theme):
                self.register_theme(choice)
                self.theme = choice.name
            elif choice:
                if choice not in self.available_themes:
                    raise ValueError(f"unknown theme {choice!r}; try one of: "
                                     + ", ".join(sorted(self.available_themes)))
                self.theme = choice
        except Exception as e:
            self._log_action(f"theme not applied: {e}", ok=False)

    def action_refresh_all(self) -> None:
        """Reload every pane from the core API (read-only) off the UI thread. Used at start-up,
        on `r` and after an action; each pane fills in as soon as its own read returns."""
        if self._refreshing:
            return
        self._refreshing = True
        self._refresh_all(self._panes())

    @work(thread=True, group="rhubarb-refresh")
    def _refresh_all(self, panes) -> None:
        """Fetch each pane in turn (blocking reads, e.g. `tart list`) and render it right away."""
        try:
            for pane in panes:
                try:
                    data = pane.fetch()
                except Exception as e:  # fetch() doesn't raise; a refresh must never kill the app
                    data = {"error": f"refresh failed: {e}"}
                self.call_from_thread(pane.render_data, data)
            self.call_from_thread(self._stamp)
        except Exception:
            pass  # the app is shutting down
        finally:
            self._refreshing = False

    def _stamp(self) -> None:
        """Show when the data on screen was last read, and any action still running."""
        import time

        running = f" · running: {self._action_label}…" if self._action_label else ""
        self.sub_title = f"control plane · updated {time.strftime('%H:%M:%S')}{running}"

    def check_action(self, action: str, parameters: tuple) -> bool | None:
        """Offer only the keys that make sense now (the footer follows this).

        Clone actions exist only on the Clones tab (hidden elsewhere), and while an action is
        running every write action is shown disabled, so actions never overlap.
        """
        if action in self.CLONE_ACTIONS and self._active_tab() != "clones":
            return False
        if action in self.WRITE_ACTIONS and self._action_running:
            return None
        return True

    def action_toggle_help(self) -> None:
        """Show or hide the panel listing every key for the focused widget and the app."""
        from textual.widgets import HelpPanel

        if self.screen.query(HelpPanel):
            self.action_hide_help_panel()
        else:
            self.action_show_help_panel()

    def _tick(self) -> None:
        """The timer: tail the Logs tab every tick; poll `tart`-backed panes every SLOW_EVERY."""
        self._tick_n += 1
        if self._active_pane_id() == "#logs-pane" or self._tick_n % self.SLOW_EVERY == 0:
            self._refresh_active()

    _PANE_IDS = {"images": "#images-pane", "clones": "#clones-pane",
                 "provenance": "#provenance-pane", "logs": "#logs-pane"}
    # What gets the keyboard when a tab opens, so arrows and Enter work straight away.
    _FOCUS_IDS = {"images": "#images-table", "clones": "#clones-table",
                  "provenance": "#provenance-pane", "logs": "#logs-table"}

    def _active_tab(self) -> str | None:
        try:
            return self.query_one(TabbedContent).active
        except Exception:
            return None

    def _active_pane_id(self) -> str | None:
        return self._PANE_IDS.get(self._active_tab())

    def _refresh_active(self) -> None:
        """Reload just the visible pane, its blocking read in a worker (timer and tab switch)."""
        pane_id = self._active_pane_id()
        if not pane_id or self._polling or self._refreshing:
            return
        # While an action runs (a build, a reset), leave `tart list` alone; logs are plain file
        # reads, so keep tailing them, e.g. to watch that build.
        if self._action_running and pane_id != "#logs-pane":
            return
        try:
            pane = self.query_one(pane_id)
        except Exception:
            return
        if pane_id == "#provenance-pane":
            vm, via = self._provenance_vm()
            if vm:
                pane.select(vm, via)   # follow the last highlighted image/clone (no read here)
        self._polling = True
        if pane_id != "#logs-pane":
            self.sub_title = "control plane · refreshing…"
        self._poll(pane)

    @work(thread=True, group="rhubarb-poll")
    def _poll(self, pane) -> None:
        """Run a pane's blocking ``fetch()`` off the UI thread, then render on it."""
        try:
            data = pane.fetch()
        except Exception as e:  # fetch() doesn't raise, but a poll must never kill the app
            data = {"error": f"refresh failed: {e}"}
        try:
            self.call_from_thread(self._finish_poll, pane, data)
        except Exception:
            self._polling = False  # the app is shutting down

    def _finish_poll(self, pane, data: dict) -> None:
        self._polling = False
        try:
            pane.render_data(data)
        finally:
            self._stamp()

    def action_show_tab(self, tab: str) -> None:
        self.query_one(TabbedContent).active = tab

    def on_data_table_row_highlighted(self, event: DataTable.RowHighlighted) -> None:
        """Remember the highlighted row's provenance source; only *read* it when the Provenance
        tab is visible — so navigating images/clones never pays for the file read or the
        clone->image lookup. Provenance exists for built images; a clone shows its source image.
        """
        table_id = event.data_table.id
        if table_id == "images-table":
            try:
                self._prov_source = ("image", str(event.data_table.get_row(event.row_key)[0]))
            except Exception:
                return
        elif table_id == "clones-table":
            self._prov_source = ("clone", str(event.row_key.value))
        else:
            return
        if self._active_pane_id() == "#provenance-pane":
            self._update_provenance()

    def _provenance_vm(self) -> tuple[str | None, str | None]:
        """``(vm, via)``: the VM whose provenance to show — the highlighted image, or a clone's
        source image (resolved from the Clones pane's cache, so no `tart` call) — and the clone
        it was reached from, if any."""
        if not self._prov_source:
            return None, None
        kind, ref = self._prov_source
        if kind == "clone":
            clone = self._lookup_clone(ref)
            return (clone.image, ref) if clone is not None else (None, None)
        return ref, None

    def _update_provenance(self) -> None:
        """Show the highlighted row's provenance now (one small file read)."""
        vm, via = self._provenance_vm()
        if vm:
            try:
                self.query_one("#provenance-pane", ProvenancePane).show(vm, via)
            except Exception:
                pass

    def on_data_table_row_selected(self, event: DataTable.RowSelected) -> None:
        """Enter on an image or a clone opens its provenance (a clone shows its image's)."""
        if event.data_table.id in ("images-table", "clones-table"):
            self.action_show_tab("provenance")

    def on_tabbed_content_tab_activated(self, event: TabbedContent.TabActivated) -> None:
        """Refresh the pane that just became visible (so switching tabs shows fresh data at
        once), hand it the keyboard, and update the footer to that tab's keys."""
        try:
            self.query_one(self._FOCUS_IDS[event.pane.id]).focus()
        except Exception:
            pass
        self.refresh_bindings()
        self._refresh_active()


def mouse_enabled(argv: list[str], env) -> bool:
    """Whether the TUI should capture the mouse.

    The TUI is fully keyboard-driven. Inside herdr (``HERDR_PANE_ID`` set) mouse capture is
    off by default so herdr keeps its own mouse handling (pane focus, scrolling, selection).
    ``--mouse`` / ``--no-mouse`` or ``RHUBARB_TUI_MOUSE=1|0`` override that.
    """
    if "--no-mouse" in argv:
        return False
    if "--mouse" in argv:
        return True
    value = env.get("RHUBARB_TUI_MOUSE")
    if value is not None:
        return value.strip().lower() not in ("0", "false", "no", "off")
    return not env.get("HERDR_PANE_ID")


def main() -> None:
    import os

    from .theme import choose_theme

    argv = sys.argv[1:]
    try:
        theme = choose_theme(argv, os.environ)
    except Exception:   # a theme problem must never stop the TUI from starting
        theme = None
    RhubarbTUI(theme_choice=theme).run(mouse=mouse_enabled(argv, os.environ))
