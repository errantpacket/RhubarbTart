"""A reusable confirmation modal for destructive TUI actions (Phase 0.5, Stage C).

``ConfirmScreen`` is a ``ModalScreen[bool]``: push it and read the boolean it
returns through the ``push_screen`` callback — ``True`` only on an explicit yes.
Destructive actions (``rm``, ``reset``) MUST go through this before the core is
called; the core enforces its own guards regardless, but nothing destructive ever
runs from the UI without this in-TUI confirmation.

    self.push_screen(ConfirmScreen("Remove clone work-1?", action_label="Remove"),
                     lambda ok: ok and self._start(...))

Keyboard: ``y`` confirms, ``n`` / ``escape`` cancels; buttons do the same.
"""

from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Grid
from textual.screen import ModalScreen
from textual.widgets import Button, Static


class ConfirmScreen(ModalScreen[bool]):
    """A modal yes/no confirmation. Returns ``True`` only on an explicit confirm."""

    DEFAULT_CSS = """
    ConfirmScreen {
        align: center middle;
    }
    ConfirmScreen #confirm-dialog {
        grid-size: 2;
        grid-gutter: 1 2;
        grid-rows: 1fr 3;
        padding: 1 2;
        width: 64;
        height: auto;
        max-width: 90%;
        border: thick $panel;
        background: $surface;
    }
    ConfirmScreen #confirm-prompt {
        column-span: 2;
        height: auto;
        content-align: left top;
        padding: 0 0 1 0;
    }
    ConfirmScreen Button {
        width: 100%;
    }
    """

    BINDINGS = [
        Binding("y", "confirm", "Yes"),
        Binding("n,escape", "cancel", "No"),
    ]

    def __init__(self, prompt: str, *, action_label: str = "Confirm",
                 danger: bool = True) -> None:
        super().__init__()
        self._prompt = prompt
        self._action_label = action_label
        self._danger = danger

    def compose(self) -> ComposeResult:
        with Grid(id="confirm-dialog"):
            yield Static(self._prompt, id="confirm-prompt")
            yield Button(self._action_label, variant="error" if self._danger else "primary",
                         id="confirm-yes")
            yield Button("Cancel", variant="primary" if self._danger else "default",
                         id="confirm-no")

    def on_button_pressed(self, event: Button.Pressed) -> None:
        self.dismiss(event.button.id == "confirm-yes")

    def action_confirm(self) -> None:
        self.dismiss(True)

    def action_cancel(self) -> None:
        self.dismiss(False)
