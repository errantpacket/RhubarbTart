# /// script
# requires-python = ">=3.12"
# dependencies = []
# ///
"""Entry point for ./rhubarb (see tools/rhubarb/cli.py)."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from rhubarb.cli import main  # noqa: E402

if __name__ == "__main__":
    main()
