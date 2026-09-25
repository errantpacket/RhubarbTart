---
name: rhubarb-build
description: Build, run and troubleshoot RhubarbTart macOS images with Tart and Packer in this repo. Covers bootstrapping the pinned toolchain, preflight, resolving/verifying inputs, scripts/build.sh, the smoke test, finding the guest password in the keychain, SSH into clones, and diagnosing failures (Setup Assistant boot_command hangs, finalize.sh or smoke-test errors, hash/Team ID mismatches, -unverified images). Use this whenever someone wants to make, rebuild, run, clone or connect to an image, or says the build/bootstrap/smoke test failed, even if they don't mention Tart or Packer by name.
---

# Building and running RhubarbTart images

The pipeline turns an Apple IPSW plus a locked set of signed apps into a hardened Tart VM,
then proves the hardening from outside before giving the image its real name. Your job is to
drive that pipeline and, when it fails, find the cause without weakening any check. Every
failure message in this repo means a real guarantee didn't hold.

## Before you start

- Builds only run on an **Apple silicon Mac** (macOS 26+ host). On Linux you can still run
  `uv run tools/resolve.py plan` and `./tools/check.sh`, but not bootstrap, resolve or build,
  because signature checks need `codesign`/`pkgutil`/`spctl`. Say so rather than attempting them.
- Shell state doesn't persist between your commands, so prefix commands with
  `source scripts/env.sh &&`. Without it, `tart`/`packer`/`uv` may resolve to other installs and
  preflight will (correctly) refuse to run.

## The sequence (and why each step exists)

```sh
./tools/bootstrap.sh                          # 1. pinned tart/packer/plugin/uv → .toolchain/
source scripts/env.sh
uv run tools/resolve.py preflight             # 2. right binaries on PATH, versions, Tart signer
uv run tools/resolve.py resolve               # 3. only when refreshing inputs (see below)
ssh-add ~/.ssh/id_ed25519                     # 4. smoke test logs in with this key
RHUBARB_SSH_PUBKEYS=~/.ssh/id_ed25519.pub ./scripts/build.sh   # 5. build + smoke + rename
```

1. **bootstrap** is needed once, and again whenever `config/toolchain.env` changes (preflight tells you).
2. **preflight** runs inside build.sh too; running it first gives a faster, clearer failure.
3. **resolve** changes `sources.lock.json`, which is what gets built. Don't run it just to
   build; only run it when the user wants newer macOS/apps. Then the lock diff needs review and
   a commit *before* building (use the `rhubarb-update-inputs` skill). An existing committed
   lock plus `cache/` is enough to build.
4. The SSH key must be in the agent, or the smoke test can't prove key login works.
5. **build.sh** verifies the cache against the lock, builds the vanilla VM (only if missing),
   builds `rbt-tahoe-<build>-<inputs-sha>-unverified`, smoke-tests a throwaway clone, then
   renames it to the final name and writes `out/<vm>.provenance.json`.

Stage 1 installs macOS from the IPSW and drives Setup Assistant by keystrokes. It takes a while
(roughly 30–60 minutes). Run build.sh in the background and check on it rather than blocking.

### Choices to confirm with the user

- **SSH keys**: without `RHUBARB_SSH_PUBKEYS`, the image ships with Remote Login *disabled*.
  That's secure and valid, but confirm it's intended.
- **`REBUILD_VANILLA=1`**: reinstalls from the IPSW and rotates the admin password. Needed after
  changing the macOS build, or when the vanilla VM's keychain entry is lost. Costs another stage-1 run.
- `RHUBARB_SSH_FROM` only needs changing if they use softnet or a non-default vmnet subnet.

## Using a built image

```sh
tart clone rbt-tahoe-<build>-<sha> work-1     # use clones; keep the built image pristine
tart run work-1                                # GUI login with the keychain password
./scripts/ssh.sh work-1                        # key-only SSH, host key pinned per VM name
security find-generic-password -s RhubarbTart -a rbt-tahoe-<build>-<sha> -w
```

The password is stored under the *final* image name and under the vanilla name
(`rbt-tahoe-<build>-vanilla`). A `-unverified` image has no entry of its own; use the vanilla
one. Don't print the password into the conversation unless the user asks for it.

## When something fails

Read `references/troubleshooting.md`. It maps each error message to its cause and fix. General
rules, because the "quick fix" is usually the insecure one:

- **Never edit a hash, Team ID or check just to get past a failure.** A mismatch means upstream
  changed, the cache is stale, or something was tampered with. Find out which. For pins,
  re-derive them (`toolchain-pin`, `resolve`) and let the user review the diff.
- **Don't rename a `-unverified` image by hand.** The final name means "passed the smoke test".
  Inspect it, fix the cause, rebuild.
- If the fix needs a code change (e.g. Apple changed an output string on a new macOS build),
  switch to the `rhubarb-dev` skill and keep the check as strict as it was.
- Be explicit about what you verified versus what only the user's Mac can show.
