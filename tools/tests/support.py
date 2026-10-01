"""Shared helpers for the self-tests (#128): the check/fail recorder and repo paths."""

import sys
from pathlib import Path

TOOLS = Path(__file__).resolve().parent.parent   # tools/
ROOT = TOOLS.parent                               # the repository
if str(TOOLS) not in sys.path:
    sys.path.insert(0, str(TOOLS))

FAILS: list[str] = []


def check(name: str, cond: bool) -> None:
    print(f"  {'ok  ' if cond else 'FAIL'}  {name}")
    if not cond:
        FAILS.append(name)


def _raises(fn, exc) -> bool:
    try:
        fn()
        return False
    except exc:
        return True


def _is_frozen(obj, attr: str, value) -> bool:
    """True iff setting attr on obj raises (a frozen dataclass forbids assignment)."""
    try:
        setattr(obj, attr, value)
        return False
    except Exception:
        return True


# ---- rhubarbtart CLI: clone records -------------------------------------------------------------

def _stub(bindir: Path, name: str, body: str) -> None:
    """A tiny stand-in program, run by *this* Python (portable to the Mac, no CLT python3)."""
    f = bindir / name
    f.write_text(f"#!{sys.executable}\nimport json, os, sys, time\nfrom pathlib import Path\n" + body)
    f.chmod(0o755)
