# /// script
# requires-python = ">=3.12"
# dependencies = ["textual==8.2.8"]
# ///
"""Launcher for the RhubarbTart TUI (``./rhubarbtart-tui``).

The app lives in ``rhubarb.tui.app`` (layout, polling, provenance routing) with action dispatch
in ``rhubarb.tui.dispatch``; this file only carries the pinned, hash-locked Textual dependency
(PEP 723 metadata + ``tools/rhubarb_tui.py.lock``) and starts the app. It is a thin client of
the typed core API (``tools/rhubarb/api.py``): it never touches ``tart`` or the keychain directly.

Textual is pinned to an exact version and hash-verified via uv's script lockfile
(``uv lock --script``); the shim runs this file with ``uv run --script``. See
docs/INTERFACE-PLAN.md (decision gate B-0).
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from rhubarb.tui.app import RhubarbTUI, main, mouse_enabled  # noqa: E402

__all__ = ["RhubarbTUI", "main", "mouse_enabled"]

if __name__ == "__main__":
    main()
