# /// script
# requires-python = ">=3.12"
# dependencies = ["textual==8.2.8"]
# ///
"""Headless render test for the read-only RhubarbTart TUI (Phase 0.5, Stage B — Gate B).

Runs the Textual app under Textual's own headless harness (``App.run_test`` /
``Pilot``) with the core API **mocked** — ``rhubarb.api.images`` / ``.clones`` /
``.provenance`` are monkeypatched to return fixed dataclass values — so it needs
no Mac, no ``tart`` and no keychain. It asserts the app mounts and that each of
the three panes renders its mock rows, and exercises the read-only
clones -> provenance selection wiring.

This is Textual-dependent, so unlike ``tools/test_rhubarb.py`` (stdlib-only) it
carries its own PEP 723 metadata pinning ``textual==8.2.8`` and an adjacent uv
script lockfile (``tools/test_rhubarb_tui.py.lock``, ``uv lock --script``) that
hash-verifies it — matching how ``./rhubarb-tui`` runs the app itself. ``check.sh``
runs it with ``uv run --script``. See docs/INTERFACE-PLAN.md, Gate B.
"""

import asyncio
import sys
from pathlib import Path

TOOLS = Path(__file__).resolve().parent
sys.path.insert(0, str(TOOLS))

from rich.console import Console  # noqa: E402  (bundled with textual)

from rhubarb import api  # noqa: E402  the single core-API surface the TUI reads through

FAILS: list[str] = []


def check(name: str, cond: bool) -> None:
    print(f"  {'ok  ' if cond else 'FAIL'}  {name}")
    if not cond:
        FAILS.append(name)


# ---- mock API data (no tart / keychain / Mac) ----------------------------------------------
# Fixed dataclass values standing in for what the core API returns on the build Mac.

MOCK_IMAGES = [
    api.Image(name="rbt-kali-research-cc4479ae7492", profile="kali-research",
              kind="image", status="current", clones=2),
    api.Image(name="rbt-nixos-research-fedcba987654", profile="nixos-research",
              kind="image", status="outdated", clones=0),
    api.Image(name="macos-tahoe-vanilla", profile="-", kind="vanilla",
              status="vanilla", clones=0),
]

MOCK_CLONES = api.CloneList(
    clones=[
        api.Clone(name="work-1", profile="kali-research", family="kali",
                  image="rbt-kali-research-cc4479ae7492", state="running",
                  freshness="current", password_mode="unique",
                  password_account="work-1", enrollments=["tailscale"],
                  created_at="2026-01-02T03:04:05Z"),
        api.Clone(name="work-2", profile="nixos-research", family="nixos",
                  image="rbt-nixos-research-fedcba987654", state="MISSING",
                  freshness="outdated", password_mode="inherited",
                  password_account="rbt-nixos-research-fedcba987654",
                  enrollments=[], created_at="2026-01-03T00:00:00Z"),
    ],
    problems=["work-9.json: record name != filename (untrusted)"],
)

MOCK_PROVENANCE = api.Provenance(
    vm="work-1", profile="kali-research", built_at="2026-01-02T03:04:05Z",
    git_commit="deadbeefcafe", git_dirty=True,
    toolchain={"tart": "2.0.0", "packer": "1.14.2"},
    tart_vm={"Name": "work-1", "Running": False},
    inputs_sha256="a" * 64, lock_sha256="b" * 64,
    lock={"schema": 2, "profile": "kali-research"},
    build_files={"packer/kali/kali.pkr.hcl": "c" * 64},
)


def _text(renderable) -> str:
    """Flatten a cell value or a rich renderable to plain text for substring checks."""
    if isinstance(renderable, str):
        return renderable
    console = Console(width=240, no_color=True)
    with console.capture() as cap:
        console.print(renderable, end="")
    return cap.get()


def _row_text(row) -> str:
    return " | ".join(_text(cell) for cell in row)


