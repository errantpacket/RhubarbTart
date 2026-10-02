# Quick start

This page takes you from a fresh checkout to a running Kali clone. The same steps build any
profile; [Guests and profiles](profiles.md) lists them.

## Requirements

- An Apple silicon Mac on macOS 26 or later. The macOS 27 guest needs a macOS 27 host.
- The Xcode Command Line Tools (`xcode-select --install`). The bootstrap builds GnuPG with them.
- For the Kali and NixOS profiles, Rosetta:
  `softwareupdate --install-rosetta --agree-to-license`. The build checks for it and stops with
  this command if it's missing.
- About 100 to 150 GB of free disk space per profile you build.
- A Terminal window on the Mac itself. Builds write passwords to your login keychain, which a
  session started over SSH can't do.

You don't need Homebrew or `sudo`. Everything the build uses is downloaded, checked against a
pinned hash and installed into the repository's `.toolchain/` folder.

## The steps

```sh
# 1. Install the pinned tools into ./.toolchain (no Homebrew, no sudo)
./tools/bootstrap.sh && source scripts/env.sh && uv run tools/resolve.py preflight

# 2. Optional: refresh the guest's inputs to their newest versions, then review the changes.
#    Skip this to build exactly what the repository pins.
# uv run tools/resolve.py resolve kali-research && git diff locks/

# 3. Create an SSH key for your VMs (first time only; set a passphrase when asked),
#    keep its passphrase in your keychain, then build and test the image
[ -f ~/.ssh/rhubarbtart_ed25519 ] || ssh-keygen -t ed25519 -a 100 -C rhubarbtart -f ~/.ssh/rhubarbtart_ed25519
ssh-add --apple-use-keychain ~/.ssh/rhubarbtart_ed25519
RHUBARB_SSH_PUBKEYS=~/.ssh/rhubarbtart_ed25519.pub ./scripts/build.sh kali-research

# 4. Make a clone and start it; don't use the image directly
./rhubarbtart new web-1 --profile kali-research && ./rhubarbtart run web-1
```

`source scripts/env.sh` lasts only for that terminal. Run it again in each new terminal before
`build.sh` or `uv run`. The `./rhubarbtart` commands don't need it.

## What each step does

1. **Bootstrap** downloads Tart, Packer and its Tart plugin, uv, zot and cosign, and builds GnuPG
   from source. Each is checked against a pinned hash and installed into the repository's own
   `.toolchain/` folder. `preflight` then confirms that these are the versions on your `PATH`.
   Building GnuPG needs the Xcode Command Line Tools.
2. **Resolve** is optional. The included profiles have committed locks, and the build downloads
   and verifies exactly those versions. Resolve looks up the newest versions instead, verifies
   them and rewrites `locks/kali-research.lock.json`, which changes the image's name and leaves
   your checkout modified. Run it when you want newer versions, or when the build reports that a
   pinned download is gone (vendors such as Google keep only recent releases).
3. **Your SSH key** gets you into your VMs. Use a key only for them, not the one you use for
   GitHub or servers. The first line creates it only if it doesn't exist yet. `ssh-add
   --apple-use-keychain` loads it and stores its passphrase in your macOS keychain; after a
   restart, run `ssh-add --apple-load-keychain` to load it again without typing the passphrase.
   The public key is built into the image, so a new key means rebuilding your images. Hardware
   security keys and other options: [Your SSH key](using.md#your-ssh-key). Without a key,
   the image is built with SSH turned off.

   **Build** installs the OS from the vendor's installer, adds the tools, and **seals** the guest:
   it applies the hardening and checks that each setting took effect. It then starts a temporary
   copy and tests it from the outside. Only if those tests pass does the image get its final name.
   A build is a full OS install and can take 15 to 45 minutes, so run it in a separate terminal.
   `./scripts/build.sh --list` lists the available profiles.
4. **`rhubarbtart new`** creates a clone and gives it its own password, stored in your keychain.
   `rhubarbtart run` starts it in a window and keeps the terminal until the VM shuts down; add
   `--detach` to get the terminal back. [Using your VMs](using.md) covers `ssh`, `enroll`,
   `reset`, `rm` and the dashboard.

## If a step fails

Each failure message names what didn't hold. [Troubleshooting](troubleshooting.md) maps the
messages to their causes and fixes. A build that fails its tests leaves the image under its
`-unverified` name; it is never renamed by hand.

## Next steps

- [Key concepts](concepts.md) explains profiles, locks, images and clones.
- [Using your VMs](using.md) covers the everyday commands and the `./rhubarbtart-tui` dashboard.
- [Engagements and evidence](engagements.md) shows how to scope work and keep evidence.
- [Guests and profiles](profiles.md) helps you pick another guest or define your own.
