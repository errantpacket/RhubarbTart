---
name: rhubarb-build
description: Build, run, enroll and troubleshoot RhubarbTart guest VMs (Tart + Packer) from their profiles (macOS 26/27, NixOS and Kali). Covers bootstrapping the pinned toolchain, preflight, scripts/build.sh PROFILE, the smoke test, managing clones with the rhubarbtart CLI (new, run, stop, ssh, exec, enroll, list, reset, rm, per-clone passwords, outdated clones), engagements (define, provision, connect, teardown, evidence, vaults), finding passwords in the keychain, Rosetta, VPN/ZTNA enrollment (Tailscale, Cloudflare WARP), and diagnosing failures (Setup Assistant or provisioning hangs, Kali preseed/GRUB, NixOS install, finalize or smoke-test errors, hash/signature/Team ID mismatches, -unverified images). Use this whenever someone wants to make, rebuild, run, clone, reset, remove, connect to or enroll a research VM, or says a build, bootstrap, enrollment or smoke test failed, even if they don't mention Tart or Packer. For designing a guest use rhubarb-profiles; for refreshing versions use rhubarb-update-inputs.
---

# Building and running RhubarbTart guests

A guest is a **profile** (`profiles/<id>.json`: OS base + packages + options). The pipeline turns
the OS vendor's installer plus the profile's locked inputs into a hardened Tart VM, proves the
hardening from outside, and only then gives the image its final name. Drive that pipeline; when it
fails, find the cause without weakening any check. Every failure message means a guarantee didn't
hold.

## Before you start

- `./scripts/build.sh --list` (or `uv run tools/resolve.py list`) shows the profiles.
- **Where things can run:**
  - Builds need an **Apple silicon Mac**. `macos-27` profiles need a **macOS 27 host**.
  - `resolve` for NixOS/Kali profiles runs anywhere with `gpg`, Linux included.
  - `resolve` for macOS profiles needs macOS, because the signature checks use codesign.
  - Say so rather than attempting a step on the wrong host.
