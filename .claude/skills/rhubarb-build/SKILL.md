---
name: rhubarb-build
description: Build, run, enroll and troubleshoot RhubarbTart guest VMs (Tart + Packer) from their profiles — macOS 26/27, NixOS and Kali. Covers bootstrapping the pinned toolchain, preflight, scripts/build.sh PROFILE, the smoke test, finding the guest password in the keychain, cloning, running with Rosetta, SSH, VPN/ZTNA enrollment (Tailscale, Cloudflare WARP, Perimeter 81) via scripts/enroll.sh, and diagnosing failures (Setup Assistant or provisioning hangs, Kali preseed/GRUB, NixOS install, finalize or smoke-test errors, hash/signature/Team ID mismatches, -unverified images). Use this whenever someone wants to make, rebuild, run, clone, connect to or enroll a research VM, or says a build, bootstrap, enrollment or smoke test failed, even if they don't mention Tart or Packer. For designing a guest use rhubarb-profiles; for refreshing versions use rhubarb-update-inputs.
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
- Shell state doesn't persist between your commands. **On the Mac**, prefix them with
  `source scripts/env.sh &&`, otherwise preflight (correctly) refuses other `tart`/`packer`/`uv`
  installs. **On a Linux resolve host** there is no `.toolchain/` (env.sh would exit), so run
  `uv run tools/resolve.py …` directly there.

## The sequence (and why each step exists)

```sh
./tools/bootstrap.sh                                   # 1. pinned toolchain -> .toolchain/
source scripts/env.sh && uv run tools/resolve.py preflight  # 2. right binaries, versions, signer
uv run tools/resolve.py resolve <profile>              # 3. only when refreshing inputs
ssh-add ~/.ssh/id_ed25519                              # 4. the smoke test logs in with it
RHUBARB_SSH_PUBKEYS=~/.ssh/id_ed25519.pub ./scripts/build.sh <profile>   # 5.
```

1. Run **bootstrap** once, and again whenever `config/toolchain.env` changes.
2. **preflight** also runs inside build.sh; running it first gives a clearer failure.
3. **resolve** rewrites `locks/<profile>.lock.json`, which is what gets built. Don't run it just to
   build: a committed lock plus `cache/` is enough. Refreshing means reviewing the diff and
   committing before building; use the `rhubarb-update-inputs` skill for that.
4. Without the key in the agent, the smoke test can't prove key login works.
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
- **`RHUBARB_SSH_FROM`** only changes if they use softnet or a non-default vmnet subnet. Kali
  also binds its preseed server to that address, so it must be the vmnet host IPv4.

## Using a built image

```sh
tart clone rbt-<profile>-<sha> work-1          # always clone; keep the built image pristine
tart run work-1                                # macOS
tart run --rosetta=rosetta work-1              # Linux profiles with "rosetta": true
./scripts/ssh.sh work-1                        # key-only; host key pinned per VM name
./scripts/enroll.sh work-1 tailscale --image rbt-<profile>-<sha>   # or: warp --org <team> | perimeter81
security find-generic-password -s RhubarbTart -a rbt-<profile>-<sha> -w
```

- **Passwords** are stored under the built image's name (and the vanilla name for macOS).
  Clones inherit the image's password, so `enroll.sh` takes the running clone as its target and
  `--image` for the keychain lookup.
- **Usernames:** `ssh.sh` and `enroll.sh` assume `admin`. For a profile with another
  `username`, set `RHUBARB_USER=that-name`.
- **Don't print passwords or enrollment secrets** into the conversation unless asked.
- **Enrollment secrets** live in the host keychain (service `RhubarbTart-enroll`). The header of
  `scripts/enroll.sh` shows how to add them. Never put them in the repo, a profile, or an image.
- **macOS + Tailscale:** the user must approve the system extension in the VM once per clone,
  and afterwards remove the auth-key policy
  (`sudo defaults delete /Library/Preferences/io.tailscale.ipn.macsys AuthKey`); enroll.sh
  prints this.

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
