# Development

How to validate a change: `check.sh`, the off-Mac paths, and the project skills that guide Claude Code.

```sh
./tools/check.sh    # before every commit; also runs on Linux
```

CI runs the same `check.sh` on Linux for every pull request and push to `main`
(`.github/workflows/check.yml`). There, Packer checks show as SKIPPED, because the pinned Packer is
the macOS build; they still run on the Mac. `check.sh` also checks that the workflow's actions are
pinned to commit SHAs and that its uv matches `config/toolchain.env`.

`check.sh` runs shell, Packer and Python checks, the offline self-tests, a headless Textual TUI
render test, and profile validation. The self-tests cover Ed25519 RFC 8032 vectors, NAR hash vs
real Nix, Debian version ordering, clone records, the `rhubarb` CLI lifecycle, password rotation,
engagements and links, the evidence chain, vault seal and verify, the control-plane service, the
range client, `herdr arm`, tiered approvals and the logs API. It also greps for regressions of the
[ground rules](../CONTRIBUTING.md#ground-rules): Homebrew or `packer init` creeping back, default
credentials, `NOPASSWD`, auto-login, password SSH, disabled SIP or Gatekeeper, unsigned repos,
baked VPN secrets, plain-HTTP downloads, the service or herdr driver bypassing the core, and
plugin-version or Textual-lock drift.

- Host and macOS guest scripts run under macOS `/bin/bash` 3.2. Under `set -e`, `! cmd` never
  fails the script, so write `if cmd; then die …; fi`.
- NixOS changes can be evaluated without a Mac, using Docker against the pinned nixpkgs.

**Claude Code skills.** The repo ships four project skills:

| Skill | Use it to… |
|---|---|
| `rhubarb-profiles` | Design a guest: pick an OS, tools, options and sizes |
| `rhubarb-build` | Build, run, clone, SSH, enroll, and troubleshoot failures |
| `rhubarb-update-inputs` | Refresh locks, pin Team IDs and keys, bump the toolchain, add tools or OS bases |
| `rhubarb-dev` | Change the build code without breaking a guarantee |

← back to the [README](../README.md)