- Run builds from a **GUI Terminal session on the Mac**, not over SSH. macOS Local Network
  Privacy blocks Packer/VM networking for SSH-launched processes, and the keychain writes need
  the login session. (The TUI's Build action refuses when it detects an SSH session.)
- Shell state doesn't persist between your commands. **On the Mac**, prefix them with
  `source scripts/env.sh &&`, otherwise preflight (correctly) refuses other `tart`/`packer`/`uv`
  installs. **On a Linux resolve host** there is no `.toolchain/` (env.sh would exit), so run
  `uv run tools/resolve.py …` directly there.

## The sequence (and why each step exists)

```sh
./tools/bootstrap.sh                                   # 1. pinned toolchain -> .toolchain/
source scripts/env.sh && uv run tools/resolve.py preflight  # 2. right binaries, versions, signer
uv run tools/resolve.py resolve <profile>              # 3. only when refreshing inputs
ssh-add --apple-use-keychain ~/.ssh/rhubarbtart_ed25519   # 4. the smoke test logs in with it
RHUBARB_SSH_PUBKEYS=~/.ssh/rhubarbtart_ed25519.pub ./scripts/build.sh <profile>   # 5.
```

1. Run **bootstrap** once, and again whenever `config/toolchain.env` changes.
2. **preflight** also runs inside build.sh; running it first gives a clearer failure.
3. **resolve** rewrites `locks/<profile>.lock.json`, which is what gets built. Don't run it just to
   build: a committed lock plus `cache/` is enough. Refreshing means reviewing the diff and
   committing before building; use the `rhubarb-update-inputs` skill for that.
4. Without the key in the agent, the smoke test can't prove key login works. If the user has no
   VM key yet, they create one with a passphrase, `ssh-keygen -t ed25519 -a 100 -C rhubarbtart -f
   ~/.ssh/rhubarbtart_ed25519`. It prompts for the passphrase, so ask them to run it themselves
   (`! <command>`) rather than creating it for them. After a restart, `ssh-add
   --apple-load-keychain` reloads it. Details: `docs/using.md` ("Your SSH key").
5. **build.sh** verifies the cache against the lock (hash plus signer), installs by family, builds
   `rbt-<profile>-<sha>-unverified`, smoke-tests a throwaway clone, renames on success, and writes
   `out/<vm>.provenance.json`.

Builds are long: macOS stage 1 and Kali's unattended install dominate, and Packer allows the
Kali install up to 120 minutes. Run build.sh in the background and check on it rather than
blocking.

### Choices to confirm with the user

- **SSH keys:** without `RHUBARB_SSH_PUBKEYS` the image has SSH *disabled*. That's secure and
  valid, but confirm it's intended. Only ed25519/ecdsa keys (optionally `-sk`) are accepted; RSA
  is rejected.
- **`REBUILD_VANILLA=1`** (macOS only) reinstalls the vanilla VM from the IPSW and rotates its
  password. A *new* macOS build doesn't need it, since the vanilla name includes the build
  (`rbt-BASE-BUILD-vanilla`). Use it after a stage-1 template change, or when the vanilla VM's
  keychain entry is lost. Old vanilla VMs stay on disk until you `tart delete` them.
- **`RHUBARB_SSH_FROM`** (default `192.168.64.1`, Tart's shared-NAT host) only changes on a
  non-default vmnet subnet. Kali also binds its preseed server to that address, so it must be
  the vmnet host IPv4. Softnet is not supported: it moves each VM to a random subnet (#30).

## Using built images: the `rhubarbtart` CLI

Built images are templates. All work happens in clones managed by `./rhubarbtart` (on the Mac):

```sh
./rhubarbtart images                              # built images, current vs outdated per profile
./rhubarbtart new web-1 --profile kali-research   # clone + per-clone password rotation (--no-rotate keeps the image's)
./rhubarbtart new web-2 --image rbt-<profile>-<sha>   # clone a specific verified image
./rhubarbtart new mac-1 --profile tahoe-research --from-registry  # macOS only: stacked on the verified registry copy (#31)
./rhubarbtart run web-1 [--headless] [--detach]   # applies Rosetta etc. from the record
./rhubarbtart stop web-1                          # stop a running clone (VM and record stay)
./rhubarbtart ssh web-1 [-- CMD]
./rhubarbtart exec web-1 -- CMD                   # run one command; journaled as evidence if the clone is in an engagement
./rhubarbtart enroll web-1 tailscale              # or: warp --org TEAM (only these two services)
./rhubarbtart list                                # state, outdated/deleted image, password mode, enrollment
./rhubarbtart reset web-1 [--same-image] [--no-rotate]   # destroy + re-clone (drops identity and enrollment)
./rhubarbtart rm web-1 [--yes]
```

`./rhubarbtart-tui` is a terminal dashboard over the same core (Images, Clones, Provenance and Logs
tabs, plus the clone actions and Build). Its Build action writes
`logs/build-<profile>-<UTC>.log` (0600, VNC password redacted) in the state dir.

- **Prefer `rhubarbtart` over raw `tart`/`scripts/*.sh`** for clones. It records lineage
  (profile, image, username, Rosetta), so the right flags, user and keychain entry are used.
  It only acts on clones it created, and never on built `rbt-…` images or other VMs.
- **`new` gives each clone its own random password** (keychain account = clone name), proven
  through `sudo` that the old one is rejected. This needs key SSH: an image built with
  `RHUBARB_SSH_PUBKEYS` and the key in `ssh-agent` (or `RHUBARB_SSH_IDENTITY=<keyfile>`).
  Otherwise the clone keeps the image's password (`list` shows `inherited`). An image whose
  provenance records SSH *disabled* is refused up front with a rebuild hint. Suggest
  `reset NAME --same-image` once the key is loaded.
- **`outdated`** in `list` means the profile's lock has moved on since that clone was made.
  Offer `rhubarbtart reset NAME` (a fresh clone of the current image); warn that it discards the
  clone's state and enrollment.
- **Passwords:** `security find-generic-password -s RhubarbTart -a CLONE -w` (or the image name
  when `inherited`). Don't print passwords or enrollment secrets unless asked.
- **Enrollment secrets** live in the host keychain (service `RhubarbTart-enroll`); the header of
  `scripts/enroll.sh` shows how to add them. For macOS + Tailscale the user approves the system
  extension once per clone, then removes the auth-key policy (the script prints how).
- **Records** live in `~/Library/Application Support/RhubarbTart/` (or `RHUBARB_STATE_DIR`),
  together with `events.log`, run and build logs (`logs/`), evidence (`evidence/`) and the
  service socket. If `rhubarbtart` refuses a record (bad permissions, symlink, schema), don't loosen
  the check: see the troubleshooting guide.

## Engagements, evidence and agents

An engagement (`engagements/<id>.json`) is a committed scope manifest: ranges of clones built
from profiles, `links` between them, targets, agent budget and evidence policy. Clones can't
reach each other on Tart's default NAT; a manifest's `links` are the only path. Details:
`docs/using.md` (Engagements).

```sh
./rhubarbtart engagement define lab               # strictly validate engagements/lab.json (a path is reduced to its id)
./rhubarbtart engagement list                     # defined engagements and their clone counts
./rhubarbtart engagement provision lab            # create its clones from verified images (existing names are skipped)
./rhubarbtart engagement connect lab              # open its links (ssh -R) until Ctrl-C
./rhubarbtart evidence collect|list|verify lab    # pull ~/evidence from its clones; show; check the hash chain
./rhubarbtart vault seal lab [--out DIR]          # signed, read-only evidence bundle (default: vaults/ in the repo)
./rhubarbtart vault verify DIR [--pub KEY]        # check a vault's cosign signature and every hash
./rhubarbtart engagement teardown lab [--yes] [--no-collect]   # collect evidence, then remove its clones
```

- `teardown` collects evidence first unless `--no-collect`. Confirm with the user before
  `--no-collect` or `--yes`.
- `vault seal` signs with the offline cosign key in the keychain (service
  `RhubarbTart-signing`, created by `scripts/signing-key.sh init`).
- Agents: `./rhubarbtart serve [--socket PATH]` runs the control-plane service on a 0600 Unix
  socket. `./rhubarbtart herdr arm ID` then launches the agents listed in
  `engagements/<id>.herdr.json` under herdr, each pinned to one clone through `rbt-range`.
  Commands matching the config's `tiered` patterns wait for the operator:
  `./rhubarbtart herdr pending ID`, then `./rhubarbtart herdr approve ID REQUEST` (one grant allows one
  run). Never approve on the user's behalf.

## When something fails

Read `references/troubleshooting.md`. It maps each message to its cause and fix, per family.
The general rules exist because the quick fix is usually the insecure one:

- **Never hand-edit a hash, Team ID, key fingerprint or check to get past a failure.** A
  mismatch means upstream changed, the cache is stale, or tampering. Re-derive the value
  (`resolve`, `toolchain-pin`) and let the user review the diff.
- **Don't rename a `-unverified` image by hand.** The final name means "passed the smoke test".
- **Code changes go through the `rhubarb-dev` skill**, with checks kept equally strict. An example
  is Apple or Kali changing an output string.
- **Be explicit about what you verified** and what only the user's Mac can show.