def _static_text(widget) -> str:
    """Plain text a Static widget currently displays.

    Textual renders a Static's content to a visual: simple markup/Text becomes a
    ``Content`` (``.plain``), while a rich ``Group``/``Table`` stays a
    ``RichVisual`` wrapping the original renderable, which we flatten with rich.
    """
    visual = widget.render()
    if hasattr(visual, "plain"):
        return visual.plain
    return _text(getattr(visual, "_renderable", visual))


def _mock_images():
    return MOCK_IMAGES


def _mock_clones():
    return MOCK_CLONES


def _mock_provenance(vm):
    return MOCK_PROVENANCE


async def _render() -> None:
    # Mock the core API before the app mounts (each pane calls api.* in refresh_data).
    api.images = _mock_images
    api.clones = _mock_clones
    api.provenance = _mock_provenance

    import rhubarb_tui
    from textual.widgets import DataTable, Static, TabbedContent

    app = rhubarb_tui.RhubarbTUI()
    async with app.run_test() as pilot:
        await pilot.pause()  # let on_mount -> action_refresh_all run against the mocks

        check("app mounts headlessly and shows all three panes",
              app.query_one("#images-pane") is not None
              and app.query_one("#clones-pane") is not None
              and app.query_one("#provenance-pane") is not None)

        # -- images pane --------------------------------------------------------
        itable = app.query_one("#images-table", DataTable)
        check("images pane renders one row per mock image",
              itable.row_count == len(MOCK_IMAGES))
        irow0 = _row_text(itable.get_row_at(0))
        check("images pane shows the mock image name, status and clone count",
              "rbt-kali-research-cc4479ae7492" in irow0 and "current" in irow0 and "2" in irow0)
        istatus = _static_text(app.query_one("#images-status", Static))
        check("images pane status line reflects the mock count", "3 image" in istatus)

        # -- clones pane --------------------------------------------------------
        ctable = app.query_one("#clones-table", DataTable)
        check("clones pane renders one row per mock clone",
              ctable.row_count == len(MOCK_CLONES.clones))
        crow0 = _row_text(ctable.get_row_at(0))
        check("clones pane shows the mock clone's name, state and freshness",
              "work-1" in crow0 and "running" in crow0 and "current" in crow0)
        crow1 = _row_text(ctable.get_row_at(1))
        check("clones pane shows the second mock clone (MISSING / outdated / inherited)",
              "work-2" in crow1 and "MISSING" in crow1 and "inherited" in crow1)
        cprob = _static_text(app.query_one("#clones-problems", Static))
        check("clones pane surfaces rejected (untrusted) records",
              "ignored" in cprob.lower() and "work-9.json" in cprob)

        # -- provenance pane ----------------------------------------------------
        prov = app.query_one("#provenance-pane")
        body_before = _static_text(app.query_one("#provenance-body", Static))
        check("provenance pane is empty until a VM is selected",
              "No VM selected" in body_before)

        # Read-only selection wiring: pointing the pane at a VM renders its record.
        prov.show("work-1")
        await pilot.pause()
        body_after = _static_text(app.query_one("#provenance-body", Static))
        check("provenance pane renders the mock record's key fields",
              "work-1" in body_after and "kali-research" in body_after
              and "deadbeefcafe" in body_after and "packer/kali/kali.pkr.hcl" in body_after)

        # -- keyboard tab navigation (read-only) --------------------------------
        await pilot.press("3")
        await pilot.pause()
        check("number keys switch panes (Provenance)",
              app.query_one(TabbedContent).active == "provenance")
        await pilot.press("1")
        await pilot.pause()
        check("number keys switch panes (Images)",
              app.query_one(TabbedContent).active == "images")

        # -- refresh binding re-reads the (mocked) API without error ------------
        await pilot.press("r")
        await pilot.pause()
        check("refresh binding re-renders every pane from the API",
              app.query_one("#images-table", DataTable).row_count == len(MOCK_IMAGES)
              and app.query_one("#clones-table", DataTable).row_count == len(MOCK_CLONES.clones))


def main() -> None:
    asyncio.run(_render())
    if FAILS:
        sys.exit(f"{len(FAILS)} TUI render test(s) failed")
    print("all rhubarb TUI render tests passed")


if __name__ == "__main__":
    main()
