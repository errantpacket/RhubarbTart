# Reference

Keeping a profile's inputs fresh, the configuration variables, host requirements, the pinned
toolchain and the repository map.

## Keeping inputs fresh

```sh
uv run tools/resolve.py plan kali-research       # what would change (no downloads)
uv run tools/resolve.py resolve kali-research    # re-pin; then review the diff and commit
uv run tools/resolve.py toolchain-pin --latest   # host tools; the Mac (toolchain gpg) or any OS with gpg
```

- **Review every lock diff.** Look for signer changes, URL hosts, TOFU entries and version jumps.
  The pin is only as good as the review.
- **"latest" is never trusted blindly.** Apple's "latest" IPSW is already macOS 27, so each macOS
  base filters on its major version. `pin_build` / `pin_version` in a base freezes it.
- **`team_id: null`** means "record and warn". Confirm the ID with the vendor, then pin it.
- **After a toolchain bump**, commit and re-run `./tools/bootstrap.sh` on the Mac.

Useful options:

- `resolve --skip-large` pins the OS image from its published hash but doesn't download it now.
  The build fetches it later.
- `verify --skip-image` re-checks a profile's cached packages without the OS image.
- `toolchain-pin --only gnupg` or `--only registry` re-pins only GnuPG, or only zot and cosign.

## Configuration

