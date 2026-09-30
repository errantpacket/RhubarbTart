"""DataTable helpers shared by the TUI panes (#122).

A pane that rebuilds its table on refresh would reset the cursor to the top row, and the
rebuild's highlight events would move whatever follows the highlight (the provenance view,
the Logs viewer) there too. :func:`rebuilding` wraps a rebuild so the highlighted row survives.
"""

from collections.abc import Hashable, Iterator
from contextlib import contextmanager, nullcontext

from textual.widgets import DataTable


def cursor_key(table: DataTable) -> Hashable | None:
    """The key of the row under the cursor, or ``None`` if there is none."""
    try:
        if table.row_count and table.is_valid_coordinate(table.cursor_coordinate):
            return table.coordinate_to_cell_key(table.cursor_coordinate).row_key.value
    except Exception:
        pass
    return None


def restore_cursor(table: DataTable, key: Hashable | None) -> None:
    """Put the cursor back on the row keyed ``key``, if that row still exists."""
    if key is None:
        return
    try:
        table.move_cursor(row=table.get_row_index(key), animate=False)
    except Exception:
        pass


@contextmanager
def rebuilding(table: DataTable, keep: Hashable | None = None) -> Iterator[None]:
    """Rebuild ``table`` inside this block without losing the highlighted row.

    Restores the cursor to ``keep`` (default: the row highlighted on entry) afterwards. While
    a row is being kept, the rebuild's own highlight events are suppressed so listeners don't
    see a spurious jump to the top.
    """
    key = cursor_key(table) if keep is None else keep
    with table.prevent(DataTable.RowHighlighted) if key is not None else nullcontext():
        yield
        restore_cursor(table, key)
