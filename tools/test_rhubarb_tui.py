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
hash-verifies it — matching how ``./rhubarbtart-tui`` runs the app itself. ``check.sh``
runs it with ``uv run --script``. See docs/INTERFACE-PLAN.md, Gate B.
"""

import asyncio
import os
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


def _log_text(app) -> str:
    """The plain text currently in the action log (the write surface / status area).

    The shell streams action milestones (and the core's #19 progress) into this
    ``RichLog`` via ``call_from_thread``; flattening its lines lets a test assert what
    the operator would actually see.
    """
    from textual.widgets import RichLog

    log = app.query_one("#action-log", RichLog)
    return "\n".join(strip.text for strip in log.lines)


def _mock_images():
    return MOCK_IMAGES


def _mock_clones():
    return MOCK_CLONES


def _mock_provenance(vm):
    return MOCK_PROVENANCE


MOCK_LOGS = {
    "logs/build-kali-research-20260930T120000Z.log": (
        api.LogRef(id="logs/build-kali-research-20260930T120000Z.log",
                   label="build kali-research-20260930T120000Z", kind="build",
                   size=2048, mtime="2026-09-30T12:05:00+00:00"),
        "[build] installing kali\n[smoke] PASSED\n[build] done: rbt-kali-research-cc4479ae7492"),
    "logs/work-1.log": (
        api.LogRef(id="logs/work-1.log", label="work-1", kind="clone",
                   size=64, mtime="2026-09-30T11:00:00+00:00"),
        "tart run work-1\nbooted"),
}


def _mock_list_logs():
    return [ref for ref, _ in sorted(MOCK_LOGS.values(), key=lambda v: v[0].mtime, reverse=True)]


def _tail(text_for):
    """A ``tail_log`` stand-in over mock log texts, with the core's semantics: the cursor
    remembers how much was read, a later call returns only what was appended (re-sending an
    unfinished last line), and a log that no longer continues the old text resets."""
    def tail_log(log_id, cursor=None, max_lines=2000):
        text = text_for(log_id)
        start = 0
        if cursor and cursor[0] == log_id and text.startswith(cursor[2]):
            start = len(cursor[2])
        new = text[start:]
        done = new.rfind("\n") + 1
        return api.LogTail(text="\n".join(new.splitlines()),
                           cursor=(log_id, 0, text[:start + done]),
                           reset=start == 0, partial=done < len(new))
    return tail_log


_mock_tail_log = _tail(lambda log_id: MOCK_LOGS[log_id][1])


def _view_text(app) -> str:
    """The plain text in the Logs tab's viewer."""
    from textual.widgets import Log

    return "\n".join(str(line) for line in app.query_one("#logs-view", Log).lines)


async def _logs_tab() -> None:
    """#120: the Logs tab lists build/clone logs newest first, shows the highlighted log,
    follows the selection, tails a log that grew, and the header shows when data was read."""
    from textual.widgets import DataTable

    api.images = _mock_images
    api.clones = _mock_clones
    api.provenance = _mock_provenance
    api.list_logs = _mock_list_logs
    api.tail_log = _mock_tail_log

    from rhubarb_tui import RhubarbTUI

    app = RhubarbTUI()
    async with app.run_test() as pilot:
        await pilot.pause()
        await pilot.press("4")
        await app.workers.wait_for_complete()
        await pilot.pause()
        table = app.query_one("#logs-table", DataTable)
        check("logs tab: lists every log", table.row_count == 2)
        check("logs tab: newest first (the build)",
              "build kali-research" in _row_text(table.get_row_at(0)))
        check("logs tab: shows the newest log", "[smoke] PASSED" in _view_text(app))
        check("logs tab: the viewer says which log it shows",
              _static_text(app.query_one("#logs-title")).startswith("build kali-research"))
        check("header: shows when data was last read", "updated" in app.sub_title)

        table.focus()
        await pilot.press("down")
        await pilot.pause()
        check("logs tab: follows the highlighted log", "booted" in _view_text(app)
              and "[smoke] PASSED" not in _view_text(app))

        # The clone log grows: a refresh tails the new content.
        ref, _ = MOCK_LOGS["logs/work-1.log"]
        MOCK_LOGS["logs/work-1.log"] = (
            api.LogRef(id=ref.id, label=ref.label, kind=ref.kind, size=128,
                       mtime="2026-09-30T13:00:00+00:00"),
            "tart run work-1\nbooted\nshutting down")
        await pilot.press("r")
        await app.workers.wait_for_complete()
        await pilot.pause()
        check("logs tab: tails a log that grew", "shutting down" in _view_text(app))


async def _follow_log() -> None:
    """#122: the viewer appends only what a log wrote (no duplicates, an unfinished line is
    completed in place), follows the end only while you are there, and a refresh that was in
    flight when you moved the highlight doesn't snap it back."""
    import threading

    from textual.widgets import DataTable, Log

    body = {"logs/build-x.log": "".join(f"line {i}\n" for i in range(200)),
            "logs/work-1.log": "tart run work-1\nbooted\n"}
    n = [0]

    def refs():
        n[0] += 1                                   # a new mtime every listing: always "changed"
        return [api.LogRef(id="logs/build-x.log", label="build x", kind="build",
                           size=len(body["logs/build-x.log"]), mtime=f"2026-09-30T12:{n[0] % 60:02d}:00+00:00"),
                api.LogRef(id="logs/work-1.log", label="work-1", kind="clone",
                           size=len(body["logs/work-1.log"]), mtime="2026-09-30T11:00:00+00:00")]

    ui_reads = []

    def images():
        ui_reads.append(threading.current_thread() is threading.main_thread())
        return MOCK_IMAGES

    api.images = images
    api.clones = _mock_clones
    api.provenance = _mock_provenance
    api.list_logs = refs
    api.tail_log = _tail(lambda log_id: body[log_id])
    from rhubarb_tui import RhubarbTUI

    app = RhubarbTUI()
    async with app.run_test() as pilot:
        await app.workers.wait_for_complete()
        await pilot.pause()
        check("startup: reads `tart` panes off the UI thread",
              ui_reads and not any(ui_reads))
        await pilot.press("4")
        await app.workers.wait_for_complete()
        await pilot.pause()
        view = app.query_one("#logs-view", Log)
        check("follow: the whole log is shown, at its end",
              view.line_count == 200 and view.is_vertical_scroll_end)

        async def grow(text: str) -> None:
            body["logs/build-x.log"] += text
            app._refresh_active()
            await app.workers.wait_for_complete()
            await pilot.pause()
            await pilot.wait_for_scheduled_animations()
            await pilot.pause()      # let any scroll the write requested land before checking

        await grow("".join(f"line {i}\n" for i in range(200, 205)))
        check("follow: appends only the new lines",
              view.line_count == 205 and _view_text(app).count("line 200") == 1)
        check("follow: stays at the end while you are there", view.is_vertical_scroll_end)

        view.scroll_home(animate=False, immediate=True)
        await pilot.pause()
        await grow("line 205\n")
        check("follow: scrolled back, new lines don't pull you to the end",
              view.line_count == 206 and view.scroll_y == 0)

        await grow("half")
        await grow(" done\nnext\n")
        check("follow: an unfinished line is completed in place",
              _view_text(app).endswith("line 205\nhalf done\nnext") and view.scroll_y == 0)

        # A refresh reads the build log; before it lands, the operator highlights work-1.
        pane = app.query_one("#logs-pane")
        body["logs/build-x.log"] += "late line\n"
        in_flight = pane.fetch()
        table = app.query_one("#logs-table", DataTable)
        table.focus()
        table.move_cursor(row=1, animate=False)
        await pilot.pause()
        pane.render_data(in_flight)
        await pilot.pause()
        check("follow: a refresh in flight doesn't snap the highlight back",
              pane._selected == "logs/work-1.log" and table.cursor_row == 1
              and "booted" in _view_text(app) and "late line" not in _view_text(app))


async def _polling() -> None:
    """#120: the poller reads tart-backed panes OFF the UI thread, skips while a poll is in
    flight, and leaves `tart` alone while an action runs."""
    import threading

    api.images = _mock_images
    api.provenance = _mock_provenance
    api.list_logs = _mock_list_logs
    api.tail_log = _mock_tail_log
    seen = []

    def counting_clones():
        seen.append(threading.current_thread() is threading.main_thread())
        return MOCK_CLONES

    api.clones = counting_clones
    from rhubarb_tui import RhubarbTUI

    app = RhubarbTUI()
    async with app.run_test() as pilot:
        await pilot.pause()
        await pilot.press("2")                         # Clones tab: the switch polls it
        await app.workers.wait_for_complete()
        await pilot.pause()
        check("poller: the tab switch read clones off the UI thread",
              seen and seen[-1] is False)
        base = len(seen)

        app._action_running = True
        app._refresh_active()
        await app.workers.wait_for_complete()
        check("poller: leaves `tart` alone while an action runs", len(seen) == base)

        app._action_running = False
        app._polling = True
        app._refresh_active()
        await app.workers.wait_for_complete()
        check("poller: skips while a poll is in flight", len(seen) == base)

        app._polling = False
        app._refresh_active()
        await app.workers.wait_for_complete()
        await pilot.pause()
        check("poller: refreshes the visible pane in a worker and clears the flag",
              len(seen) == base + 1 and seen[-1] is False and not app._polling)


async def _keys() -> None:
    """#122: the footer offers only what applies — clone actions on the Clones tab only, write
    actions disabled while one runs (and a second one refused) — `?` shows every key, and Enter
    on a clone opens its image's provenance (not a record for the clone's own name)."""
    import threading
    import types

    from textual.widgets import DataTable, HelpPanel

    from rhubarb.tui.confirm import ConfirmScreen

    api.images = _mock_images
    api.clones = _mock_clones
    api.list_logs = _mock_list_logs
    api.tail_log = _mock_tail_log
    asked = []
    api.provenance = lambda vm: (asked.append(vm), MOCK_PROVENANCE)[1]
    from rhubarb_tui import RhubarbTUI

    def footer_keys(app) -> set:
        return {b.key for _, b, enabled, _ in app.screen.active_bindings.values()
                if b.show and enabled}

    app = RhubarbTUI()
    async with app.run_test() as pilot:
        await app.workers.wait_for_complete()
        await pilot.pause()
        check("keys: clone actions are not offered on the Images tab",
              not {"b", "s", "e", "x", "d"} & footer_keys(app))
        await pilot.press("d")
        await pilot.pause()
        check("keys: `d` on the Images tab does nothing", not isinstance(app.screen, ConfirmScreen))

        await pilot.press("2")
        await app.workers.wait_for_complete()
        await pilot.pause()
        check("keys: the Clones tab offers the clone actions",
              {"b", "s", "e", "x", "d"} <= footer_keys(app))

        await pilot.press("question_mark")
        await pilot.pause()
        check("keys: ? shows the key panel", bool(app.screen.query(HelpPanel)))
        await pilot.press("question_mark")
        await pilot.pause()
        check("keys: ? again hides it", not app.screen.query(HelpPanel))

        # A slow action: while it runs, write keys are disabled and a second one is refused.
        release = threading.Event()
        slow = types.SimpleNamespace(
            ID="slow", LABEL="Slow thing", DESTRUCTIVE=False, REQUIRES_CLONE=False,
            handle=lambda ctx: (release.wait(10), actions_mod.ActionOutcome(
                ok=True, summary="slow done", needs_refresh=False))[1])
        from rhubarb.tui import actions as actions_mod
        app.dispatch_action(slow, name="demo")
        await pilot.pause()
        check("keys: a running action shows in the header", "running: Slow thing" in app.sub_title)
        check("keys: write keys are disabled while it runs",
              not {"d", "n", "B"} & footer_keys(app) and app.check_action("build", ()) is None)
        app.dispatch_action(slow, name="again")
        await pilot.pause()
        check("keys: a second action is refused meanwhile",
              "wait for Slow thing demo to finish" in _log_text(app))
        release.set()
        await app.workers.wait_for_complete()
        await pilot.pause()
        check("keys: the write keys come back when it finishes",
              {"d", "n", "B"} <= footer_keys(app) and "running" not in app.sub_title
              and "slow done" in _log_text(app))

        await pilot.press("1")
        await pilot.press("2")          # no manual focus: the tab key alone must reach the table
        await app.workers.wait_for_complete()
        await pilot.pause()
        check("keys: opening a tab puts the keyboard in its table",
              app.focused is app.query_one("#clones-table", DataTable))
        await pilot.press("enter")
        await app.workers.wait_for_complete()
        await pilot.pause()
        check("keys: Enter on a clone opens its image's provenance",
              app.query_one("TabbedContent").active == "provenance"
              and asked and asked[-1] == "rbt-kali-research-cc4479ae7492" and "work-1" not in asked)
        check("keys: ...and says which clone it came from",
              "(image of clone work-1)" in _static_text(app.query_one("#provenance-body")))


def _test_theme() -> None:
    """#131: the TUI follows herdr's theme: built-in names map to Textual themes, auto_switch
    picks dark_name/light_name, [theme.custom] tokens override colours (bad ones skipped), the
    config is found where herdr finds it, and it only applies inside herdr unless asked."""
    import tempfile

    from textual.theme import BUILTIN_THEMES

    from rhubarb.tui import theme as T

    check("theme: no config -> herdr's default (catppuccin -> catppuccin-mocha)",
          T.textual_theme({}).primary == BUILTIN_THEMES["catppuccin-mocha"].primary)
    check("theme: every herdr built-in resolves to a Textual theme",
          all(T.textual_theme({"theme": {"name": n}}).name == T.THEME_NAME
              for n in [*T.BUILTIN_MAP, *T.EXTRA_THEMES, "terminal"]))
    check("theme: a mapped name uses that Textual palette",
          T.textual_theme({"theme": {"name": "dracula"}}).background
          == BUILTIN_THEMES["dracula"].background)
    check("theme: a herdr theme Textual lacks is defined here (kanagawa)",
          T.textual_theme({"theme": {"name": "kanagawa"}}).background == "#1f1f28")
    check("theme: terminal -> Textual's ANSI theme for the appearance",
          T.textual_theme({"theme": {"name": "terminal"}}, "light").ansi
          and not T.textual_theme({"theme": {"name": "terminal"}}, "light").dark)
    check("theme: an unknown name falls back to herdr's default",
          T.textual_theme({"theme": {"name": "no-such"}}).primary
          == BUILTIN_THEMES["catppuccin-mocha"].primary)
    auto = {"theme": {"auto_switch": True, "dark_name": "nord", "light_name": "gruvbox-light"}}
    check("theme: auto_switch uses dark_name / light_name by appearance",
          T.herdr_theme_name(auto, "dark") == "nord"
          and T.herdr_theme_name(auto, "light") == "gruvbox-light")
    check("theme: without auto_switch, name wins over dark_name",
          T.herdr_theme_name({"theme": {"name": "vesper", "dark_name": "nord"}}) == "vesper")
    custom = {"theme": {"name": "nord", "auto_switch": True, "custom": {
        "accent": "#ff0000", "panel_bg": "reset", "text": "not-a-colour", "bogus": "#00ff00",
        "red": "rgb(1,2,3)", "dark": {"accent": "#00ff00"}, "light": {"accent": "#0000ff"}}}}
    o_dark, o_light = T.herdr_overrides(custom, "dark"), T.herdr_overrides(custom, "light")
    check("theme: custom tokens map to Textual fields; the appearance layer wins",
          o_dark.get("primary") == "#00ff00" and o_light.get("primary") == "#0000ff"
          and o_dark.get("error") == "rgb(1,2,3)")
    check("theme: reset, unknown tokens and unparsable colours are skipped",
          "background" not in o_dark and "foreground" not in o_dark and len(o_dark) == 2)
    check("theme: without auto_switch the dark/light layers are ignored",
          T.herdr_overrides({"theme": {"custom": {"accent": "#111111", "dark": {"accent": "#222222"}}}})
          == {"primary": "#111111"})
    check("theme: config path follows HERDR_CONFIG_PATH, then XDG_CONFIG_HOME",
          T.herdr_config_path({"HERDR_CONFIG_PATH": "/x/c.toml", "XDG_CONFIG_HOME": "/y"})
          == Path("/x/c.toml")
          and T.herdr_config_path({"XDG_CONFIG_HOME": "/y"}) == Path("/y/herdr/config.toml"))
    with tempfile.TemporaryDirectory() as d:
        bad = Path(d) / "config.toml"
        bad.write_text("this is [not toml")
        check("theme: a malformed config reads as herdr's defaults",
              T.load_herdr_config({"HERDR_CONFIG_PATH": str(bad)}) == {})
        good = Path(d) / "good.toml"
        good.write_text('[theme]\nname = "dracula"\n')
        env = {"HERDR_PANE_ID": "w1:p1", "HERDR_CONFIG_PATH": str(good)}
        chosen = T.choose_theme([], env)
        check("theme: inside herdr, the synced theme is chosen",
              getattr(chosen, "background", None) == BUILTIN_THEMES["dracula"].background)
        check("theme: outside herdr, Textual's default stays", T.choose_theme([], {}) is None)
        check("theme: --theme / RHUBARB_TUI_THEME override the sync",
              T.choose_theme(["--theme", "nord"], env) == "nord"
              and T.choose_theme(["--theme=gruvbox"], env) == "gruvbox"
              and T.choose_theme([], {**env, "RHUBARB_TUI_THEME": "flexoki"}) == "flexoki")


async def _theme_app() -> None:
    """#131: the running app adopts the chosen theme; a bad name is logged, not fatal."""
    from rhubarb.tui import theme as T

    api.images = _mock_images
    api.clones = _mock_clones
    api.provenance = _mock_provenance
    api.list_logs = _mock_list_logs
    api.tail_log = _mock_tail_log
    from rhubarb_tui import RhubarbTUI

    app = RhubarbTUI(theme_choice=T.textual_theme({"theme": {"name": "vesper"}}))
    async with app.run_test() as pilot:
        await pilot.pause()
        check("theme app: herdr's theme is registered and active",
              app.theme == T.THEME_NAME
              and app.current_theme.background == T.EXTRA_THEMES["vesper"].background)
    app = RhubarbTUI(theme_choice="no-such-theme")
    async with app.run_test() as pilot:
        await pilot.pause()
        check("theme app: an unknown --theme is reported and the default stays",
              app.theme == "textual-dark" and "theme not applied" in _log_text(app))


def _test_mouse() -> None:
    """Inside herdr the TUI leaves the mouse to herdr; flags and env override."""
    from rhubarb_tui import mouse_enabled

    check("mouse: on in a plain terminal", mouse_enabled([], {}))
    check("mouse: off inside herdr", not mouse_enabled([], {"HERDR_PANE_ID": "w1:p1"}))
    check("mouse: --mouse forces it on inside herdr",
          mouse_enabled(["--mouse"], {"HERDR_PANE_ID": "w1:p1"}))
    check("mouse: --no-mouse forces it off", not mouse_enabled(["--no-mouse"], {}))
    check("mouse: RHUBARB_TUI_MOUSE=1 turns it on inside herdr",
          mouse_enabled([], {"HERDR_PANE_ID": "w1:p1", "RHUBARB_TUI_MOUSE": "1"}))
    check("mouse: RHUBARB_TUI_MOUSE=0 turns it off", not mouse_enabled([], {"RHUBARB_TUI_MOUSE": "0"}))


async def _selection_survives() -> None:
    """The highlighted row stays put across refreshes — when nothing changed, when a row's
    values changed, and (Logs) when the list reorders."""
    import dataclasses

    from textual.widgets import DataTable

    images = list(MOCK_IMAGES)
    clones = MOCK_CLONES
    logs = {
        "logs/build-a.log": (api.LogRef(id="logs/build-a.log", label="build a", kind="build",
                                        size=10, mtime="2026-09-30T12:00:00+00:00"), "build a out"),
        "logs/work-1.log": (api.LogRef(id="logs/work-1.log", label="work-1", kind="clone",
                                       size=10, mtime="2026-09-30T11:00:00+00:00"), "work-1 out"),
    }
    api.images = lambda: images
    api.clones = lambda: clones
    api.provenance = _mock_provenance
    api.list_logs = lambda: [r for r, _ in sorted(logs.values(), key=lambda v: v[0].mtime,
                                                  reverse=True)]
    api.tail_log = _tail(lambda log_id: logs[log_id][1])

    def key(app, table_id):
        t = app.query_one(table_id, DataTable)
        return t.coordinate_to_cell_key(t.cursor_coordinate).row_key.value

    def at_cursor(app, table_id):
        """The first cell of the row under the cursor (works whether or not rows have keys)."""
        t = app.query_one(table_id, DataTable)
        return _text(t.get_row_at(t.cursor_row)[0])

    from rhubarb_tui import RhubarbTUI

    app = RhubarbTUI()
    async with app.run_test() as pilot:
        await pilot.pause()

        # Images: pick the third row, refresh, change a row, refresh.
        app.query_one("#images-table", DataTable).focus()
        await pilot.press("down", "down")
        await pilot.pause()
        picked = at_cursor(app, "#images-table")
        check("images: third row picked", picked == MOCK_IMAGES[2].name)
        await pilot.press("r")
        await pilot.pause()
        check("images: selection kept across a refresh", at_cursor(app, "#images-table") == picked)
        images[0] = dataclasses.replace(images[0], clones=7)
        await pilot.press("r")
        await pilot.pause()
        check("images: selection kept when rows change", at_cursor(app, "#images-table") == picked)

        # Clones: pick the second row, then its state changes.
        await pilot.press("2")
        await pilot.pause()
        app.query_one("#clones-table", DataTable).focus()
        await pilot.press("down")
        await pilot.pause()
        check("clones: second row picked", key(app, "#clones-table") == "work-2")
        clones = api.CloneList(clones=[clones.clones[0],
                                       dataclasses.replace(clones.clones[1], state="running")],
                               problems=clones.problems)
        await pilot.press("r")
        await pilot.pause()
        check("clones: selection kept when rows change", key(app, "#clones-table") == "work-2")

        # Logs: pick work-1, then it becomes the newest (the list reorders), then grows in place.
        await pilot.press("4")
        await app.workers.wait_for_complete()
        await pilot.pause()
        app.query_one("#logs-table", DataTable).focus()
        await pilot.press("down")
        await pilot.pause()
        check("logs: work-1 picked", key(app, "#logs-table") == "logs/work-1.log")
        # The newest log (the top row) keeps growing while an older one is being read: the
        # reader must not be pulled back to the top.
        ref, _ = logs["logs/build-a.log"]
        logs["logs/build-a.log"] = (dataclasses.replace(ref, size=20,
                                                        mtime="2026-09-30T12:30:00+00:00"),
                                    "build a out\nstep 2")
        await pilot.press("r")
        await pilot.pause()
        check("logs: reading an older log while the newest grows keeps the selection",
              key(app, "#logs-table") == "logs/work-1.log" and "work-1 out" in _view_text(app))
        ref, _ = logs["logs/work-1.log"]
        logs["logs/work-1.log"] = (dataclasses.replace(ref, size=20,
                                                       mtime="2026-09-30T13:00:00+00:00"),
                                   "work-1 out\nmore")
        await pilot.press("r")
        await pilot.pause()
        check("logs: selection follows the log when the list reorders",
              key(app, "#logs-table") == "logs/work-1.log" and "more" in _view_text(app))
        ref, _ = logs["logs/work-1.log"]
        logs["logs/work-1.log"] = (dataclasses.replace(ref, size=30,
                                                       mtime="2026-09-30T13:30:00+00:00"),
                                   "work-1 out\nmore\nand more")
        await pilot.press("r")
        await pilot.pause()
        check("logs: selection kept when a log grows in place",
              key(app, "#logs-table") == "logs/work-1.log" and "and more" in _view_text(app))


async def _render() -> None:
    # Mock the core API before the app mounts (each pane calls api.* in refresh_data).
    api.images = _mock_images
    api.clones = _mock_clones
    api.provenance = _mock_provenance
    api.list_logs = _mock_list_logs
    api.tail_log = _mock_tail_log

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

        # -- provenance pane (lazy: populates when its tab is shown) ------------
        # The shell remembers the highlighted row's provenance source but only *reads* it when
        # the Provenance tab is visible, so navigating images/clones stays fast. On mount the
        # images tab is active, so provenance is still blank here.
        prov = app.query_one("#provenance-pane")
        body_mount = _static_text(app.query_one("#provenance-body", Static))
        check("provenance is blank until its tab is shown (lazy — off the navigation hot path)",
              "No VM selected" in body_mount)

        # Switching to the Provenance tab reads the remembered highlight (the first image).
        await pilot.press("3")
        await pilot.pause()
        body_tab = _static_text(app.query_one("#provenance-body", Static))
        check("switching to the Provenance tab populates it from the highlighted image",
              "No VM selected" not in body_tab and "work-1" in body_tab)

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


# ---- Stage C: action infrastructure + the wired `rm` exemplar ------------------------------
# The panes stay read-only; write actions are dispatched by the shell through the
# rhubarb.tui.actions modules. These tests pin the contract the per-action agents build on:
# the guard (require_clone), the handler signature (ActionContext -> ActionOutcome), and the
# confirm-modal -> worker -> refresh flow, all against a mocked core API (no Mac/tart/keychain).


def _test_guard() -> None:
    from rhubarb.tui import actions

    def refuses(label, name):
        try:
            actions.require_clone(name)
            check(label, False)
        except actions.NotAClone:
            check(label, True)

    refuses("guard refuses a built image (rbt-*)", "rbt-kali-research-cc4479ae7492")
    refuses("guard refuses a vanilla precursor", "macos-tahoe-vanilla")
    refuses("guard refuses an unverified precursor", "rbt-kali-research-cc4479ae7492-unverified")
    refuses("guard refuses an empty target", "")
    check("guard allows a managed clone name", actions.require_clone("work-1") == "work-1")


def _test_rm_handler() -> None:
    """rm.handle honours the contract: calls api.rm, streams progress, returns an outcome."""
    from rhubarb.tui import actions
    from rhubarb.tui.actions import rm

    seen: list = []
    calls: list = []
    api.rm = lambda name: (calls.append(name),
                           api.RemoveResult(name=name, image="rbt-kali-research-cc4479ae7492",
                                            keychain_deleted=True))[1]
    ctx = actions.ActionContext(name="work-1", clone=MOCK_CLONES.clones[0],
                                progress=seen.append)
    outcome = rm.handle(ctx)
    check("rm.handle calls api.rm with the target name", calls == ["work-1"])
    check("rm.handle returns a successful ActionOutcome", outcome.ok and "removed" in outcome.summary)
    check("rm.handle streams progress via ctx.progress", any("work-1" in m for m in seen))
    check("rm module is marked destructive + clone-targeted", rm.DESTRUCTIVE and rm.REQUIRES_CLONE)


async def _actions() -> None:
    # Mock the core so a clone row exists and rm is observable (no tart/keychain).
    rm_calls: list = []
    api.images = _mock_images
    api.clones = _mock_clones
    api.provenance = _mock_provenance
    api.list_logs = _mock_list_logs
    api.tail_log = _mock_tail_log
    api.rm = lambda name: (rm_calls.append(name),
                           api.RemoveResult(name=name, image="rbt-kali-research-cc4479ae7492",
                                            keychain_deleted=True))[1]

    import rhubarb_tui
    from rhubarb.tui.confirm import ConfirmScreen

    app = rhubarb_tui.RhubarbTUI()
    async with app.run_test() as pilot:
        await pilot.pause()

        check("app mounts the action log (write surface)",
              app.query_one("#action-log") is not None)

        # Guard at dispatch: a non-clone target is refused, nothing runs, no modal.
        from rhubarb.tui.actions import rm as rm_mod
        app.dispatch_action(rm_mod, name="rbt-kali-research-cc4479ae7492")
        await pilot.pause()
        check("dispatch refuses a non-clone target (no modal, no api.rm)",
              not isinstance(app.screen, ConfirmScreen) and rm_calls == [])

        # Exemplar end-to-end: select a clone, press the rm key -> confirm modal appears.
        app.query_one("#clones-table").focus()
        await pilot.pause()
        await pilot.press("2")   # Clones tab
        await pilot.pause()
        await pilot.press("d")   # remove-clone binding
        await pilot.pause()
        check("destructive action opens the confirm modal first",
              isinstance(app.screen, ConfirmScreen))

        # Cancelling (n) must NOT run the action.
        await pilot.press("n")
        await pilot.pause()
        check("declining the modal runs nothing",
              not isinstance(app.screen, ConfirmScreen) and rm_calls == [])

        # Confirming (y) runs api.rm in the worker and dismisses the modal.
        await pilot.press("d")
        await pilot.pause()
        await pilot.press("y")
        await app.workers.wait_for_complete()
        await pilot.pause()
        check("confirming runs api.rm on the selected clone in a worker",
              rm_calls == ["work-1"])
        check("the confirm modal is dismissed after the action", not isinstance(app.screen, ConfirmScreen))


def _test_action_handlers() -> None:
    """Every action module dispatches to the correct ``api.*`` with the expected args.

    Each ``handle`` is called directly (they are synchronous and run off the UI thread in
    the app), against a mocked core API — so this asserts the exact core call, its
    arguments, the streamed progress and the returned ``ActionOutcome`` without a Mac,
    ``tart`` or the keychain. No action reaches ``tart``/keychain except through ``api.*``.
    """
    from rhubarb.tui import actions
    from rhubarb.tui.actions import build, enroll, new, reset, run, ssh

    clone = MOCK_CLONES.clones[0]

    # -- run: api.run(name, headless=, detach=) -------------------------------------------
    run_calls: list = []

    def _fake_run(name, headless=False, detach=False):
        run_calls.append((name, headless, detach))
        return api.RunResult(name=name, detached=detach,
                             argv=None if detach else ["tart", "run", name],
                             log_path="/state/logs/x.log" if detach else None)
    api.run = _fake_run
    seen: list = []
    out = run.handle(actions.ActionContext(name="work-1", clone=clone,
                                           params={"headless": True, "detach": True},
                                           progress=seen.append))
    check("run.handle calls api.run with headless+detach from params",
          run_calls == [("work-1", True, True)])
    check("run.handle (detached) reports success and asks for a refresh",
          out.ok and out.needs_refresh and "background" in out.summary)
    run_calls.clear()
    out = run.handle(actions.ActionContext(name="work-1", clone=clone, progress=seen.append))
    check("run.handle (foreground) returns the tart argv as exec_argv, no refresh",
          run_calls == [("work-1", False, False)] and out.exec_argv == ["tart", "run", "work-1"]
          and not out.needs_refresh and out.ok)

    # -- ssh: api.ssh_args(name) ----------------------------------------------------------
    ssh_calls: list = []

    def _fake_ssh_args(name):
        ssh_calls.append(name)
        return api.SSHArgs(name=name, username="researcher", ip="192.168.64.9",
                           argv=["ssh", "-i", "/x/key", "researcher@192.168.64.9"])
    api.ssh_args = _fake_ssh_args
    out = ssh.handle(actions.ActionContext(name="work-1", clone=clone, progress=seen.append))
    check("ssh.handle calls api.ssh_args with the clone name", ssh_calls == ["work-1"])
    check("ssh.handle returns the pinned ssh argv as exec_argv, no refresh",
          out.ok and out.exec_argv == ["ssh", "-i", "/x/key", "researcher@192.168.64.9"]
          and not out.needs_refresh)

    # -- enroll: api.enroll(name, service, org=) ------------------------------------------
    en_calls: list = []

    def _fake_enroll(name, service, org=None):
        en_calls.append((name, service, org))
        return api.EnrollResult(name=name, service=service, recorded=True,
                                enrolled_at="2026-01-04T00:00:00Z")
    api.enroll = _fake_enroll
    out = enroll.handle(actions.ActionContext(name="work-1", clone=clone,
                                              params={"service": "warp", "org": "acme"},
                                              progress=seen.append))
    check("enroll.handle calls api.enroll with the service and org from params",
          en_calls == [("work-1", "warp", "acme")])
    check("enroll.handle records success and asks for a refresh",
          out.ok and out.needs_refresh and "warp" in out.summary)
    api.enroll = lambda name, service, org=None: api.EnrollResult(
        name=name, service=service, recorded=False, enrolled_at=None)
    out = enroll.handle(actions.ActionContext(name="work-1", clone=clone,
                                              params={"service": "tailscale"}, progress=seen.append))
    check("enroll.handle (core reports not recorded) -> ok, no refresh, manual note",
          out.ok and not out.needs_refresh and "manual" in out.summary.lower())
    out = enroll.handle(actions.ActionContext(name="work-1", clone=clone, progress=seen.append))
    check("enroll.handle refuses when no service was chosen", not out.ok and not out.needs_refresh)

    # -- new: api.new(name, profile=, image=, rotate=, progress=) --------------------------
    new_calls: list = []

    def _fake_new(name, profile=None, image=None, rotate=True, progress=None):
        new_calls.append((name, profile, image, rotate))
        if progress:  # #19: the core streams milestones through the passed callback
            progress(f"cloned rbt-x -> {name}")
        return api.NewResult(name=name, image="rbt-kali-research-cc4479ae7492",
                             profile=profile or "kali-research", rotated=rotate,
                             password_mode="unique" if rotate else "inherited", note=None)
    api.new = _fake_new
    seen = []
    out = new.handle(actions.ActionContext(name="fresh-1",
                                           params={"profile": "kali-research", "rotate": True},
                                           progress=seen.append))
    check("new.handle calls api.new with name/profile/rotate (image None)",
          new_calls == [("fresh-1", "kali-research", None, True)])
    check("new.handle forwards ctx.progress into api.new so #19 milestones stream",
          any("cloned rbt-x -> fresh-1" == m for m in seen))
    check("new.handle returns a successful outcome naming the new clone",
          out.ok and "created fresh-1" in out.summary)

    # -- reset: api.reset(name, same_image=, rotate=, progress=) ---------------------------
    reset_calls: list = []

    def _fake_reset(name, same_image=False, rotate=True, progress=None):
        reset_calls.append((name, same_image, rotate))
        if progress:  # #19: teardown line + re-clone milestones stream through the callback
            progress(f"{name}: destroyed (enrollment and identity are gone); re-cloning")
        return api.NewResult(name=name, image="rbt-kali-research-cc4479ae7492",
                             profile="kali-research", rotated=rotate,
                             password_mode="unique" if rotate else "inherited", note=None)
    api.reset = _fake_reset
    seen = []
    out = reset.handle(actions.ActionContext(name="work-1", clone=clone,
                                             params={"same_image": True, "rotate": False},
                                             progress=seen.append))
    check("reset.handle calls api.reset with same_image+rotate from params",
          reset_calls == [("work-1", True, False)])
    check("reset.handle forwards ctx.progress into api.reset so #19 teardown streams",
          any("destroyed" in m for m in seen))
    check("reset module is destructive + clone-targeted", reset.DESTRUCTIVE and reset.REQUIRES_CLONE)
    prompt = reset.confirm_prompt(actions.ActionContext(name="work-1", clone=clone,
                                                        params={"same_image": True}))
    check("reset.confirm_prompt names the clone and warns it cannot be undone",
          "work-1" in prompt and "cannot be undone" in prompt)

    # -- build: shells out to the audited scripts/build.sh; never over SSH -----------------
    api.list_profiles = lambda: ["kali-research", "nixos-research"]
    out = build.handle(actions.ActionContext(name="", params={"profile": "nope"}, progress=seen.append))
    check("build.handle refuses an unknown profile", not out.ok and "unknown profile" in out.summary)
    out = build.handle(actions.ActionContext(name="", params={}, progress=seen.append))
    check("build.handle refuses with no profile chosen", not out.ok)
    saved_env = {k: os.environ.get(k) for k in ("SSH_CONNECTION", "SSH_TTY")}
    os.environ["SSH_CONNECTION"] = "1.2.3.4 5 6.7.8.9 22"
    try:
        out = build.handle(actions.ActionContext(name="", params={"profile": "kali-research"},
                                                 progress=seen.append))
    finally:
        for k, v in saved_env.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
    check("build.handle refuses to launch a build over an SSH session (GUI-session rule)",
          not out.ok and "SSH session" in out.summary)
    check("build.confirm_prompt shouts the GUI Terminal.app caveat",
          "GUI Terminal.app" in build.confirm_prompt(
              actions.ActionContext(name="", params={"profile": "kali-research"})))
    check("build module is non-destructive and not clone-targeted",
          not build.DESTRUCTIVE and not build.REQUIRES_CLONE)


async def _action_writes() -> None:
    """End-to-end through the shell (mock API): destructive gating, correct core calls, and
    #19 progress streaming into the status area — no Mac/tart/keychain.

    Covers the hard rules: a destructive action does NOTHING until the confirm modal is
    accepted (and then calls the core exactly once with the expected args); the non-clone
    guard blocks a vanilla/rbt-* target before any modal; and a reset/new run streams the
    core's #19 milestones into the action log.
    """
    reset_calls: list = []
    new_calls: list = []
    api.images = _mock_images
    api.clones = _mock_clones
    api.provenance = _mock_provenance
    api.list_logs = _mock_list_logs
    api.tail_log = _mock_tail_log

    def _fake_reset(name, same_image=False, rotate=True, progress=None):
        reset_calls.append((name, same_image, rotate))
        if progress:
            progress(f"{name}: destroyed (enrollment and identity are gone); re-cloning from "
                     "rbt-kali-research-cc4479ae7492")
            progress(f"{name}: unique password set")
        return api.NewResult(name=name, image="rbt-kali-research-cc4479ae7492",
                             profile="kali-research", rotated=rotate,
                             password_mode="unique" if rotate else "inherited", note=None)

    def _fake_new(name, profile=None, image=None, rotate=True, progress=None):
        new_calls.append((name, profile, image, rotate))
        if progress:
            progress(f"cloned rbt-kali-research-cc4479ae7492 -> {name}")
        return api.NewResult(name=name, image="rbt-kali-research-cc4479ae7492",
                             profile=profile or "kali-research", rotated=rotate,
                             password_mode="unique" if rotate else "inherited", note=None)
    api.reset = _fake_reset
    api.new = _fake_new

    import rhubarb_tui
    from rhubarb.tui.actions import new as new_mod
    from rhubarb.tui.actions import reset as reset_mod
    from rhubarb.tui.confirm import ConfirmScreen

    app = rhubarb_tui.RhubarbTUI()
    async with app.run_test() as pilot:
        await pilot.pause()

        # Non-clone guard blocks the destructive reset before any modal appears.
        app.dispatch_action(reset_mod, name="macos-tahoe-vanilla")
        await pilot.pause()
        check("reset: non-clone guard refuses a vanilla target (no modal, no api.reset)",
              not isinstance(app.screen, ConfirmScreen) and reset_calls == [])

        # Destructive reset opens the confirm modal and does nothing until accepted.
        params = {"same_image": True, "rotate": False}
        app.dispatch_action(reset_mod, name="work-1", clone=MOCK_CLONES.clones[0], params=params)
        await pilot.pause()
        check("reset: opens the confirm modal before touching the core",
              isinstance(app.screen, ConfirmScreen) and reset_calls == [])
        await pilot.press("n")  # decline
        await pilot.pause()
        check("reset: declining the modal calls no api.reset",
              not isinstance(app.screen, ConfirmScreen) and reset_calls == [])

        # Accepting runs the reset in a worker, exactly once, with the expected args.
        app.dispatch_action(reset_mod, name="work-1", clone=MOCK_CLONES.clones[0], params=params)
        await pilot.pause()
        await pilot.press("y")  # confirm
        await app.workers.wait_for_complete()
        await pilot.pause()
        check("reset: confirming calls api.reset once with same_image+rotate",
              reset_calls == [("work-1", True, False)])
        check("reset: the core's #19 progress streams into the status area (action log)",
              "re-cloning from" in _log_text(app))

        # new is non-destructive: it runs directly and streams its #19 milestones live.
        app.dispatch_action(new_mod, name="fresh-1", params={"profile": "kali-research", "rotate": True})
        await app.workers.wait_for_complete()
        await pilot.pause()
        check("new: calls api.new with the requested name/profile/rotate",
              new_calls == [("fresh-1", "kali-research", None, True)])
        check("new: the core's #19 progress streams into the status area (action log)",
              "cloned rbt-kali-research-cc4479ae7492 -> fresh-1" in _log_text(app))


async def _action_prompts() -> None:
    """The input-driven write actions (new/enroll/build) end-to-end through the shell's
    prompt modals — pressing the binding, driving the SelectScreen/InputScreen, and
    asserting the exact ``api.*`` call (or none, on cancel) — with a mocked core API
    (no Mac/tart/keychain).

    Covers the hard rules for the modal-gated writes: everything goes through
    ``dispatch_action`` -> the core; cancelling any modal (Escape) makes no ``api.*``
    call and logs a cancel line; and the highlighted clone (read-only table) is the
    enroll target. It complements ``_action_writes`` (which drives reset/new via
    ``dispatch_action`` directly) by exercising the ``prompt`` modals themselves.
    """
    from textual.widgets import Input

    api.images = _mock_images
    api.clones = _mock_clones
    api.provenance = _mock_provenance
    api.list_logs = _mock_list_logs
    api.tail_log = _mock_tail_log
    api.list_profiles = lambda: ["kali-research", "nixos-research"]

    new_calls: list = []
    enroll_calls: list = []

    def _fake_new(name, profile=None, image=None, rotate=True, progress=None):
        new_calls.append((name, profile, image, rotate))
        if progress:
            progress(f"cloned rbt-x -> {name}")
        return api.NewResult(name=name, image="rbt-kali-research-cc4479ae7492",
                             profile=profile or "kali-research", rotated=rotate,
                             password_mode="unique" if rotate else "inherited", note=None)

    def _fake_enroll(name, service, org=None):
        enroll_calls.append((name, service, org))
        return api.EnrollResult(name=name, service=service, recorded=True,
                                enrolled_at="2026-01-04T00:00:00Z")
    api.new = _fake_new
    api.enroll = _fake_enroll

    import rhubarb_tui
    from rhubarb.tui import actions
    from rhubarb.tui.actions import build as build_mod
    from rhubarb.tui.confirm import ConfirmScreen
    from rhubarb.tui.prompt import InputScreen, SelectScreen

    # build shells out to scripts/build.sh; intercept its handler so the test asserts the
    # shell wiring (binding -> profile picker -> dispatch) without launching a real build.
    build_calls: list = []
    saved_build_handle = build_mod.handle

    def _fake_build_handle(ctx):
        build_calls.append((ctx.name, (ctx.params or {}).get("profile")))
        return actions.ActionOutcome(ok=True, summary=f"build {ctx.params.get('profile')} finished")

    app = rhubarb_tui.RhubarbTUI()
    async with app.run_test() as pilot:
        await pilot.pause()

        # -- new: n -> profile SelectScreen -> name InputScreen -> api.new once --------------
        await pilot.press("n")
        await pilot.pause()
        check("new: pressing 'n' opens the profile SelectScreen",
              isinstance(app.screen, SelectScreen))
        await pilot.press("enter")  # pick the highlighted (first, sorted) profile: kali-research
        await pilot.pause()
        check("new: choosing a profile opens the name InputScreen",
              isinstance(app.screen, InputScreen))
        app.screen.query_one("#input-field", Input).value = "fresh-9"
        await pilot.press("enter")
        await app.workers.wait_for_complete()
        await pilot.pause()
        check("new: driving the modals calls api.new once with the typed name + chosen "
              "profile + rotate True",
              new_calls == [("fresh-9", "kali-research", None, True)])
        check("new: no modal is left on screen after the flow",
              not isinstance(app.screen, (SelectScreen, InputScreen)))

        # -- new cancel: n -> Escape the profile picker -> no api.new, logs a cancel ---------
        new_calls.clear()
        await pilot.press("n")
        await pilot.pause()
        check("new (cancel): the profile SelectScreen is up", isinstance(app.screen, SelectScreen))
        await pilot.press("escape")
        await pilot.pause()
        check("new (cancel): Escape makes no api.new call", new_calls == [])
        check("new (cancel): a cancel line is logged", "cancelled" in _log_text(app))

        # -- enroll (warp): highlight a clone -> e -> service picker -> org input -> api.enroll
        await pilot.press("2")  # Clones tab
        await pilot.pause()
        app.query_one("#clones-table").focus()
        await pilot.pause()
        await pilot.press("e")
        await pilot.pause()
        check("enroll: pressing 'e' on a highlighted clone opens the service SelectScreen",
              isinstance(app.screen, SelectScreen))
        await pilot.press("down")   # highlight index 1: warp (SERVICES = tailscale, warp)
        await pilot.press("enter")
        await pilot.pause()
        check("enroll: choosing warp opens the required team/org InputScreen",
              isinstance(app.screen, InputScreen))
        app.screen.query_one("#input-field", Input).value = "acme"
        await pilot.press("enter")
        await app.workers.wait_for_complete()
        await pilot.pause()
        check("enroll (warp): api.enroll is called with the highlighted clone, service and org",
              enroll_calls == [("work-1", "warp", "acme")])

        # -- enroll (warp, blank org): required -> no api.enroll, logs a clear message --------
        enroll_calls.clear()
        await pilot.press("e")
        await pilot.pause()
        await pilot.press("down")   # warp
        await pilot.press("enter")
        await pilot.pause()
        app.screen.query_one("#input-field", Input).value = ""  # leave org blank
        await pilot.press("enter")
        await pilot.pause()
        check("enroll (warp, blank org): no api.enroll call and a clear 'needs a team/org' log",
              enroll_calls == [] and "team/org" in _log_text(app).lower())

        # -- enroll (tailscale): no org prompt -> dispatches immediately ---------------------
        enroll_calls.clear()
        await pilot.press("e")
        await pilot.pause()
        check("enroll (tailscale): service picker is up again", isinstance(app.screen, SelectScreen))
        await pilot.press("enter")  # highlighted index 0: tailscale (no org prompt)
        await app.workers.wait_for_complete()
        await pilot.pause()
        check("enroll (tailscale): api.enroll is called with org None and no org prompt shown",
              enroll_calls == [("work-1", "tailscale", None)]
              and not isinstance(app.screen, InputScreen))

        # -- enroll cancel: e -> Escape the service picker -> no api.enroll ------------------
        enroll_calls.clear()
        await pilot.press("e")
        await pilot.pause()
        await pilot.press("escape")
        await pilot.pause()
        check("enroll (cancel): Escaping the service picker makes no api.enroll call",
              enroll_calls == [] and not isinstance(app.screen, SelectScreen))

        # -- build: B -> profile SelectScreen -> ACK modal (GUI-session caveat) -> handler ----
        build_mod.handle = _fake_build_handle
        try:
            await pilot.press("B")
            await pilot.pause()
            check("build: pressing 'B' opens the profile SelectScreen",
                  isinstance(app.screen, SelectScreen))
            await pilot.press("enter")  # pick kali-research
            await pilot.pause()
            check("build: choosing a profile opens the acknowledgement modal (REQUIRES_ACK)",
                  isinstance(app.screen, ConfirmScreen))
            await pilot.press("y")  # acknowledge the GUI-session caveat
            await app.workers.wait_for_complete()
            await pilot.pause()
            check("build: acknowledging runs the build handler with that profile",
                  build_calls == [("", "kali-research")])

            # build cancel: B -> Escape the profile picker -> the handler never runs.
            build_calls.clear()
            await pilot.press("B")
            await pilot.pause()
            await pilot.press("escape")
            await pilot.pause()
            check("build (cancel): Escaping the profile picker never runs the build handler",
                  build_calls == [] and "cancelled" in _log_text(app))

            # build ack-cancel: B -> pick profile -> decline the ack -> the handler never runs.
            build_calls.clear()
            await pilot.press("B")
            await pilot.pause()
            await pilot.press("enter")  # pick the profile
            await pilot.pause()
            check("build (ack-cancel): the acknowledgement modal is up",
                  isinstance(app.screen, ConfirmScreen))
            await pilot.press("n")  # decline
            await pilot.pause()
            check("build (ack-cancel): declining the ack never runs the build handler",
                  build_calls == [])
        finally:
            build_mod.handle = saved_build_handle


def main() -> None:
    asyncio.run(_render())
    _test_mouse()
    _test_theme()
    asyncio.run(_theme_app())
    _test_guard()
    _test_rm_handler()
    _test_action_handlers()
    asyncio.run(_actions())
    asyncio.run(_action_writes())
    asyncio.run(_action_prompts())
    asyncio.run(_logs_tab())
    asyncio.run(_follow_log())
    asyncio.run(_keys())
    asyncio.run(_polling())
    asyncio.run(_selection_survives())
    if FAILS:
        sys.exit(f"{len(FAILS)} TUI render test(s) failed")
    print("all rhubarb TUI render tests passed")


if __name__ == "__main__":
    main()
