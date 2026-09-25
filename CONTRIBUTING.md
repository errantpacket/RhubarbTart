# Contributing to RhubarbTart

Thanks for your interest. RhubarbTart's whole value is its guarantees — known inputs, verified
twice, hardened images, proven from outside — so the bar for a change is that it keeps every one of
those intact. This guide is how to work with the project without weakening them.

## Ground rules

These are non-negotiable; `tools/check.sh` enforces most of them, and a change that needs to break
one should be raised as a discussion first, not slipped in:

- **No unpinned or unverified inputs.** Every OS image, package, and host tool is pinned in a
  committed lock and verified against a vendor hash/signature (or recorded as trust-on-first-use).
  Pins are **derived by the tools and reviewed by a human** — never hand-edit a hash or URL in a
  lock. Regenerate it and investigate any difference.
- **No Homebrew, no `packer init`.** The host toolchain comes only from `.toolchain/`
  (`tools/bootstrap.sh`); build plugins are installed offline.
- **Images stay hardened.** No default/weak passwords, no auto-login, no passwordless sudo,
  key-only SSH (or none), firewall on, no shared machine or VPN identity. Every posture change
  needs an assertion in the family's `guest/*/finalize.sh` (or the `nix/` declaration) **and**, where
  observable, a check in `scripts/smoke-test.sh`.
- **Secrets never get baked or logged.** Passwords and enrollment tokens live in the host keychain
  and only ever move keychain → subprocess stdin → shred. Never `argv`, files, images, or logs.
- **Only verified images get a real name.** The smoke test on a throwaway clone is the gate.

The [`rhubarb-dev`](.claude/skills/rhubarb-dev/SKILL.md) skill spells out the invariants and where
each concern lives; [`docs/PLAN.md`](docs/PLAN.md) is the project's direction.

## Getting set up

```sh
./tools/bootstrap.sh && source scripts/env.sh   # pinned toolchain into .toolchain/ (macOS)
uv run tools/resolve.py preflight
./tools/check.sh                                 # run this before every commit — works on Linux too
```

`check.sh` runs the shell/Packer/Python linters, the offline self-tests (Ed25519 RFC 8032 vectors,
NAR hash vs real Nix, Debian version ordering, clone records, the CLI lifecycle, password
rotation), profile validation, and greps every guest family for regressions of the rules above. A
green `check.sh` is the minimum bar for a change; Packer checks need `packer` on PATH.

Much can be validated **without a Mac**: resolvers (`resolve.py plan/resolve --skip-large`), the
self-tests, and NixOS config evaluation via Docker against the pinned nixpkgs (recipe in the
`rhubarb-dev` architecture reference). Things that genuinely need Apple hardware — Setup Assistant
timing, the macOS 27 provisioning path, real installs, `tart ip` — should be called out as
untested in your change.

## Where things go

| Want to… | Start here |
|---|---|
| Add a guest from existing tools | a new `profiles/*.json` — see [`rhubarb-profiles`](.claude/skills/rhubarb-profiles/SKILL.md) |
| Add a tool or an OS base | `config/packages` / `config/bases` — see [`rhubarb-update-inputs`](.claude/skills/rhubarb-update-inputs/SKILL.md) |
| Refresh versions / pins / keys | `resolve.py resolve` / `toolchain-pin`, then review the lock diff |
| Change build code | the [`rhubarb-dev`](.claude/skills/rhubarb-dev/SKILL.md) skill |

## Style

- Match the surrounding code: short comments that explain **why**, specific `die`/`fail` messages,
  no new host dependencies beyond stock macOS + `.toolchain/`.
- **Host and macOS guest scripts run under macOS `/bin/bash` 3.2.** No associative arrays,
  `mapfile`, `${x,,}`, `|&`, or `&>>`. Under `set -e`, `! cmd` never fails the script — write
  `if cmd; then die …; fi`.
- New cryptographic or parsing code gets a case in `tools/test_rhubarb.py` using
  reference-implementation values, not values this code produced.
- Update the README tables (posture / provenance / config) and any affected skill when behavior
  changes — the README is the spec.

## Commits & pull requests

- Keep secrets and build artifacts out of git. `cache/`, `out/`, `.toolchain/`, and vendor
  installers are already git-ignored; don't force them in.
- When a change touches inputs, include the reviewed **lock diff** and explain what moved
  (signers, URLs, versions, any trust-on-first-use entries).
- Describe what you verified and how, and what still needs a real build on a Mac.
- Small, focused commits with a clear message are easier to review than one large one.

## Reporting security issues

Not through public issues or PRs — see [`SECURITY.md`](SECURITY.md).

## Intended use

RhubarbTart is for authorized security research, scoped penetration testing, and CTFs.
Contributions should assume and preserve that authorized-use framing.
