# Development

How to validate a change: `check.sh`, the documentation site, the off-Mac paths, and the project
skills that guide Claude Code. The workflow (issue, branch, pull request) is in
[CONTRIBUTING.md](https://github.com/errantpacket/RhubarbTart/blob/main/CONTRIBUTING.md#workflow-issues-branches-pull-requests).

## Checks

```sh
./tools/check.sh    # before every commit; also runs on Linux
```

CI runs the same `check.sh` on Linux for every pull request and push to `main`
(`.github/workflows/check.yml`). There, Packer's syntax and format checks use the runner's
preinstalled Packer, which is not pinned; the pinned Packer runs them on the Mac. `check.sh` also checks that the workflow's actions are
pinned to commit SHAs and that its uv matches `config/toolchain.env`.

`check.sh` runs shell, Packer and Python checks, the offline self-tests, a headless Textual TUI
render test, and profile validation. The self-tests cover Ed25519 RFC 8032 vectors, NAR hash vs
real Nix, Debian version ordering, clone records, the `rhubarbtart` CLI lifecycle, password rotation,
engagements and links, the evidence chain, vault seal and verify, the control-plane service, the
range client, `herdr arm`, tiered approvals and the logs API.

It also greps for regressions of the
[ground rules](https://github.com/errantpacket/RhubarbTart/blob/main/CONTRIBUTING.md#ground-rules):

- Homebrew or `packer init` creeping back, or `--from-ipsw=latest`;
- default credentials, `NOPASSWD`, auto-login, password SSH, disabled SIP or Gatekeeper;
- unsigned repos, plain-HTTP downloads, baked VPN secrets;
- `gpg` called outside `gpg.py`, or evidence unpacked on the host;
- the service or herdr driver bypassing the core;
- bash 3.2 incompatibilities in host and macOS guest scripts.

Finally it checks that the plugin version, the Textual and Zensical locks and the CI workflows'
pins are consistent, that `__version__` has a `CHANGELOG.md` entry, and that no tracked Markdown
contains an em dash.

- Host and macOS guest scripts run under macOS `/bin/bash` 3.2. Under `set -e`, `! cmd` never
  fails the script, so write `if cmd; then die …; fi`.
- NixOS changes can be evaluated without a Mac, using Docker against the pinned nixpkgs (the
  recipe is in the `rhubarb-dev` skill's
  [architecture notes](https://github.com/errantpacket/RhubarbTart/blob/main/.claude/skills/rhubarb-dev/references/architecture.md)).

## The documentation site

The documentation site is built from `docs/` by [Zensical](https://zensical.org), pinned and
hash-locked in `tools/docs_site.py` like the TUI's Textual. Preview it, and build it the way CI does:

```sh
uv run --script tools/docs_site.py serve            # http://localhost:8000
uv run --script tools/docs_site.py build --strict   # fails on a broken link
```

`check.sh` runs the strict build. `.github/workflows/docs.yml` publishes the site to GitHub
Pages when a push to `main` changes `docs/`, `zensical.toml` or the site's tooling, and can also
be started by hand. Links from a docs page to files outside `docs/` must be absolute GitHub URLs,
because the site only contains `docs/`.

Some parts are written twice on purpose, because the README has to stand alone on GitHub: the
Quick start commands and step explanations (README and `quickstart.md`) and the guest table
(README and `profiles.md`). `check.sh` runs `tools/docs_sync.py`, which fails when the copies
differ, when a committed profile is missing from the guest table, or when a page in `docs/` is
neither in `zensical.toml`'s nav nor excluded from the site. The design records stay in `docs/`
for contributors and are excluded from the site by the `exclude` plugin in `zensical.toml`.

## Claude Code skills

The repository ships four project skills in `.claude/skills/`:

| Skill | Use it to… |
|---|---|
| `rhubarb-profiles` | Design a guest: pick an OS, tools, options and sizes |
| `rhubarb-build` | Build, run, clone, SSH, enroll, and troubleshoot failures |
| `rhubarb-update-inputs` | Refresh locks, pin Team IDs and keys, bump the toolchain, add tools or OS bases |
| `rhubarb-dev` | Change the build code without breaking a guarantee |
