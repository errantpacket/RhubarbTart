"""Logs pane — read-only view of ``api.list_logs()`` / ``api.tail_log()`` (#120, #122).

Left: every readable log (each clone's run log, build output written by the Build action, and
the lifecycle trail), newest first. Right: the highlighted log, following it as it grows. An
idle log costs one ``stat``; a growing one costs only the bytes it wrote (``tail_log`` returns
what was appended since the last read), and new lines are appended to the viewer. The viewer
follows the end while you are at the bottom and stays put while you scroll back through it.

Strictly read-only: it calls ``rhubarb.api.list_logs()`` and ``rhubarb.api.tail_log()`` and
nothing else; the core confines reads to regular files inside the state dir. The core API is
imported lazily so the package imports without ``tart``.

Like the other panes it offers ``refresh_data()`` (fetch + render, on the calling thread), and
also the split ``fetch()`` / ``render_data()`` so the shell can do the file work off the UI thread.
"""

from collections import deque

from rich.text import Text
from textual.app import ComposeResult
from textual.containers import Horizontal, Vertical
from textual.widgets import DataTable, Log, Static

from .tables import rebuilding

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
    LogsPane #logs-main { width: 1fr; border-left: solid $panel; }
    LogsPane #logs-title { padding: 0 1; color: $text-muted; }
    """

    def __init__(self, *args, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        self._selected: str | None = None      # log id the viewer should show
        self._shown: tuple | None = None       # (id, size, mtime) currently in the viewer
        self._cursor: tuple | None = None      # tail_log cursor for the shown log
        self._partial = False                  # the viewer's last line is unfinished
        self._lines: deque = deque(maxlen=MAX_LINES)   # what the viewer holds
        self._rows: list | None = None         # last rendered list, to avoid needless rebuilds
        self._cols: list | None = None         # column keys, for in-place cell updates

    def compose(self) -> ComposeResult:
        with Vertical(id="logs-side"):
            yield Static("Loading logs…", id="logs-status")
            table: DataTable = DataTable(id="logs-table", zebra_stripes=True, cursor_type="row")
            yield table
        with Vertical(id="logs-main"):
            yield Static("", id="logs-title")
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
        asked, shown, cursor = self._selected, self._shown, self._cursor
        ids = [r.id for r in logs]
        sel = asked if asked in ids else (ids[0] if ids else None)
        tail = None
        ref = next((r for r in logs if r.id == sel), None)
        if ref is not None and shown != (ref.id, ref.size, ref.mtime):
            same = shown is not None and shown[0] == ref.id
            try:
                tail = api.tail_log(ref.id, cursor if same else None, max_lines=MAX_LINES)
            except Exception as e:
                tail = api.LogTail(text=f"(cannot read {ref.label}: {e})", cursor=(),
                                   reset=True, partial=False)
        return {"logs": logs, "asked": asked, "selected": sel, "tail": tail, "ref": ref}

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
            status.update("No logs yet. Running a clone or building from the TUI (B) writes here.")
            table.clear()
            self._rows = None
            view.clear()
            self._lines.clear()
            self._shown = self._cursor = None
            self.query_one("#logs-title", Static).update("")
            return
        # The highlight moved while this was being read: keep the operator's choice and let
        # the next read (already requested by the highlight) fill the viewer.
        stale = data["asked"] != self._selected
        selected = self._selected if stale else data["selected"]
        n = len(logs)
        status.update(f"{n} log{'s' if n != 1 else ''} · newest first")

        # Rebuild the list only when its contents changed, so the cursor doesn't jump.
        rows = [(r.id, r.label, r.kind, _size(r.size), _when(r.mtime)) for r in logs]
        if self._rows != rows:
            same_order = self._rows is not None and [r[0] for r in self._rows] == [r[0] for r in rows]
            if same_order and self._cols:
                # Same logs, same order: only size/time moved. Update those cells in place so
                # the table (and the cursor) stay put.
                for (rid, label, _kind, size, when), old in zip(rows, self._rows, strict=True):
                    if (size, when) != (old[3], old[4]):
                        dim = "dim" if label.endswith("(removed)") else ""
                        table.update_cell(rid, self._cols[2], Text(size, style=dim))
                        table.update_cell(rid, self._cols[3], Text(when, style=dim))
            else:
                # A log appeared, went, or moved up the list: rebuild, then put the cursor back
                # on the selected log. Its highlight events are suppressed, or the rebuild's
                # "row 0 highlighted" would pull the selection back to the top.
                with rebuilding(table, keep=selected):
                    table.clear()
                    for rid, label, kind, size, when in rows:
                        # Logs of clones that no longer exist stay readable, but step back.
                        dim = "dim" if label.endswith("(removed)") else ""
                        table.add_row(Text(label, style=dim),
                                      Text(kind, style=dim or _KIND_STYLE.get(kind, "")),
                                      Text(size, style=dim), Text(when, style=dim), key=rid)
            self._rows = rows

        if stale:
            return
        self._selected = selected
        ref, tail = data["ref"], data["tail"]
        if ref is not None:
            self.query_one("#logs-title", Static).update(
                f"{ref.label} · {ref.kind} log · {_size(ref.size)} · updated {_when(ref.mtime)}")
        if tail is not None and ref is not None:
            self._show(view, tail, new_log=self._shown is None or self._shown[0] != ref.id)
            self._shown = (ref.id, ref.size, ref.mtime)

    def _show(self, view: Log, tail, *, new_log: bool) -> None:
        """Put a ``tail_log`` result in the viewer: append when it continues what is shown,
        redraw otherwise. Follow the end only if the operator was already there: an append
        does that by itself (``Log.write_lines`` scrolls only from the end), but a redraw
        starts from an empty view, so it puts the operator's position back explicitly."""
        follow = new_log or view.is_vertical_scroll_end
        lines = tail.text.split("\n") if tail.text else []
        if tail.reset or self._partial:
            # Replace everything, or re-send an unfinished last line now that it has more.
            if tail.reset:
                self._lines.clear()
            elif self._lines:
                self._lines.pop()
            self._lines.extend(lines)
            y = view.scroll_y
            view.clear()
            view.write_lines(list(self._lines), scroll_end=follow)
            if not follow:
                view.scroll_to(y=y, animate=False, immediate=True)
        elif lines:
            self._lines.extend(lines)
            view.write_lines(lines, scroll_end=follow)
        self._partial = tail.partial
        self._cursor = tail.cursor or None

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
            self._shown = self._cursor = None   # read the new selection from its tail
            self.refresh_data()
