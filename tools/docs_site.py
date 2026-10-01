# /// script
# requires-python = ">=3.12"
# dependencies = ["zensical==0.0.67"]
# ///
"""Build or preview the documentation site (#37): ``uv run --script tools/docs_site.py ARGS``.

A thin launcher for the Zensical CLI that carries its pin. Zensical (and everything it pulls in)
is pinned and hash-verified through the adjacent uv script lockfile ``tools/docs_site.py.lock``
(``uv lock --script tools/docs_site.py``), the same way the TUI pins Textual. check.sh asserts
the pin and the lock, and builds the site with ``--strict`` so a broken link fails.

    uv run --script tools/docs_site.py build --strict   # into site/ (gitignored)
    uv run --script tools/docs_site.py serve            # live preview
"""

import os
import sys
from pathlib import Path

if __name__ == "__main__":
    os.chdir(Path(__file__).resolve().parents[1])   # zensical.toml is at the repository root
    from zensical.main import cli

    sys.exit(cli())
