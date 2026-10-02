"""Keep the README and the docs site in step (#164). Run by tools/check.sh; stdlib only.

The README is the landing page on GitHub and the site is the full documentation, so a few parts
appear in both. Each is written twice on purpose (the README has to stand alone) and checked here:

- the Quick start commands and step explanations: README "## Quick start" and docs/quickstart.md;
- the guest table: README "## Choose a guest" and docs/profiles.md;
- every committed profile appears in that table, or by name in the text below it;
- every page under docs/ is in the site's nav or deliberately excluded (zensical.toml).

Links are compared with the README's `docs/` prefix removed, since the site resolves them from
inside docs/. Prints one line per problem and exits 1 if there are any.
"""

from __future__ import annotations

import re
import subprocess
import sys
import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def section(text: str, heading: str) -> str:
    """The body of a `## heading` section, up to the next `## ` heading."""
    m = re.search(rf"^## {re.escape(heading)}\n(.*?)(?=^## |\Z)", text, re.S | re.M)
    return m.group(1) if m else ""


def code_block(body: str) -> str:
    m = re.search(r"^```sh\n(.*?)^```", body, re.S | re.M)
    return m.group(1) if m else ""


def steps(body: str) -> str:
    """The numbered explanation list (from `1. **` to the end of the list), normalised."""
    m = re.search(r"^1\. \*\*.*?(?=^</details>|^## |\Z)", body, re.S | re.M)
    return norm(m.group(0)) if m else ""


def table(body: str) -> str:
    return norm("\n".join(line for line in body.splitlines() if line.startswith("|")))


def norm(text: str) -> str:
    text = text.replace("](docs/", "](")
    return "\n".join(line.rstrip() for line in text.strip().splitlines() if line.strip())


def main() -> int:
    problems: list[str] = []
    readme = (ROOT / "README.md").read_text()
    quick = (ROOT / "docs/quickstart.md").read_text()
    profiles_page = (ROOT / "docs/profiles.md").read_text()

    r_quick, d_quick = section(readme, "Quick start"), section(quick, "The steps")
    if not code_block(r_quick) or code_block(r_quick) != code_block(d_quick):
        problems.append("Quick start commands differ: README.md '## Quick start' vs "
                        "docs/quickstart.md '## The steps' (copy one to the other)")
    if not steps(r_quick) or steps(r_quick) != steps(section(quick, "What each step does")):
        problems.append("Quick start step explanations differ: README.md 'What each step does' vs "
                        "docs/quickstart.md '## What each step does'")

    r_guests, d_guests = section(readme, "Choose a guest"), section(profiles_page, "Choose a guest")
    if not table(r_guests) or table(r_guests) != table(d_guests):
        problems.append("guest table differs: README.md vs docs/profiles.md '## Choose a guest'")
    listed = d_guests
    for f in sorted(subprocess.run(["git", "ls-files", "profiles/*.json"], cwd=ROOT,
                                   capture_output=True, text=True, check=True).stdout.split()):
        pid = Path(f).stem
        if f"`{pid}`" not in listed:
            problems.append(f"profile {pid} ({f}) is not in docs/profiles.md '## Choose a guest'")

    cfg = tomllib.loads((ROOT / "zensical.toml").read_text())["project"]
    in_nav: set[str] = set()

    def walk(items: list) -> None:
        for item in items:
            for value in item.values():
                walk(value) if isinstance(value, list) else in_nav.add(value)

    walk(cfg.get("nav", []))
    excluded = set(cfg.get("plugins", {}).get("exclude", {}).get("glob", []))
    for page in sorted(p.name for p in (ROOT / "docs").glob("*.md")):
        if page not in in_nav and page not in excluded:
            problems.append(f"docs/{page} is neither in zensical.toml's nav nor excluded from the site")
    for page in sorted(in_nav - {p.name for p in (ROOT / "docs").glob("*.md")}):
        problems.append(f"zensical.toml's nav lists docs/{page}, which doesn't exist")

    for p in problems:
        print(p)
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main())
