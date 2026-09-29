# Development

How to validate a change: `check.sh`, the off-Mac paths, and the project skills that guide Claude Code.

```sh
./tools/check.sh    # before every commit; also runs on Linux
```

`check.sh` runs shell, Packer and Python checks, the offline self-tests (Ed25519 RFC 8032 vectors,
NAR hash vs real Nix, Debian version ordering, clone records, the `rhubarb` CLI lifecycle and
password rotation), a headless Textual TUI render test, and profile validation. It also greps every family
for regressions of the rules above: Homebrew or `packer init` creeping back, default credentials,
`NOPASSWD`, auto-login, password SSH, unsigned repos, baked VPN secrets, and plugin-version drift.

- Host and macOS guest scripts run under macOS `/bin/bash` 3.2. Under `set -e`, `! cmd` never
  fails the script, so write `if cmd; then die …; fi`.
- NixOS changes can be evaluated without a Mac, using Docker against the pinned nixpkgs.

**Working with Claude Code?** The repo ships four project skills:

| Skill | Use it to… |
|---|---|
| `rhubarb-profiles` | Design a guest: pick an OS, tools, options and sizes |
| `rhubarb-build` | Build, run, clone, SSH, enroll, and troubleshoot failures |
| `rhubarb-update-inputs` | Refresh locks, pin Team IDs and keys, bump the toolchain, add tools or OS bases |
| `rhubarb-dev` | Change the build code without breaking a guarantee |

← back to the [README](../README.md)
