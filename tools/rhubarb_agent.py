# /// script
# requires-python = ">=3.12"
# dependencies = []
# ///
"""Entry point for ./rbt-range, the scoped range client an agent uses (see tools/rhubarb/agent.py)."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from rhubarb.agent import main  # noqa: E402

if __name__ == "__main__":
    main()