| Variable | Default | Effect |
|---|---|---|
| `RHUBARB_SSH_PUBKEYS` | unset | Path to a file of public keys to authorize, one per line: ed25519 or ecdsa-nistp256, optionally `-sk`; RSA is rejected. Unset: SSH disabled |
| `RHUBARB_SSH_FROM` | `192.168.64.1` | `from=` restriction on those keys (Tart's host address). Set it empty to omit `from=`. Kali's preseed server binds here too, so a Kali build needs a plain IPv4 address |
| `RHUBARB_SSH_IDENTITY` | unset | Private key `rhubarbtart` uses to reach clones (adds `-i … -o IdentitiesOnly=yes`). Unset: the `ssh-agent` and default `~/.ssh/id_*` are used. `rhubarbtart` never reads the operator's `~/.ssh/config` (`-F /dev/null`) |
| `RHUBARB_USER` | `admin` | Username for the low-level `scripts/ssh.sh` / `enroll.sh` (`rhubarbtart` reads it from the clone's record) |
| `REBUILD_VANILLA` | `0` | macOS: `1` reinstalls the vanilla VM from the IPSW and **rotates its password**. New macOS builds get a new vanilla VM automatically |
| `RHUBARB_HEADLESS` | `true` | Build: `false` shows the VM window instead of running headless (watch a build) |
| `RHUBARB_NO_COLOR` / `NO_COLOR` | unset | Build: set either to disable Packer's color (Nix writes progress to stderr, which the colored UI paints red) |
| `RHUBARB_STATE_DIR` | `~/Library/Application Support/RhubarbTart` (elsewhere `$XDG_STATE_HOME/rhubarbtart`) | Where `rhubarbtart` keeps clone records, `events.log`, run and build logs (`logs/`), the evidence store (`evidence/`) and the control-plane socket (`service.sock`) |
| `RHUBARB_SSH_WAIT` | `180` | Seconds each rotation (`new`/`reset`/`engagement provision`) waits for a clone's SSH **per boot attempt** (up to two boots) before keeping the inherited password |
| `RHUBARB_SSH_DENIED_RETRY` | `5` | Seconds between the 3 key-refusal checks before rotation concludes the key is wrong (a new guest can refuse once while booting, #88) |
| `RHUBARB_CACHE` | `./cache` | Downloads, content-addressed as `artifacts/<sha256>/<file>` so builds of the same file name never collide, per-profile guest stage dirs (`stage/`) and the local registry's storage (`registry/`) |
| `RHUBARB_REGISTRY` | `127.0.0.1:5780` | `publish.sh`: registry to publish to and verify from. Localhost is plain HTTP; anything else is HTTPS |
| `RHUBARB_REGISTRY_PORT` | `5780` | `registry.sh`: port of the local zot registry (5000 is macOS's AirPlay Receiver) |
| `RHUBARB_COSIGN_PUB` | `config/keys/rhubarb-cosign.pub` | `publish.sh verify`: public key to verify against (another publisher's) |
| `GITHUB_TOKEN` | unset | Optional; avoids GitHub API rate limits while resolving |
| `RHUBARB_TUI_MOUSE` | on (off inside herdr) | `./rhubarbtart-tui`: `1`/`0` turns mouse capture on or off; `--mouse` / `--no-mouse` override it |
| `RHUBARB_TUI_THEME` | herdr's theme inside herdr, else Textual's default | `./rhubarbtart-tui`: any Textual theme name (for example `nord`); `--theme NAME` does the same |
| `RBT_SERVICE_SOCKET` · `RBT_RANGE_CLONE` | set by `herdr arm` | `rbt-range`: the control-plane socket and the one clone this agent may drive |
| `RBT_APPROVAL_WAIT` · `RBT_APPROVAL_POLL` | `600` · `5` | `rbt-range`: seconds to wait for an operator approval of a tiered command, and between checks (`0` disables waiting) |
| `RBT_HERDR` | unset | `herdr arm`: path to the `herdr` binary. It takes priority over `PATH` and herdr's default install location |

## Requirements

- An Apple silicon Mac on macOS 26 or later. macOS 27 guests need a **macOS 27 host**.
- At most **two macOS VMs running at once** on one Mac. Apple's licence sets the limit and the
  Virtualization framework enforces it; Linux VMs don't count, and VMs from other apps (UTM,
  Parallels) do. A macOS build needs one free slot. `build.sh` and `rhubarbtart` check this and
  name the VMs that are running.
- Rosetta, for the Linux profiles that run x86_64 programs (`nixos-research`, `kali-research`):
  `softwareupdate --install-rosetta --agree-to-license`. A new Mac may not have it. The build
  and `rhubarbtart` check for it and stop with this command if it's missing.
- Free disk space for each profile you build; see [Disk space](#disk-space).
- 8 GB of memory is enough. Every build and test in these docs ran on an M1 Mac with 8 GB, with
  guests set to the bases' default of 8 GB.
- `gpg` on whichever machine resolves NixOS/Kali profiles or runs `toolchain-pin`. Any OS works.
- Licensing: RhubarbTart is FSL-1.1-ALv2 ([LICENSE.md](https://github.com/errantpacket/RhubarbTart/blob/main/LICENSE.md)). Tart 2.38.0 is also
  FSL-1.1-ALv2 (© OpenAI), so check your use is a "Permitted Purpose". Chrome and WARP are
  proprietary; NixOS allows them only by name. See [THIRD-PARTY-NOTICES.md](https://github.com/errantpacket/RhubarbTart/blob/main/THIRD-PARTY-NOTICES.md).


## Disk space

Measured on an M1 Mac with 8 GB of memory (October 2026). Sizes are what `du` reports; a macOS
image is a copy-on-write clone of its vanilla VM, so the two together can use less than the sum.

| Item | Size | Kept |
|---|---|---|
| macOS restore image (download) | 18 GB (macOS 26), 25 GB (macOS 27) | In `cache/` |
| macOS vanilla VM | 26 to 27 GB | One per macOS build, shared by every macOS profile on that build |
| macOS image (`tahoe-research`, `goldengate-research`) | 26 to 29 GB | Per profile |
| Kali installer (download) · `kali-research` image | 3.7 GB · 18 GB | |
| NixOS installer (download) · `nixos-research` image · `juiceshop-target` image | 1.7 GB · 10 GB · 4 GB | The installer is shared by NixOS profiles |
| Tool installers (Chrome, ZAP, WARP and so on) | Under 1 GB per profile | In `cache/` |

So plan for about 25 GB for Kali, 12 GB for NixOS, 70 to 85 GB for the first macOS profile on a
macOS version, and about 30 GB for each further macOS profile on the same version. A clone
starts as a copy-on-write copy of its image, using almost no space, and grows as you use it, up
to the profile's disk size (60 to 80 GB; 40 GB for `juiceshop-target`). The smoke test's clone is
deleted after each build.

Nothing is deleted for you: `rhubarbtart` never deletes images. Space builds up from:

- **Outdated images.** After a lock changes, `./rhubarbtart images` shows the old image as
  `outdated`. Move its clones on with `rhubarbtart reset NAME`, then delete it with
  `tart delete rbt-…`. A clone keeps working after its image is deleted; `list` then shows
  `image-deleted`.
- **`-unverified` images** left by a failed build. Delete them with `tart delete` once you've
  looked inside; a new build replaces them anyway.
- **Old vanilla VMs** (`rbt-<base>-<build>-vanilla`), once no current macOS profile uses that
  build. The next build of that macOS version reinstalls it.
- **Old downloads** in `cache/artifacts/`, filed by hash. Delete the folders whose hash no lock
  in `locks/` mentions. Be careful with the rest: a build downloads what its lock needs again,
  but some vendors remove old releases (Chrome keeps only recent ones). If a cached file is the
  only copy left, deleting it means re-resolving the lock before the next build.

Each image also has a keychain entry under its name (service `RhubarbTart`). After deleting an
image, remove it with `security delete-generic-password -s RhubarbTart -a rbt-…`, unless a clone
still uses the image's password (`list` shows `inherited`).

## Toolchain

`config/toolchain.env` pins the version, URL and SHA256 of each host tool, and
`tools/bootstrap.sh` installs them into the git-ignored `.toolchain/`:

| Tool | Release source | Pin accepted when… | Install-time checks |
|---|---|---|---|
| Tart | `github.com/openai/tart` | `tart_<v>_checksums.txt` == GitHub asset digest | sha256, codesign, notarization, `TART_TEAM_ID` |
| Packer | `releases.hashicorp.com` | `SHA256SUMS` GPG-verified against HashiCorp key `C874 011F … 72D7 468F` | sha256, Team ID if signed and pinned |
| packer-plugin-tart | `github.com/cirruslabs/packer-plugin-tart` | `SHA256SUMS` == GitHub asset digest | sha256; `packer plugins install --path` (never `packer init`) |
| uv | `github.com/astral-sh/uv` | `.sha256` == GitHub asset digest | sha256 |
| GnuPG (+ its libraries) | `gnupg.org` source tarballs | Detached signature by a pinned GnuPG release key (checked in pure Python) and the sha256 in gnupg.org's signed `swdb.lst` agree | sha256; built from source; may link only macOS system libraries; version check |
| zot · cosign | `github.com/project-zot/zot` · `github.com/sigstore/cosign` | Release checksums file == GitHub asset digest | sha256; version check |
| Textual (TUI only) | PyPI (`files.pythonhosted.org`) | `uv lock --script` → `tools/rhubarb_tui.py.lock` | per-file sha256, verified by uv at `uv run --script` |
| Zensical (docs site only) | PyPI (`files.pythonhosted.org`) | `uv lock --script` → `tools/docs_site.py.lock` | per-file sha256, verified by uv at `uv run --script` |

- `scripts/env.sh` puts `.toolchain/bin` first on PATH, sets `PACKER_PLUGIN_PATH`, sets
  `CHECKPOINT_DISABLE=1` (no Packer phone-home), and pins uv to its own managed Python 3.13.
- `preflight` fails if a tool resolves outside `.toolchain/`, a version is off, or the pins
  changed since the last bootstrap.
- `toolchain-pin --latest` re-derives every hash and only accepts it when two upstream views
  agree.
- **Textual** is the TUI's only third-party dependency. It is not a bootstrap binary. It is pinned to an exact version in `tools/rhubarb_tui.py`'s PEP 723 header
  and hash-locked in `tools/rhubarb_tui.py.lock` (`uv lock --script`); `./rhubarbtart-tui` runs under
  `uv run --script`, which installs it from that lock and verifies every file's sha256 (the
  `--require-hashes` equivalent). The headless render test `tools/test_rhubarb_tui.py` pins the
  same version in its own `tools/test_rhubarb_tui.py.lock`.
- **Zensical** builds the documentation site and nothing else. It is pinned in
  `tools/docs_site.py` and hash-locked in `tools/docs_site.py.lock` the same way.
- `check.sh` asserts that the pins and all three locks stay consistent.


## Repository map

| Path | Purpose |
|---|---|
| `profiles/*.json` | What to build, one file per guest |
| `config/bases/*.json` · `config/packages/*.json` | OS installers and tools: where each comes from and how it is verified |
| `config/keys/` | Pinned keys: vendor keys (HashiCorp, Google, Cloudflare, Tailscale, Kali, GnuPG) and the publisher's `rhubarb-cosign.pub` |
| `config/cosign/signing-config-offline.json` | cosign signing config that names no CA, OIDC, transparency-log or timestamp service |
| `config/toolchain.env` | Host toolchain pins |
| `locks/*.lock.json` | Resolved inputs per profile (generated, reviewed, committed) |
| `engagements/<id>.json` · `engagements/<id>.herdr.json` | Engagement scope manifests, and the optional herdr config (agents, tiered commands) |
| `vendor/` | Locally supplied installers for tools with no public URL (git ignores `.pkg`, `.dmg` and `.cer` files there; see `vendor/README.md`) |
| `tools/resolve.py` | `list` · `plan` · `resolve` · `verify` · `provenance` · `preflight` · `toolchain-pin` |
| `tools/rhubarb/{bases,packages,profiles,locks,toolchain}.py` | Base and package resolvers, profile loading, lock files and image identity, toolchain pins and preflight |
| `tools/rhubarb/{apt,gpg,pgp_ed25519,distsign,nar,macos,common}.py` | Verification: signed apt repos, pinned-key OpenPGP (via the toolchain gpg), a minimal Ed25519 OpenPGP verifier for GnuPG's own tarballs, Tailscale distsign, Nix NAR hashes, macOS signature checks, shared helpers |
| `rhubarbtart` · `tools/rhubarb_cli.py` · `tools/rhubarb/cli.py` | Clone management CLI (launcher shim, entry point, commands) |
| `tools/rhubarb/api.py` | Typed core API: the one import surface for the CLI, TUI and service |
| `tools/rhubarb/{results,logs}.py` | The dataclasses the core returns · the logs API behind the TUI Logs tab (both re-exported by `api`) |
| `tools/rhubarb/{clones,hostops}.py` | Clone record store (StrictModes rules) · tart, keychain, SSH and password rotation |
| `tools/rhubarb/engagements.py` | Engagement manifest loading and strict validation |
| `tools/rhubarb/evidence.py` · `tools/rhubarb/vault.py` | Hash-chained evidence journal with content-addressed items · signed, sealed evidence vaults |
| `tools/rhubarb/service.py` | Control-plane service: HTTP over a 0600 Unix socket (`rhubarbtart serve`) |
| `rbt-range` · `tools/rhubarb_agent.py` · `tools/rhubarb/agent.py` | Scoped range client an agent uses to run commands in its one assigned clone |
| `tools/rhubarb/{herdr,approvals}.py` | `rhubarbtart herdr arm` (launch an engagement's agents) · tiered-command approvals, ledgered in the evidence journal |
| `rhubarbtart-tui` · `tools/rhubarb_tui.py` · `tools/rhubarb_tui.py.lock` | TUI launcher shim, launcher (carries the pinned Textual) and its hashed lockfile |
| `tools/rhubarb/tui/` | Textual TUI over `api.py`: app shell (`app.py`), action dispatch (`dispatch.py`), read-only panes (`images_pane`, `clones_pane`, `provenance_pane`, `logs_pane`), table helpers (`tables.py`), herdr theme sync (`theme.py`), modals (`confirm.py`, `prompt.py`), and one module per action in `actions/` (`build`, `enroll`, `new`, `reset`, `rm`, `run`, `ssh`) |
| `tools/bootstrap.sh` · `tools/check.sh` · `tools/docs_sync.py` | Toolchain install · static checks and tests · keeps the README and the docs site in step |
| `tools/test_rhubarb.py` · `tools/tests/` · `tools/testdata/` | Offline self-tests: the runner, the tests grouped by area, and their fixtures |
| `tools/test_rhubarb_tui.py` (+ `.lock`) | Headless TUI render test (mocked core API) |
| `tools/serve_preseed.py` | One-shot preseed server bound only to Tart's host address |
| `packer/macos/` · `packer/linux/` | Build templates per family |
| `guest/macos/` · `guest/nixos/` · `guest/kali/` | In-guest verify/install (`install.sh`) and harden/seal (`finalize.sh`) scripts |
| `guest/kali/preseed.cfg.tmpl` | Unattended Kali install (rendered per build, never committed rendered) |
| `guest/nixos/nix/` | The NixOS system definition (reads the staged profile), installed as `/etc/nixos` |
| `scripts/` | `build.sh` · `smoke-test.sh` · `ssh.sh` · `enroll.sh` · `env.sh` · `registry.sh` (localhost OCI registry) · `signing-key.sh` (cosign key pair) · `publish.sh` (publish + verify signed images) · `vault.sh` (sign + verify a vault's root manifest) |
| `docs/` | These pages ([`docs/README.md`](README.md) is the home page) and `docs/images/` (screenshots). The design records with uppercase names, and `history-rewrite.md` (old to new commit hashes from the one-time rewrite before going public), stay in the repository but are left off the site |
| `zensical.toml` · `tools/docs_site.py` (+ `.lock`) · `.github/workflows/docs.yml` | The documentation site: its config, the pinned Zensical launcher, and the GitHub Pages workflow |
| `CHANGELOG.md` | Release notes; its newest version must match `__version__` in `tools/rhubarb/__init__.py` |
| `LICENSE.md` · `THIRD-PARTY-NOTICES.md` | FSL-1.1-ALv2 licence · licences of the tools RhubarbTart runs, Textual and Zensical |
| `README.md` · `CONTRIBUTING.md` · `SECURITY.md` · `CODE_OF_CONDUCT.md` | Landing page · how to contribute · how to report a vulnerability · conduct |
| `CLAUDE.md` | Short workflow notes for Claude Code sessions |
| `.github/` | CI (`check.yml`), the docs site workflow, Dependabot, `CODEOWNERS`, and the issue and PR templates |
| `out/` · `cache/` (git-ignored) | Build provenance and publication records (`*.provenance.json`, `*.published.json`) · downloads, stage dirs and registry storage |
| `.claude/skills/` | Guides for Claude Code sessions (see [Development](development.md)) |
