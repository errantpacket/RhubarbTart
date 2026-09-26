"""Provenance pane — read-only detail view of ``api.provenance(vm)``.

Given a VM name (set via ``show``), renders its parsed
``out/<vm>.provenance.json``: profile, built_at, git commit/dirty, the pinned
toolchain, the inputs/lock sha256 identity, the committed lock's key fields, and
the per-file build digests.

Read-only by construction: the only core-API call here is
``rhubarb.api.provenance(vm)`` — never ``new``/``run``/``enroll``/``reset``/
``rm``, and never ``tart`` or the keychain directly. ``api`` is imported lazily
inside the methods so importing this package never requires ``tart`` present
(the headless render test and off-Mac dev both work).
"""

from rich.console import Group, RenderableType
from rich.table import Table
from rich.text import Text
from textual.app import ComposeResult
from textual.containers import VerticalScroll
from textual.css.query import NoMatches
from textual.widgets import Static

# How many build-file digests to list before collapsing the rest into a count.
_MAX_BUILD_FILES = 40


class ProvenancePane(VerticalScroll):
    """The parsed provenance record for the currently selected VM."""

    BORDER_TITLE = "Provenance"

    def __init__(self, *args, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        self._vm: str | None = None
        # Set before the body widget is mounted (the shell may refresh_data on
        # mount before compose has run); applied in on_mount.
        self._pending: RenderableType | None = None

    def compose(self) -> ComposeResult:
        yield Static(self._empty_message(), id="provenance-body")

    def on_mount(self) -> None:
        if self._pending is not None:
            self._write(self._pending)
            self._pending = None

    def show(self, vm: str) -> None:
        """Point the pane at ``vm`` (e.g. from a clones-pane selection) and reload."""
        self._vm = vm
        self.refresh_data()

    def refresh_data(self) -> None:
        """(Re)render the current VM's provenance via ``rhubarb.api``. Read-only.

        Reads ``out/<vm>.provenance.json`` through ``api.provenance``. A missing
        record (``FileNotFoundError``) or a malformed one (``api.VerifyError``)
        is shown as a message instead of crashing the app.
        """
        if self._vm is None:
            self._write(self._empty_message())
            return
        from rhubarb import api  # lazy: importing this package must not require tart

        try:
            rec = api.provenance(self._vm)  # api.Provenance
        except FileNotFoundError:
            self._write(Text.assemble(
                ("No provenance record ", "bold"),
                (f"for {self._vm}\n", "bold"),
                (f"(out/{self._vm}.provenance.json not found — was it built on this host?)",
                 "dim"),
            ))
            return
        except api.VerifyError as e:
            self._write(Text.assemble(
                (f"Cannot read provenance for {self._vm}\n", "bold red"),
                (str(e), "red"),
            ))
            return
        except Exception as e:  # never let a pane refresh take the whole TUI down
            self._write(Text.assemble(
                (f"Unexpected error reading provenance for {self._vm}\n", "bold red"),
                (f"{type(e).__name__}: {e}", "red"),
            ))
            return
        self._write(self._build_view(rec))

    # -- rendering -------------------------------------------------------------

    def _empty_message(self) -> RenderableType:
        return Text.assemble(
            ("No VM selected\n", "bold"),
            ("Select a clone in the Clones tab to see its build provenance.", "dim"),
        )

    def _build_view(self, rec) -> RenderableType:
        """Build the detail view for an ``api.Provenance`` record."""
        parts: list[RenderableType] = []

        title = Table.grid(padding=(0, 1))
        title.add_column()
        title.add_row(Text(rec.vm, style="bold"))
        parts.append(title)

        # Identity / build summary.
        summary = self._kv_table()
        summary.add_row("profile", rec.profile)
        summary.add_row("built at", rec.built_at)
        summary.add_row("git commit", self._git_line(rec))
        summary.add_row("inputs sha256", self._sha(rec.inputs_sha256))
        summary.add_row("lock sha256", self._sha(rec.lock_sha256))
        parts.append(self._section("build", summary))

        # Pinned host toolchain.
        parts.append(self._section("toolchain", self._mapping_table(rec.toolchain)))

        # Committed lock — key scalar fields; nested structures noted by size.
        parts.append(self._section("lock", self._mapping_table(rec.lock)))

        # Live tart VM record captured at build time (may be absent).
        if rec.tart_vm:
            parts.append(self._section("tart vm", self._mapping_table(rec.tart_vm)))
        else:
            parts.append(self._section("tart vm", Text("(not captured)", style="dim")))

        # Per-file build digests.
        parts.append(self._section(
            f"build files ({len(rec.build_files)})", self._build_files(rec.build_files)))

        return Group(*parts)

    def _section(self, heading: str, body: RenderableType) -> RenderableType:
        return Group(Text(f"\n{heading}", style="bold underline"), body)

    def _kv_table(self) -> Table:
        t = Table.grid(padding=(0, 2))
        t.add_column(style="cyan", justify="right", no_wrap=True)
        t.add_column(overflow="fold")
        return t

    def _mapping_table(self, mapping) -> RenderableType:
        """Render a dict's scalar entries as key/value rows; summarise nested ones."""
        if not isinstance(mapping, dict) or not mapping:
            return Text("(none)", style="dim")
        t = self._kv_table()
        for key in sorted(mapping, key=str):
            value = mapping[key]
            if isinstance(value, dict):
                rendered: RenderableType = Text(f"{{…}} ({len(value)} keys)", style="dim")
            elif isinstance(value, list):
                rendered = Text(f"[…] ({len(value)} items)", style="dim")
            elif isinstance(value, bool):
                rendered = "yes" if value else "no"
            elif value is None:
                rendered = Text("null", style="dim")
            else:
                rendered = str(value)
            t.add_row(str(key), rendered)
        return t

    def _build_files(self, files) -> RenderableType:
        if not files:
            return Text("(none)", style="dim")
        t = Table.grid(padding=(0, 2))
        t.add_column(overflow="fold")
        t.add_column(style="dim", no_wrap=True)
        for i, path in enumerate(sorted(files)):
            if i >= _MAX_BUILD_FILES:
                t.add_row(Text(f"… and {len(files) - _MAX_BUILD_FILES} more", style="dim"), "")
                break
            t.add_row(str(path), self._sha(files[path], short=True))
        return t

    def _git_line(self, rec) -> RenderableType:
        if not rec.git_commit:
            return Text("(not a git checkout)", style="dim")
        commit = Text(str(rec.git_commit))
        if rec.git_dirty:
            commit.append("  dirty tree", style="yellow")
        return commit

    def _sha(self, value, short: bool = False) -> RenderableType:
        if not value:
            return Text("(none)", style="dim")
        s = str(value)
        if short and len(s) > 16:
            return Text(s[:16] + "…", style="dim")
        return Text(s)

    def _write(self, renderable: RenderableType) -> None:
        """Update the body widget, deferring until it exists if refreshed early."""
        try:
            self.query_one("#provenance-body", Static).update(renderable)
        except NoMatches:
            self._pending = renderable
