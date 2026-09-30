"""Logs pane — read-only view of ``api.list_logs()`` / ``api.read_log()`` (#120).

Left: every readable log (each clone's run log, build output written by the Build action, and
the lifecycle trail), newest first. Right: the highlighted log, tailing as it grows. The pane
re-reads a log only when its size or timestamp changed, so an idle log costs one ``stat``.

Strictly read-only: it calls ``rhubarb.api.list_logs()`` and ``rhubarb.api.read_log()`` and
nothing else; the core confines reads to regular files inside the state dir. The core API is
imported lazily so the package imports without ``tart``.

Like the other panes it offers ``refresh_data()`` (fetch + render, on the calling thread), and
also the split ``fetch()`` / ``render()`` so the shell can do the file work off the UI thread.
"""

from rich.text import Text
from textual.app import ComposeResult
from textual.containers import Horizontal, Vertical
from textual.widgets import DataTable, Log, Static

_KIND_STYLE = {"build": "cyan", "clone": "green", "events": "magenta"}
MAX_LINES = 2000


def _when(iso: str) -> str:
    """A compact local time for the list (the full timestamp doesn't fit the side panel)."""
    from datetime import datetime

    try:
        return datetime.fromisoformat(iso).astimezone().strftime("%m-%d %H:%M")
    except ValueError:
        return iso


def _size(n: int) -> str:
    for unit in ("B", "KB", "MB"):
        if n < 1024 or unit == "MB":
            return f"{n:.0f} {unit}" if unit == "B" else f"{n:.1f} {unit}"
        n /= 1024
    return f"{n:.1f} MB"


class LogsPane(Horizontal):
    """Build and clone logs, with a tailing viewer."""

    BORDER_TITLE = "Logs"

    DEFAULT_CSS = """
    LogsPane #logs-side { width: 58; }
    LogsPane #logs-status { padding: 0 1; color: $text-muted; }
    LogsPane #logs-status.-error { color: $error; }
    LogsPane #logs-view { width: 1fr; border-left: solid $panel; }
    """

    def __init__(self, *args, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        self._selected: str | None = None      # log id the viewer should show
        self._shown: tuple | None = None       # (id, size, mtime) currently in the viewer
        self._rows: list | None = None         # last rendered list, to avoid needless rebuilds
        self._cols: list | None = None         # column keys, for in-place cell updates

    def compose(self) -> ComposeResult:
        with Vertical(id="logs-side"):
            yield Static("", id="logs-status")
            table: DataTable = DataTable(id="logs-table", zebra_stripes=True, cursor_type="row")
            yield table
        yield Log(id="logs-view", highlight=False, auto_scroll=True, max_lines=MAX_LINES)

    def on_mount(self) -> None:
        table = self.query_one("#logs-table", DataTable)
        if not table.columns:
            self._cols = table.add_columns("Log", "Kind", "Size", "Updated")

    # -- data -----------------------------------------------------------------

    def fetch(self) -> dict:
        """Blocking read (safe off the UI thread): the log list, plus the selected log's text
        when it changed since it was last shown. Errors come back as ``{"error": msg}``."""
        from rhubarb import api  # lazy: package imports without tart present

        try:
            logs = api.list_logs()
        except Exception as e:  # never let a refresh take down the app
            return {"error": f"cannot list logs: {e}"}
        ids = [r.id for r in logs]
        sel = self._selected if self._selected in ids else (ids[0] if ids else None)
        text = None
        ref = next((r for r in logs if r.id == sel), None)
        if ref is not None and self._shown != (ref.id, ref.size, ref.mtime):
            try:
                text = api.read_log(ref.id, max_lines=MAX_LINES)
            except Exception as e:
                text = f"(cannot read {ref.label}: {e})"
        return {"logs": logs, "selected": sel, "text": text, "ref": ref}

    def render_data(self, data: dict) -> None:
        """Apply a ``fetch()`` result (UI thread)."""
        if not self.is_mounted:
            return
        status = self.query_one("#logs-status", Static)
        table = self.query_one("#logs-table", DataTable)
        view = self.query_one("#logs-view", Log)
        if "error" in data:
            status.add_class("-error")
            status.update(data["error"])
            return
        status.remove_class("-error")
        logs = data["logs"]
        if not logs:
            status.update("No logs yet. Clone runs and TUI builds write here.")
            table.clear()
            view.clear()
            self._shown = None
            return
        status.update(f"{len(logs)} log(s) — newest first")

        # Rebuild the list only when its contents changed, so the cursor doesn't jump.
        rows = [(r.id, r.label, r.kind, _size(r.size), _when(r.mtime)) for r in logs]
        if self._rows != rows:
            same_order = self._rows is not None and [r[0] for r in self._rows] == [r[0] for r in rows]
            if same_order and self._cols:
                # Same logs, same order: only size/time moved. Update those cells in place so
                # the table (and the cursor) stay put.
                for (rid, label, _kind, size, when), old in zip(rows, self._rows):
                    if (size, when) != (old[3], old[4]):
                        dim = "dim" if label.endswith("(removed)") else ""
                        table.update_cell(rid, self._cols[2], Text(size, style=dim))
                        table.update_cell(rid, self._cols[3], Text(when, style=dim))
            else:
                # A log appeared, went, or moved up the list: rebuild, then put the cursor back
                # on the selected log. Its highlight events are suppressed, or the rebuild's
                # "row 0 highlighted" would pull the selection back to the top.
                with table.prevent(DataTable.RowHighlighted):
                    table.clear()
                    for rid, label, kind, size, when in rows:
                        # Logs of clones that no longer exist stay readable, but step back.
                        dim = "dim" if label.endswith("(removed)") else ""
                        table.add_row(Text(label, style=dim),
                                      Text(kind, style=dim or _KIND_STYLE.get(kind, "")),
                                      Text(size, style=dim), Text(when, style=dim), key=rid)
                    if data["selected"] is not None:
                        try:
                            table.move_cursor(row=table.get_row_index(data["selected"]),
                                              animate=False)
                        except Exception:
                            pass
            self._rows = rows

        self._selected = data["selected"]
        ref = data["ref"]
        if data["text"] is not None and ref is not None:
            view.clear()
            view.write(data["text"])
            self._shown = (ref.id, ref.size, ref.mtime)

    def refresh_data(self) -> None:
        """(Re)load and re-render on the calling thread. Read-only."""
        if not self.is_mounted:
            return
        self.render_data(self.fetch())

    # -- selection ------------------------------------------------------------

    def on_data_table_row_highlighted(self, event: DataTable.RowHighlighted) -> None:
        """Show the highlighted log at once."""
        if event.data_table.id != "logs-table":
            return
        event.stop()
        key = event.row_key.value
        if key and key != self._selected:
            self._selected = key
            self._shown = None     # force a re-read of the new selection
            self.refresh_data()
