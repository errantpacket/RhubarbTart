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

    TITLE = "RhubarbTart"
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
        Binding("1", "show_tab('images')", "Images"),
        Binding("2", "show_tab('clones')", "Clones"),
        Binding("3", "show_tab('provenance')", "Provenance"),
        Binding("4", "show_tab('logs')", "Logs"),
        # Actions on the highlighted clone (dispatched by the shell, run in a worker;
        # destructive ones confirm first).
        Binding("b", "run_clone", "Run"),
        Binding("s", "ssh_clone", "SSH"),
        Binding("x", "reset_clone", "Reset"),
        Binding("d", "delete_clone", "Remove clone"),
        # Input-driven write actions: they gather a profile/service/name via the
        # prompt modals (rhubarb.tui.prompt) before dispatch. new/build target a
        # profile (not a clone); enroll acts on the highlighted clone.
        Binding("n", "new_clone", "New"),
        Binding("e", "enroll_clone", "Enroll"),
        Binding("B", "build", "Build"),
        # left/right arrows and tab already move between panes via TabbedContent.
    ]

    def __init__(self, *args, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        self._tick_n = 0
        self._polling = False          # a background refresh is in flight: skip the next one
        self._refreshing = False       # a full refresh (start-up, `r`, after an action) is running
        self._action_running = False   # an action worker is running: leave `tart` alone meanwhile
        # The row whose provenance to show when the Provenance tab is visible:
        # ("image"|"clone", name). Resolved lazily (a clone -> its source image).
        self._prov_source: tuple[str, str] | None = None

    def compose(self) -> ComposeResult:
        yield Header()
        with TabbedContent(initial="images"):
            with TabPane("Images", id="images"):
                yield ImagesPane(id="images-pane")
            with TabPane("Clones", id="clones"):
                yield ClonesPane(id="clones-pane")
            with TabPane("Provenance", id="provenance"):
                yield ProvenancePane(id="provenance-pane")
            with TabPane("Logs", id="logs"):
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
        self.sub_title = "control plane · loading…"
        self.action_refresh_all()
        self.set_interval(self.TICK, self._tick)

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
        """Show when the data on screen was last read."""
        import time

        self.sub_title = f"control plane · updated {time.strftime('%H:%M:%S')}"

    def _tick(self) -> None:
        """The timer: tail the Logs tab every tick; poll `tart`-backed panes every SLOW_EVERY."""
        self._tick_n += 1
        if self._active_pane_id() == "#logs-pane" or self._tick_n % self.SLOW_EVERY == 0:
            self._refresh_active()

    _PANE_IDS = {"images": "#images-pane", "clones": "#clones-pane",
                 "provenance": "#provenance-pane", "logs": "#logs-pane"}

    def _active_pane_id(self) -> str | None:
        try:
            return self._PANE_IDS.get(self.query_one(TabbedContent).active)
        except Exception:
            return None

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
            vm = self._provenance_vm()
            if vm:
                pane.select(vm)   # follow the last highlighted image/clone (no read here)
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

    def _provenance_vm(self) -> str | None:
        """The VM whose provenance to show: the highlighted image, or a clone's source image
        (resolved from the Clones pane's cache, so no `tart` call)."""
        if not self._prov_source:
            return None
        kind, ref = self._prov_source
        if kind == "clone":
            clone = self._lookup_clone(ref)
            return clone.image if clone is not None else None
        return ref

    def _update_provenance(self) -> None:
        """Show the highlighted row's provenance now (one small file read)."""
        vm = self._provenance_vm()
        if vm:
            try:
                self.query_one("#provenance-pane", ProvenancePane).show(vm)
            except Exception:
                pass

    def on_tabbed_content_tab_activated(self, event: TabbedContent.TabActivated) -> None:
        """Refresh the pane that just became visible (so switching tabs shows fresh data at once)."""
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

    RhubarbTUI().run(mouse=mouse_enabled(sys.argv[1:], os.environ))
