"""Reusable input modals for the RhubarbTart TUI (Phase 0.5, Stage C).

These mirror :class:`rhubarb.tui.confirm.ConfirmScreen`: each is a ``ModalScreen``
you push and read through the ``push_screen`` callback. Where ``ConfirmScreen``
returns a ``bool``, these return the operator's answer as ``str`` — or ``None`` when
the operator cancels (Escape), so the caller can abort cleanly without ever calling
the core. Chain them the way the app chains confirmations: push one, and in its
callback push the next.

    def got_name(name: str | None) -> None:
        if name:
            self.dispatch_action(new, name=name, params=...)
    self.push_screen(InputScreen("new clone name"), got_name)

The input-driven write actions (``new`` / ``enroll`` / ``build``) need an operator to
pick a profile/service and/or type a name before dispatch — a UI concern the action
handlers (which run off the UI thread) cannot do. That selection/text gathering lives
here; the handlers still validate authoritatively in the core.

Keyboard: Enter submits/picks, Escape cancels. Stdlib + textual only.
"""

from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Grid
from textual.screen import ModalScreen
from textual.widgets import Input, Label, OptionList


class InputScreen(ModalScreen[str | None]):
    """A modal single-line text prompt.

    Returns the typed text on Enter (empty string included — the caller decides
    whether empty is meaningful, e.g. an optional ``--org``), or ``None`` on Escape.
    """

    DEFAULT_CSS = """
    InputScreen {
        align: center middle;
    }
    InputScreen #input-dialog {
        grid-size: 1;
        grid-gutter: 1 2;
        grid-rows: auto 3;
        padding: 1 2;
        width: 64;
        height: auto;
        max-width: 90%;
        border: thick $panel;
        background: $surface;
    }
    InputScreen #input-prompt {
        height: auto;
        content-align: left top;
        padding: 0 0 1 0;
    }
    InputScreen Input {
        width: 100%;
    }
    """

    BINDINGS = [
        Binding("escape", "cancel", "Cancel"),
    ]

    def __init__(self, prompt: str, initial: str = "") -> None:
        super().__init__()
        self._prompt = prompt
        self._initial = initial

    def compose(self) -> ComposeResult:
        with Grid(id="input-dialog"):
            yield Label(self._prompt, id="input-prompt")
            yield Input(value=self._initial, id="input-field")

    def on_mount(self) -> None:
        self.query_one("#input-field", Input).focus()

    def on_input_submitted(self, event: Input.Submitted) -> None:
        """Enter submits the current text."""
        self.dismiss(event.value)

    def action_cancel(self) -> None:
        self.dismiss(None)


class SelectScreen(ModalScreen[str | None]):
    """A modal single-choice picker.

    Returns the chosen string on Enter, or ``None`` on Escape. ``choices`` is a
    non-empty list of the exact strings to pick from (profiles, services, …).
    """

    DEFAULT_CSS = """
    SelectScreen {
        align: center middle;
    }
    SelectScreen #select-dialog {
        grid-size: 1;
        grid-gutter: 1 2;
        grid-rows: auto 1fr;
        padding: 1 2;
        width: 64;
        height: auto;
        max-height: 80%;
        max-width: 90%;
        border: thick $panel;
        background: $surface;
    }
    SelectScreen #select-prompt {
        height: auto;
        content-align: left top;
        padding: 0 0 1 0;
    }
    SelectScreen OptionList {
        height: auto;
        max-height: 16;
    }
    """

    BINDINGS = [
        Binding("escape", "cancel", "Cancel"),
    ]

    def __init__(self, prompt: str, choices: list[str]) -> None:
        super().__init__()
        self._prompt = prompt
        self._choices = list(choices)

    def compose(self) -> ComposeResult:
        with Grid(id="select-dialog"):
            yield Label(self._prompt, id="select-prompt")
            # markup=False: option text is a plain profile/service name, rendered verbatim.
            yield OptionList(*self._choices, id="select-list", markup=False)

    def on_mount(self) -> None:
        self.query_one("#select-list", OptionList).focus()

    def on_option_list_option_selected(self, event: OptionList.OptionSelected) -> None:
        """Enter picks the highlighted option; return the exact choice string."""
        self.dismiss(self._choices[event.option_index])

    def action_cancel(self) -> None:
        self.dismiss(None)
