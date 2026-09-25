---
name: rhubarb-dev
description: Safely modify RhubarbTart's code (the Packer templates, guest/install.sh and guest/finalize.sh, scripts/*.sh, tools/bootstrap.sh, tools/resolve.py, tools/check.sh) without breaking its security and provenance guarantees. Explains the invariants and why they exist, where each concern lives, macOS bash 3.2 and BSD-tool pitfalls, the ordering constraints in the seal step, and how to validate changes with tools/check.sh. Use this for any edit, refactor, review or new feature in this repo's build code (hardening changes, SSH/password handling, new checks, Setup Assistant boot_command fixes, adapting to a new macOS release), even small ones, and before approving someone else's diff to these files.
---

# Changing RhubarbTart safely

This repo's value is its guarantees: known inputs, verified twice, and a sealed image whose
hardening is proven from outside. Most "simplifications" of this code quietly remove one of
those guarantees, so before changing behavior, know which guarantee the code serves. The map
of what lives where, the data flow, and the ordering constraints are in
`references/architecture.md`. Read it before touching the build/seal logic.

## Invariants (and why they exist)

Keep all of these true. If a task seems to require breaking one, stop and discuss it with the
user. Present it as a trade-off, not an implementation detail.

1. **No prebuilt images; the IPSW comes only from `updates.cdn-apple.com`, with a hash from Apple.**
   The whole provenance chain starts here. `--from-ipsw=latest` is also banned: Apple's
   "latest" already points at the next major release.
2. **Every input is verified by hash plus signature, on the host *and* in the guest.** The host
   check protects the cache; the guest check protects what's actually installed. Pins are
   derived by the tools (`resolve`, `toolchain-pin`), never hand-typed.
3. **Toolchain only from `.toolchain/`** (no Homebrew, no `packer init`, `CHECKPOINT_DISABLE=1`).
   Otherwise unpinned binaries or plugins enter the build.
4. **No default or reused credentials.** The password is generated per vanilla install, kept in
   the host keychain, and reaches Packer only via `PKR_VAR_password` (env, never argv). No
   `NOPASSWD` sudoers is ever written: provisioning pipes the password to `sudo -S`, so there's
   nothing to forget to remove.
5. **The final image has no auto-login, and key-only SSH (or none).** It has no Screen Sharing,
   has firewall and stealth mode on, keeps SIP and Gatekeeper on, and has no SSH host keys (each
   clone regenerates its own).
6. **The seal step asserts, and the smoke test proves from outside.** Every hardening change
   needs an assertion in `guest/finalize.sh` *and*, where observable, a check in
   `scripts/smoke-test.sh`. Only a smoke-passed image gets the final name.
7. **The VM name is derived from the inputs** (`inputs_sha256`: artifact hashes + Team IDs,
   no timestamps), so identical inputs give an identical name.

`./tools/check.sh` greps for regressions of most of these. When you add a new guarantee, add a
`check` line for it too.

## Pitfalls that have already bitten this repo

- **macOS `/bin/bash` is 3.2** (host scripts and guest scripts). There are no associative
  arrays, `mapfile`, `${x,,}`, `|&` or `&>>`. `printf -v` and `[[ =~ ]]` are fine.
- **`! cmd` never trips `set -e`.** A negative check written as `! grep …` silently passes. Use
  `if cmd; then die …; fi` or `cmd || die` (check.sh flags bare `!` lines).
- **Don't name a helper `log()`** in guest scripts: it shadows macOS `log(1)`, and
  `log erase` silently becomes an echo. The repo uses `say()`.
- **BSD vs GNU tools.** macOS `sed -i` needs `''`, and BSD `grep` wants options before the
  pattern. Use `plutil -extract … raw` for JSON in shell (no python needed), and `security -i`
  to keep secrets out of argv.
- **Loose status greps.** `grep enabled` also matches unrelated status lines. Match Apple's
  exact wording (`status: enabled\.`, `grep -x 'assessments enabled'`, `State = [12]`).
  When Apple changes wording in a new build, update the pattern to the new exact text; don't
  widen it into something that can also match a disabled state.
- **Credentials in shell.** The password is letters and digits only (HCL validation), because
  it's typed over VNC and embedded in single quotes. Keep that validation if you touch it.
- **Packer HCL** must be `packer fmt`-clean, and the plugin version must match
  `PACKER_PLUGIN_TART_VERSION` (check.sh enforces both).

## Workflow for a change

1. Read the relevant file(s) and `references/architecture.md`, and name which invariant or
   stage the change touches.
2. Make the change in the style of the surrounding code: short comments that explain *why*,
   `die`/`fail` with a specific message, no new dependencies on the host beyond stock macOS +
   `.toolchain/`.
3. If behavior or posture changed, update the assertion in finalize.sh, the smoke test, and the
   README tables (Security posture / Provenance / Configuration). The README is the spec that
   users and these skills rely on, so it must not drift.
4. Run `./tools/check.sh` (works on Linux; Packer checks need `.toolchain/` or `packer` on PATH).
   To confirm a new invariant check really catches something, plant the regression in a scratch
   copy of the repo, not in the working tree.
5. Report precisely: what check.sh verified, and what still needs a real build on the Mac.
   Setup Assistant timing, sshd behavior, launchd state and `socketfilterfw` output can only be
   confirmed there. Don't claim those as verified.
