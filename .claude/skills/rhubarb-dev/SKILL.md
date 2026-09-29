---
name: rhubarb-dev
description: Safely change RhubarbTart's build code (resolvers and verification, Packer templates, in-guest install and seal scripts, NixOS modules, Kali preseed, build/smoke-test/enroll scripts, check.sh) without breaking its security and provenance guarantees. Explains the invariants and why they exist, where each concern lives, per-family pitfalls (macOS bash 3.2, `! cmd` under set -e, password handling, seal ordering) and how to validate a change (check.sh, self-tests, Docker NixOS evaluation, stub simulations). Use this for any code edit, bug fix, refactor or new feature in this repo, for hardening changes, adapting to a new macOS/NixOS/Kali release, fixing a failing check, or reviewing someone's diff or doing a security review of the pipeline, even for small changes.
---

# Changing RhubarbTart safely

The value of this repo is its guarantees: known inputs verified twice, images sealed without
secrets or shared identity, and hardening proven from outside before an image is named. Most
"simplifications" quietly remove one of those, so before changing behavior, know which guarantee
the code serves. The map of where things live, the data flow and the ordering constraints are in
`references/architecture.md`. Read it before touching resolve, build, guest or seal logic.

## Invariants (and why they exist)

Keep all of these true for **every family**. If a task seems to require breaking one, stop and
present it to the user as a trade-off, not an implementation detail.

1. **Only vendor installers.** No prebuilt VM images. Apple IPSW only from
   `updates.cdn-apple.com`; NixOS ISO from `releases.nixos.org`; Kali ISO from `cdimage.kali.org`.
   Never trust a floating "latest" (`--from-ipsw=latest` already means a different macOS major).
2. **Every input is hash-pinned by a tool, never by hand, and re-verified in the guest.** Signed
   sources need both a pinned key *file* and a pinned *fingerprint* (`gpg.py` checks the
   VALIDSIG primary fingerprint). HTTPS only (`common.py` refuses anything else).
3. **Toolchain only from `.toolchain/`.** No Homebrew, no `packer init`, `CHECKPOINT_DISABLE=1`,
   and uv's own pinned Python.
4. **No reusable credentials.** Passwords are random per build, kept in the host keychain, and
   delivered via env (`PKR_VAR_*`) or 0600 uploaded files, never host argv. Anywhere a
   vendor/tool forces argv (the macOS 27 provisioning API, installer preseeds), use a random
   **bootstrap** password, rotate it, and prove it's dead. No `NOPASSWD` in any image: provisioning
   uses `sudo -S`. The one sanctioned exception is the NixOS *live ISO's* own passwordless sudo
   for the throwaway `nixos` user. It exists only in the installer session, and nothing from it
   reaches the installed system. The one *image* exception (owner decision, #59) is Kali's
   `ospd-openvas` rule `_gvm ALL = NOPASSWD: /usr/sbin/openvas`, which the Kali seal allows only as that
   exact line in a root-owned 0440 file, for a locked nologin `_gvm`. Anything broader fails.
   `kali-grant-root` (group-wide NOPASSWD ALL) is purged and pinned out.
5. **Sealed images have no auto-login, key-only SSH (or none), a firewall on, no shared
   identity** (host keys, machine-id, VPN state), and no build residue.
6. **The seal asserts; the smoke test proves from outside.** Each posture change needs an
   assertion in that family's `finalize.sh` (or the `nix/` declaration plus a finalize check) and,
   where observable, a smoke-test check. Only a smoke-passed image gets the final name.
7. **VM names derive from inputs** (`inputs_sha256`: profile + artifact hashes + signers, no
   timestamps).
8. **Secrets for enrollment never touch the repo, a profile or an image.** They go host keychain
   → SSH stdin → 0600 temp file → shred.
9. **Clone records hold no secrets and are trusted only when safe** (StrictModes-style: 0700
   dir, 0600 regular file, owned by you, strict schema, name matches file). `rhubarb` acts only
   on clones it recorded, never on `rbt-…` images, and passwords only move keychain →
   subprocess stdin. Keep new commands inside those rules, and add a case to `test_records` /
   `test_cli_lifecycle` for each.

`./tools/check.sh` greps for regressions of most of these. When you add a guarantee, add a
`check` line for it too, and prove it fires by planting the regression in a *scratch copy* of
the repo.

## Pitfalls that have already bitten this repo

- **macOS `/bin/bash` is 3.2** for host scripts and `guest/macos/*`. That rules out associative
  arrays, `mapfile`, `${x,,}`, `|&` and `&>>`. Linux guest scripts may use modern bash.
- **`! cmd` never trips `set -e`.** Write `if cmd; then die …; fi` or `cmd || die`.
- **Don't name a helper `log()` in `guest/macos/*`**: it shadows macOS `log(1)`, and the seal
  step runs `log erase`. Those scripts use `say()`. Host scripts (`build.sh`, `smoke-test.sh`) may
  keep `log()`, because they never call `log(1)`. check.sh guards the guest scripts.
- **Loose status greps** (`grep enabled` also matches unrelated lines). Match exact vendor wording,
  and update the pattern *exactly* when a release changes it.
- **Tools that print raw bytes** (gpg status lines carry non-UTF-8 user IDs): decode leniently
  and compare only the structured fields.
- **Plugin/tool behavior assumptions:**
  - The Tart plugin runs `tart ip` before typing `boot_command` when Packer's HTTP server is on,
    which is why Kali uses `tools/serve_preseed.py`.
  - The plugin's shutdown uses `sudo -S` with the SSH password, so once a password is rotated
    the guest must power itself off.
  - Read the plugin or Tart source before relying on a behavior.
- **NixOS:**
  - Files the config reads must exist when the config is evaluated.
  - Anything under `/nix/store` is world-readable, so keep secrets (password hash) outside it.
  - The Rosetta mount must be `nofail`.

## Workflow for a change

1. Read the relevant files plus `references/architecture.md`, and name the invariant or stage
   the change touches.
2. Change in the surrounding style: short *why* comments, `die`/`fail` with specific messages, no
   new host dependencies beyond stock macOS plus `.toolchain/`.
3. If posture or provenance changed, update the finalize assertion, the smoke test, and the
   README tables (Security posture, Provenance, Profiles, Configuration). The README is the spec
   users and these skills rely on.
4. Validate:
   - `./tools/check.sh`. It works on Linux; Packer checks need `packer` on PATH.
   - Resolver changes: `uv run tools/resolve.py plan <profile>` against live upstream, and add
     self-test cases for any crypto or parsing.
   - NixOS changes: the Docker evaluation recipe in `references/architecture.md`.
   - Guest shell transport changes: simulate with stub commands, as was done for `enroll.sh`
     and the rotation script (`test_rotation_script`). Stubs must behave like the real tool.
     For example, `sudo -S` reads the password byte by byte and leaves the rest of stdin for the
     command it runs.
5. Report precisely: what you verified (and how) versus what only a real build on the Mac can
   confirm (keystroke timing, provisioning API, installer flows, sshd/launchd/systemd runtime
   state).
