"""Clones pane — read-only view of ``api.clones()``.

Renders one row per ``api.Clone`` (name, profile, live state, freshness, password
mode, enrollments) in a ``DataTable``, and surfaces ``CloneList.problems`` (the
``IGNORED ...`` records that failed the StrictModes trust rules) below it.

Highlighting a clone points the Provenance tab at the image it was cloned from, and Enter
opens that tab; the app does that routing (``rhubarb.tui.app``), not this pane.

Strictly **read-only**: the only core call is ``rhubarb.api.clones()`` — never
``new``/``run``/``enroll``/``reset``/``rm``, and never ``tart`` or the keychain
directly. The API is imported lazily inside ``refresh_data`` so that importing
this package never requires ``tart`` to be installed (headless test / Linux dev).
"""

from rich.text import Text
from textual.app import ComposeResult
from textual.containers import VerticalScroll
from textual.widgets import DataTable, Static

from .tables import rebuilding

# Column headers, in order. "FRESHNESS" is the image-staleness verdict
# (current / outdated / image-deleted); "PASSWORD" is the keychain mode
# (unique / inherited).
_COLUMNS = ("Clone", "Profile", "State", "Freshness", "Password", "Enrolled")

# Colour cues for at-a-glance status. Anything not listed renders plain.
_STATE_STYLE = {"running": "green", "MISSING": "red bold"}
_FRESH_STYLE = {"current": "green", "outdated": "yellow", "image-deleted": "red bold",
                "base-missing": "red bold"}
_PW_STYLE = {"unique": "green", "inherited": "yellow"}


class ClonesPane(VerticalScroll):
    """Every research clone: live state, staleness, password mode, enrollments."""

    _sig = None   # last rendered rows, to skip no-op rebuilds

    def __init__(self, *args, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        # The last clones read, by name: lets the app resolve a clone without a `tart` call.
        self.by_name: dict = {}

    BORDER_TITLE = "Clones"

    DEFAULT_CSS = """
    ClonesPane #clones-status { padding: 0 1; color: $text-muted; }
    ClonesPane #clones-status.-error { color: $error; }
    ClonesPane #clones-problems { padding: 1 1 0 1; }
    """

    def compose(self) -> ComposeResult:
        # A one-line status/empty/error banner above the table.
        yield Static("Loading clones…", id="clones-status")
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
            status.add_class("-error")
            status.update(data["error"])
            return
        status.remove_class("-error")
        result = data["result"]
        self.by_name = {c.name: c for c in result.clones}
        self._render_clones(status, table, result.clones)
        self._render_problems(problems, result.problems)

    # -- rendering helpers ----------------------------------------------------

    def _render_clones(self, status: Static, table: DataTable, clones: list) -> None:
        if not clones:
            table.clear()  # keeps columns
            self._sig = None
            table.display = False
            status.update("No clones yet. Press n to make one from an image.")
            return

        table.display = True
        n = len(clones)
        status.update(f"{n} clone{'s' if n != 1 else ''} · Enter shows provenance")
        sig = [(c.name, c.profile, c.state, c.freshness, c.password_mode, tuple(c.enrollments))
               for c in clones]
        if sig == self._sig:
            return   # nothing changed: leave the table (and the cursor) alone
        self._sig = sig
        with rebuilding(table):   # keeps the highlighted clone across the rebuild
            self._fill(table, clones)

    def _fill(self, table: DataTable, clones: list) -> None:
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
