"""Images pane — read-only view of ``api.images()``.

Renders one row per local RhubarbTart image (and its vanilla/unverified
precursors) as returned by ``rhubarb.api.images()``: name, profile, status
(``current``/``outdated`` for a finished image, else ``vanilla``/``unverified``)
and how many clones descend from it.

Strictly read-only: this module calls ``rhubarb.api.images()`` and nothing else.
It never invokes a mutating operation (``new``/``run``/``enroll``/``reset``/
``rm``) and never touches ``tart`` or the keychain directly. The core API is
imported lazily inside ``refresh_data`` so the package imports (and the headless
render test runs) without ``tart`` present.
"""

from textual.app import ComposeResult
from textual.containers import VerticalScroll
from textual.widgets import DataTable, Static


class ImagesPane(VerticalScroll):
    """Current verified image per profile, plus vanilla/unverified precursors."""

    BORDER_TITLE = "Images"

    DEFAULT_CSS = """
    ImagesPane #images-status {
        padding: 0 1;
        color: $text-muted;
    }
    ImagesPane #images-status.-error {
        color: $error;
    }
    ImagesPane #images-table {
        height: auto;
    }
    """

    # Column headers, in display order, paired with the ``api.Image`` attribute
    # each cell is drawn from.
    _COLUMNS = (
        ("Name", "name"),
        ("Profile", "profile"),
        ("Status", "status"),
        ("Clones", "clones"),
    )

    def compose(self) -> ComposeResult:
        yield Static("", id="images-status")
        table = DataTable(id="images-table", zebra_stripes=True, cursor_type="row")
        table.add_columns(*(label for label, _ in self._COLUMNS))
        yield table

    def refresh_data(self) -> None:
        """(Re)load from ``rhubarb.api.images()`` and re-render. Read-only.

        Handles the empty case and the two documented API errors
        (``api.VerifyError`` — e.g. a bad/absent lock — and ``FileNotFoundError``
        — a host tool such as ``tart`` not installed) by showing the message in
        the status line rather than letting the app crash.
        """
        if not self.is_mounted:
            return  # compose() has not run yet; the shell will call us again.
        self.render_data(self.fetch())

    def fetch(self) -> dict:
        """The blocking read (it runs ``tart list``), safe off the UI thread (#120).

        Returns ``{"rows": [...]}`` or ``{"error": message}``; never raises.
        """
        from rhubarb import api

        try:
            return {"rows": api.images()}
        except api.VerifyError as exc:
            return {"error": str(exc) or "could not read images"}
        except FileNotFoundError as exc:
            tool = getattr(exc, "filename", None) or "a required host tool"
            return {"error": f"{tool} is not installed — cannot list images"}
        except Exception as exc:  # never let a refresh take down the app
            return {"error": f"unexpected error reading images: {exc}"}

    def render_data(self, data: dict) -> None:
        """Apply a ``fetch()`` result (UI thread)."""
        if not self.is_mounted:
            return
        if "error" in data:
            self._show_error(data["error"])
            return
        rows = data["rows"]

        table = self.query_one("#images-table", DataTable)
        table.clear()
        for img in rows:
            table.add_row(img.name, img.profile, img.status, str(img.clones))

        status = self.query_one("#images-status", Static)
        status.remove_class("-error")
        if not rows:
            status.update("No images built on this host yet.")
        else:
            n = len(rows)
            status.update(f"{n} image{'s' if n != 1 else ''}")

    def _show_error(self, message: str) -> None:
        """Surface an error/empty condition in the status line and clear the table."""
        try:
            self.query_one("#images-table", DataTable).clear()
        except Exception:
            pass
        status = self.query_one("#images-status", Static)
        status.add_class("-error")
        status.update(message)
