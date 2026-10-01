# Contributing to RhubarbTart

Thanks for your interest. RhubarbTart exists for its guarantees: known inputs, verified twice,
hardened images, proven from outside. A change must keep every one of them intact. This guide
explains how to work on the project without weakening them.

## Ground rules

These are fixed. `tools/check.sh` enforces most of them. If a change needs to break one, raise it
as a discussion first:

- **No unpinned or unverified inputs.** Every OS image, package, and host tool is pinned in a
  committed lock and verified against a vendor hash/signature (or recorded as trust-on-first-use).
  Pins are **derived by the tools and reviewed by a human**. Never hand-edit a hash or URL in a
  lock. Regenerate it and investigate any difference.
- **No Homebrew, no `packer init`.** The host toolchain comes only from `.toolchain/`
  (`tools/bootstrap.sh`); build plugins are installed offline.
- **Images stay hardened.** No default/weak passwords, no auto-login, no passwordless sudo,
  key-only SSH (or none), firewall on, no shared machine or VPN identity. Every posture change
  needs an assertion in the family's `guest/*/finalize.sh` (or the `guest/nixos/nix/` declaration) **and**, where
  observable, a check in `scripts/smoke-test.sh`.
- **Secrets never get baked or logged.** Passwords and enrollment tokens live in the host keychain
  and only move keychain → subprocess stdin → shred. Never `argv`, files, images, or logs.
- **Only verified images get a real name.** The smoke test on a throwaway clone is the gate.

The [`rhubarb-dev`](.claude/skills/rhubarb-dev/SKILL.md) skill spells out the invariants and where
each concern lives; [`docs/PLAN.md`](docs/PLAN.md) is the project's direction.

## Getting set up

```sh
./tools/bootstrap.sh && source scripts/env.sh   # pinned toolchain into .toolchain/ (macOS)
uv run tools/resolve.py preflight
./tools/check.sh                                 # run this before every commit; works on Linux too
```

`check.sh` runs the shell/Packer/Python linters, the offline self-tests (crypto and parsing, clone
records, the CLI lifecycle, password rotation, engagements, evidence, vaults, the control-plane
service and herdr approvals), a headless TUI render test, profile validation, and greps for
regressions of the rules above. A green `check.sh` is the minimum bar for a change. Packer checks
need `packer` on PATH, and a check whose tool is missing is reported as SKIPPED. See
[Development](docs/development.md) for the full list.

Much can be validated **without a Mac**: resolvers (`resolve.py plan/resolve --skip-large`), the
self-tests, and NixOS config evaluation via Docker against the pinned nixpkgs (recipe in the
`rhubarb-dev` architecture reference). Call out anything that needs Apple hardware as untested in
your change: Setup Assistant timing, the macOS 27 provisioning path, real installs, `tart ip`.

## Where things go

| Want to… | Start here |
|---|---|
| Add a guest from existing tools | a new `profiles/*.json`; see [`rhubarb-profiles`](.claude/skills/rhubarb-profiles/SKILL.md) |
| Add a tool or an OS base | `config/packages` / `config/bases`; see [`rhubarb-update-inputs`](.claude/skills/rhubarb-update-inputs/SKILL.md) |
| Refresh versions / pins / keys | `resolve.py resolve` / `toolchain-pin`, then review the lock diff |
| Change build code, the CLI, core API, TUI, service or herdr integration | the [`rhubarb-dev`](.claude/skills/rhubarb-dev/SKILL.md) skill |
| Define an engagement | `engagements/<id>.json` (and `<id>.herdr.json` for agents); see [Using your VMs](docs/using.md#engagements) |

## Style

- Match the surrounding code: short comments that explain **why**, specific `die`/`fail` messages,
  no new host dependencies beyond stock macOS + `.toolchain/`.
- **Host and macOS guest scripts run under macOS `/bin/bash` 3.2.** No associative arrays,
  `mapfile`, `${x,,}`, `|&`, or `&>>`. Under `set -e`, `! cmd` never fails the script, so write
  `if cmd; then die …; fi`.
- New cryptographic or parsing code gets a case in `tools/tests/test_verification.py` using
  reference-implementation values, not values this code produced.
- Update the reference pages under `docs/` when behavior changes, and any affected skill. The
  posture and provenance tables are in [`docs/trust-model.md`](docs/trust-model.md). The config,
  toolchain and repository tables are in [`docs/reference.md`](docs/reference.md). The README is
  the landing page; the `docs/` reference pages are the spec.

## Workflow: issues, branches, pull requests

`main` is the only long-lived branch. It must always pass `./tools/check.sh`, which CI
(`.github/workflows/check.yml`) also runs on every pull request and push to `main`.

1. **Start from an issue.** Every change, including docs and validation runs, has an issue on the
   board. Use the existing labels (`bug`, `enhancement`, `task`, `validation`, `kind:*`, `stage:*`).
2. **Branch off current `main`** as `<type>/<issue>-<slug>`, for example `fix/47-confirm-eof`,
   `feat/30-softnet`, `docs/49-branch-workflow` or `validate/25-kali`. The types are `fix`, `feat`,
   `docs`, `chore` and `validate`. Don't create long-lived `dev-*` branches.
3. **Open a PR into `main`** whose body says `Closes #N` and gives the verification (below). PRs
   are squash-merged, so history stays linear with no merge commits. The branch is deleted on merge.
4. **Post evidence on the issue.** Results from a real Mac (builds, smoke tests, gates) go there
   as a comment, because only the Mac can show them.
5. **Temporary coordination notes** (handoffs, agent-to-agent files, patches) are never
   committed. Delete them once the issue closes; the issue and the commits are the record.

## Commits & pull requests

- Keep secrets and build artifacts out of git. `cache/`, `out/`, `.toolchain/`, sealed
  `vaults/` and vendor installers are already git-ignored; don't force them in.
- When a change touches inputs, include the reviewed **lock diff** and explain what moved
  (signers, URLs, versions, any trust-on-first-use entries).
- Describe what you verified and how, and what still needs a real build on a Mac.
- Small, focused commits with a clear message are easier to review than one large one.

## Releases

The repository is versioned as a whole (SemVer, 0.x for now). The version lives in one place,
`__version__` in `tools/rhubarb/__init__.py`; `rhubarbtart --version`, the control-plane `/health`
endpoint and every new provenance record report it. It covers the `rhubarbtart` CLI, the profile,
engagement and lock file formats, and provenance records, not the internals of `api.py`. Image
names don't depend on it: they come from the inputs.

To release:

1. Move the `[Unreleased]` notes in `CHANGELOG.md` under a new `## [X.Y.Z] - YYYY-MM-DD` heading,
   set `__version__` to match (`check.sh` checks they agree), and merge that through a PR.
2. Tag `main` with an annotated, signed tag `vX.Y.Z`.
3. Publish a GitHub release with a `git archive` tarball of the tag, its `SHA256SUMS`, and a cosign
   signature made with the project's offline signing key (`scripts/signing-key.sh`), and the
   changelog section as the notes.

There is no Python package on PyPI: the code needs the repository's profiles, locks, templates and
scripts around it.

## Reporting security issues

Not through public issues or PRs. See [`SECURITY.md`](SECURITY.md).

## Intended use

RhubarbTart is for authorized security research, scoped penetration testing, and CTFs.
Contributions should assume and preserve that authorized-use framing.
