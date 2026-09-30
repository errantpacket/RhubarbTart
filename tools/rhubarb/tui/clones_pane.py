"""Clones pane — read-only view of ``api.clones()``.

Renders one row per ``api.Clone`` (name, profile, live state, freshness, password
mode, enrollments) in a ``DataTable``, and surfaces ``CloneList.problems`` (the
``IGNORED ...`` records that failed the StrictModes trust rules) below it.

Selecting a clone row points the provenance pane at that clone's VM (via
``ProvenancePane.show``) and switches to the Provenance tab.

Strictly **read-only**: the only core call is ``rhubarb.api.clones()`` — never
``new``/``run``/``enroll``/``reset``/``rm``, and never ``tart`` or the keychain
directly. The API is imported lazily inside ``refresh_data`` so that importing
this package never requires ``tart`` to be installed (headless test / Linux dev).
"""

from contextlib import nullcontext

from rich.text import Text
from textual.app import ComposeResult
from textual.containers import VerticalScroll
from textual.widgets import DataTable, Static, TabbedContent

# Column headers, in order. "FRESHNESS" is the image-staleness verdict
# (current / outdated / image-deleted); "PASSWORD" is the keychain mode
# (unique / inherited).
_COLUMNS = ("CLONE", "PROFILE", "STATE", "FRESHNESS", "PASSWORD", "ENROLLED")

# Colour cues for at-a-glance status. Anything not listed renders plain.
_STATE_STYLE = {"running": "green", "MISSING": "red bold"}
_FRESH_STYLE = {"current": "green", "outdated": "yellow", "image-deleted": "red bold",
                "base-missing": "red bold"}
_PW_STYLE = {"unique": "green", "inherited": "yellow"}


class ClonesPane(VerticalScroll):
    """Every research clone: live state, staleness, password mode, enrollments."""

    _sig = None   # last rendered rows, to skip no-op rebuilds

    BORDER_TITLE = "Clones"

    def compose(self) -> ComposeResult:
        # A one-line status/empty/error banner above the table.
        yield Static("", id="clones-status")
        table: DataTable = DataTable(id="clones-table", zebra_stripes=True)
        table.cursor_type = "row"
        yield table
        # Rejected records (CloneList.problems); hidden while empty.
        yield Static("", id="clones-problems")

    def on_mount(self) -> None:
        table = self.query_one("#clones-table", DataTable)
        if not table.columns:
            table.add_columns(*_COLUMNS)

    def refresh_data(self) -> None:
        """(Re)load from ``rhubarb.api.clones()`` and re-render. Read-only.

        Handles the empty case and the typed errors from the core API
        (``VerifyError`` for a failed host op, ``FileNotFoundError`` when a
        required host tool such as ``tart`` is not installed) by showing the
        message rather than letting the app crash.
        """
        self.render_data(self.fetch())

    def fetch(self) -> dict:
        """The blocking read (it runs ``tart list``), safe off the UI thread (#120).

        Returns ``{"result": CloneList}`` or ``{"error": message}``; never raises.
        """
        from rhubarb import api  # lazy: package imports without tart present

        try:
            return {"result": api.clones()}
        except FileNotFoundError as e:
            tool = getattr(e, "filename", None) or str(e)
            return {"error": f"{tool}: required host tool not installed (run this on the build Mac)."}
        except api.VerifyError as e:
            return {"error": f"cannot read clones: {e}"}
        except Exception as e:  # never let a pane refresh take down the app
            return {"error": f"unexpected error reading clones: {e}"}

    def render_data(self, data: dict) -> None:
        """Apply a ``fetch()`` result (UI thread)."""
        if not self.is_mounted:
            return
        status = self.query_one("#clones-status", Static)
        table = self.query_one("#clones-table", DataTable)
        problems = self.query_one("#clones-problems", Static)
        if "error" in data:
            table.display = False
            problems.display = False
            status.update(Text(data["error"], style="red bold"))
            return
        result = data["result"]
        self._render_clones(status, table, result.clones)
        self._render_problems(problems, result.problems)

    # -- rendering helpers ----------------------------------------------------

    def _render_clones(self, status: Static, table: DataTable, clones: list) -> None:
        # Preserve the highlighted clone across refreshes where we can.
        prev_key = None
        try:
            if table.row_count and table.is_valid_coordinate(table.cursor_coordinate):
                prev_key = table.coordinate_to_cell_key(table.cursor_coordinate).row_key.value
        except Exception:
            prev_key = None

        if not clones:
            table.clear()  # keeps columns
            self._sig = None
            table.display = False
            status.update(Text("no clones yet (rhubarb new NAME --profile P).", style="dim"))
            return

        table.display = True
        status.update(Text(f"{len(clones)} clone(s).", style="dim"))
        sig = [(c.name, c.profile, c.state, c.freshness, c.password_mode, tuple(c.enrollments))
               for c in clones]
        if sig == self._sig:
            return   # nothing changed: leave the table (and the cursor) alone
        self._sig = sig
        with table.prevent(DataTable.RowHighlighted) if prev_key else nullcontext():
            self._fill(table, clones, prev_key)

    def _fill(self, table: DataTable, clones: list, prev_key) -> None:
        table.clear()  # keeps columns
        for c in clones:
            enrolled = ", ".join(c.enrollments) if c.enrollments else "-"
            table.add_row(
                c.name,
                c.profile,
                Text(c.state, style=_STATE_STYLE.get(c.state, "")),
                Text(c.freshness, style=_FRESH_STYLE.get(c.freshness, "")),
                Text(c.password_mode, style=_PW_STYLE.get(c.password_mode, "")),
                enrolled,
                key=c.name,
            )

        if prev_key is not None:
            try:
                table.move_cursor(row=table.get_row_index(prev_key), animate=False)
            except Exception:
                pass

    def _render_problems(self, problems: Static, records: list) -> None:
        if not records:
            problems.display = False
            problems.update("")
            return
        problems.display = True
        text = Text()
        text.append(f"{len(records)} record(s) ignored (untrusted):\n", style="red bold")
        for i, p in enumerate(records):
            text.append(f"  IGNORED {p}", style="red")
            if i != len(records) - 1:
                text.append("\n")
        problems.update(text)

    # -- selection → provenance ----------------------------------------------

    def on_data_table_row_selected(self, event: DataTable.RowSelected) -> None:
        """Point the provenance pane at the selected clone and reveal it.

        Read-only: ``ProvenancePane.show`` only calls ``api.provenance(vm)``.
        """
        vm = event.row_key.value
        if not vm:
            return
        try:
            from rhubarb.tui.provenance_pane import ProvenancePane

            pane = self.app.query_one("#provenance-pane", ProvenancePane)
            # Reveal the pane first, then hand it the VM: the provenance pane owns
            # its own read/error handling, so navigation stays reliable regardless.
            self.app.query_one(TabbedContent).active = "provenance"
            pane.show(vm)
        except Exception:
            # Wiring is best-effort; a missing pane must not break the clones view.
            pass
