# Reference

Keeping a profile's inputs fresh, plus the configuration variables, host requirements, pinned toolchain and repository map.

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

## Reference

### Configuration

| Variable | Default | Effect |
|---|---|---|
| `RHUBARB_SSH_PUBKEYS` | unset | Public keys (ed25519/ecdsa, optionally `-sk`; RSA rejected) to authorize. Unset: SSH disabled |
| `RHUBARB_SSH_FROM` | `192.168.64.1` | `from=` restriction on those keys (Tart's host address); Kali's preseed server binds here too |
| `RHUBARB_SSH_IDENTITY` | unset | Private key `rhubarb` uses to reach clones (adds `-i … -o IdentitiesOnly=yes`). Unset: the `ssh-agent` and default `~/.ssh/id_*` are used. `rhubarb` never reads the operator's `~/.ssh/config` (`-F /dev/null`) |
| `RHUBARB_USER` | `admin` | Username for the low-level `scripts/ssh.sh` / `enroll.sh` (`rhubarb` reads it from the clone's record) |
| `REBUILD_VANILLA` | `0` | macOS: `1` reinstalls the vanilla VM from the IPSW and **rotates its password**. New macOS builds get a new vanilla VM automatically |
| `RHUBARB_HEADLESS` | `true` | Build: `false` shows the VM window instead of running headless (watch a build) |
| `RHUBARB_NO_COLOR` / `NO_COLOR` | unset | Build: set either to disable Packer's color (Nix writes progress to stderr, which the colored UI paints red) |
| `RHUBARB_STATE_DIR` | `~/Library/Application Support/RhubarbTart` | Where `rhubarb` keeps clone records and `events.log` |
| `RHUBARB_SSH_WAIT` | `180` | Seconds each rotation (`new`/`reset`/`engagement provision`) waits for a clone's SSH **per boot attempt** (the boot is retried twice) before keeping the inherited password |
| `RHUBARB_CACHE` | `./cache` | Downloads, content-addressed as `artifacts/<sha256>/<file>` so builds of the same file name never collide, and per-profile guest stage dirs (`stage/`) |
| `RHUBARB_REGISTRY` | `127.0.0.1:5780` | `publish.sh`: registry to publish to and verify from. Localhost is plain HTTP; anything else is HTTPS |
| `RHUBARB_REGISTRY_PORT` | `5780` | `registry.sh`: port of the local zot registry (5000 is macOS's AirPlay Receiver) |
| `RHUBARB_COSIGN_PUB` | `config/keys/rhubarb-cosign.pub` | `publish.sh verify`: public key to verify against (another publisher's) |
| `GITHUB_TOKEN` | unset | Optional; avoids GitHub API rate limits while resolving |

### Requirements

<details>
<summary>Host, disk space, gpg and licensing (expand)</summary>

- An Apple silicon Mac on macOS 26 or later. macOS 27 guests need a **macOS 27 host**.
- About 100–150 GB free per built profile: OS images are 3–20 GB and VM disks 60–80 GB (sparse).
- `gpg` on whichever machine resolves NixOS/Kali profiles or runs `toolchain-pin`. Any OS works.
- Licensing: Tart 2.38.0 is FSL-1.1-ALv2 (© OpenAI), so check your use is a "Permitted Purpose".
  Chrome and WARP are proprietary; NixOS allows them only by name.

</details>

### Toolchain

<details>
<summary>Pinned, verified, repo-local; no Homebrew (expand)</summary>

`config/toolchain.env` pins the version, URL and SHA256 of each host tool, and
`tools/bootstrap.sh` installs them into the git-ignored `.toolchain/`:

| Tool | Release source | Pin accepted when… | Install-time checks |
|---|---|---|---|
| Tart | `github.com/openai/tart` | `tart_<v>_checksums.txt` == GitHub asset digest | sha256, codesign, notarization, `TART_TEAM_ID` |
| Packer | `releases.hashicorp.com` | `SHA256SUMS` GPG-verified against HashiCorp key `C874 011F … 72D7 468F` | sha256, Team ID if signed and pinned |
| packer-plugin-tart | `github.com/cirruslabs/packer-plugin-tart` | `SHA256SUMS` == GitHub asset digest | sha256; `packer plugins install --path` (never `packer init`) |
| uv | `github.com/astral-sh/uv` | `.sha256` == GitHub asset digest | sha256 |
| Textual (TUI only) | PyPI (`files.pythonhosted.org`) | `uv lock --script` → `tools/rhubarb_tui.py.lock` | per-file sha256, verified by uv at `uv run --script` |

- `scripts/env.sh` puts `.toolchain/bin` first on PATH, sets `PACKER_PLUGIN_PATH`, sets
  `CHECKPOINT_DISABLE=1` (no Packer phone-home), and pins uv to its own Python 3.13.
- `preflight` fails if a tool resolves outside `.toolchain/`, a version is off, or the pins
  changed since the last bootstrap.
- `toolchain-pin --latest` re-derives every hash and only accepts it when two upstream views
  agree.
- **Textual** — the TUI's only third-party dependency, and the repo's *first* — is not
  a bootstrap binary. It is pinned to an exact version in `tools/rhubarb_tui.py`'s PEP 723 header
  and hash-locked in `tools/rhubarb_tui.py.lock` (`uv lock --script`); `./rhubarb-tui` runs under
  `uv run --script`, which installs it from that lock and verifies every file's sha256 (the
  `--require-hashes` equivalent). `check.sh` asserts the pin and the lock stay consistent.

</details>

### Repository map

<details>
<summary>What lives where (expand)</summary>

| Path | Purpose |
|---|---|
| `profiles/*.json` | What to build, one file per guest |
| `config/bases/*.json` · `config/packages/*.json` | OS installers and tools: where each comes from and how it is verified |
| `config/keys/` | Pinned vendor keys (HashiCorp, Google, Cloudflare, Tailscale, Kali) |
| `config/toolchain.env` | Host toolchain pins |
| `locks/*.lock.json` | Resolved inputs per profile (generated, reviewed, committed) |
| `tools/resolve.py` · `tools/rhubarb/` | `list` · `plan` · `resolve` · `verify` · `provenance` · `preflight` · `toolchain-pin` |
| `rhubarb-tui` · `tools/rhubarb_tui.py` · `tools/rhubarb/tui/` · `tools/rhubarb_tui.py.lock` | Textual TUI over `rhubarb/api.py` (read-only panes + confirm-gated write actions): launcher shim, app shell, pane widgets, action modules, and the pinned + hashed Textual lockfile |
| `tools/bootstrap.sh` · `tools/check.sh` · `tools/test_rhubarb.py` | Toolchain install · static checks · offline crypto/parsing self-tests |
| `tools/serve_preseed.py` | One-shot preseed server bound only to Tart's host address |
| `packer/macos/` · `packer/linux/` | Build templates per family |
| `guest/macos/` · `guest/nixos/` · `guest/kali/` | In-guest verify/install and harden/seal scripts |
| `nix/` | The NixOS system definition (reads the staged profile) |
| `kali/preseed.cfg.tmpl` | Unattended Kali install (rendered per build, never committed rendered) |
| `rhubarb` · `tools/rhubarb_cli.py` · `tools/rhubarb/{cli,clones,hostops}.py` | Clone management CLI, record store, tart/keychain/SSH operations |
| `scripts/` | `build.sh` · `smoke-test.sh` · `ssh.sh` · `enroll.sh` · `env.sh` · `registry.sh` (localhost OCI registry) · `signing-key.sh` (cosign key pair) · `publish.sh` (publish + verify signed images) |
| `.claude/skills/` | Guides for Claude Code sessions (see [Development](development.md)) |

</details>

← back to the [README](../README.md)
