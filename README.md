# RhubarbTart

Provenance-first, hardened [Tart](https://tart.run) images for macOS 26 (Tahoe) on Apple
silicon, with Google Chrome, OWASP ZAP and the Perimeter 81 (Check Point Harmony SASE) agent.

**Rules:**
1. No prebuilt third-party VM images. Every build starts from an Apple IPSW.
2. Every input is pinned and verified. Image inputs (IPSW, apps) are pinned in `sources.lock.json`
   and checked for hash, Developer ID signature, notarization and Team ID, on the host and again
   inside the guest. Host tools are pinned in `config/toolchain.env` and installed from upstream
   releases (no Homebrew).
3. The finished image contains no default passwords, no auto-login, no passwordless sudo and no
   password SSH. `scripts/smoke-test.sh` proves this from outside the VM, on a throwaway clone.

## Pipeline

```
config/sources.json ──resolve──▶ sources.lock.json (commit + review the diff)
                                         │
cache/ (IPSW, pkgs, dmg) ◀──verify───────┘  sha256 + Developer ID + notarization + Team ID
   │
   ├─ packer/01-vanilla.pkr.hcl  IPSW ─▶ rbt-tahoe-<build>-vanilla        local intermediate
   └─ packer/02-apps.pkr.hcl     clone ─▶ install.sh ─▶ finalize.sh ─▶ …-<inputs-sha>-unverified
                                                                   │
                                     smoke-test.sh on a disposable clone (image never booted)
                                                                   │ pass
                                          tart rename ─▶ rbt-tahoe-<build>-<inputs-sha>
                                                                   │
                                           out/<vm>.provenance.json ─▶ publish.sh (draft)
```

## Requirements

- An Apple silicon Mac. Use macOS 26 or later on the host: Virtualization.framework generally
  can't install a guest newer than the host.
- About 150 GB free: the IPSW (~20 GB), the vanilla VM and each built image (80 GB sparse disks).
- For `toolchain-pin` only: `gpg` on any machine (Linux is fine).
- Licensing: Tart 2.38.0 is released under FSL-1.1-ALv2 (© OpenAI). Check that your use is a
  "Permitted Purpose" under that license.

## Usage (macOS host, Apple silicon)

```sh
# Pinned toolchain from upstream releases into ./.toolchain (no Homebrew, no sudo).
# Needs only macOS's stock bash/curl/shasum/codesign; see "Toolchain" below.
./tools/bootstrap.sh
source scripts/env.sh               # interactive use: put .toolchain/bin first on PATH
uv run tools/resolve.py preflight   # PATH, versions, Tart.app signature vs config/toolchain.env

# Perimeter 81 has no public, versioned download: get the .pkg from the Harmony SASE
# admin portal (Devices > Downloads > Agents) and place it here:
cp ~/Downloads/Perimeter81.pkg vendor/perimeter81/

uv run tools/resolve.py plan        # what would be locked (no downloads; runs anywhere)
uv run tools/resolve.py resolve     # download + verify + write sources.lock.json
git diff sources.lock.json          # review, then commit

ssh-add ~/.ssh/id_ed25519           # the smoke test logs in with this key
RHUBARB_SSH_PUBKEYS=~/.ssh/id_ed25519.pub ./scripts/build.sh

tart run rbt-tahoe-25G83-<inputs-sha>                  # GUI; log in with the keychain password
./scripts/ssh.sh rbt-tahoe-25G83-<inputs-sha>          # key-only SSH, pinned host key
security find-generic-password -s RhubarbTart -a rbt-tahoe-25G83-<inputs-sha> -w
```

Clone for daily use instead of running the built image directly:
`tart clone rbt-tahoe-25G83-<inputs-sha> work-1`. Each clone gets its own SSH host keys.

The first `uv run` downloads uv's pinned Python 3.13 (see [Toolchain](#toolchain)).

### Configuration

| Variable | Default | Effect |
|---|---|---|
| `RHUBARB_SSH_PUBKEYS` | unset | File of ed25519/ecdsa public keys to authorize. Unset: Remote Login disabled in the image. |
| `RHUBARB_SSH_FROM` | `192.168.64.1` | `from=` restriction on those keys (Tart's shared-NAT host). Empty: no restriction. |
| `RHUBARB_USER` | `admin` | Guest admin account name (3–16 lowercase letters/digits) |
| `REBUILD_VANILLA` | `0` | `1` reinstalls from the IPSW and **rotates the admin password** |
| `RHUBARB_CACHE` | `./cache` | Where IPSW/app downloads and the guest stage dir live |
| `GITHUB_TOKEN` | unset | Optional; avoids GitHub API rate limits in `resolve`/`toolchain-pin` |

### Repository map

| Path | Purpose |
|---|---|
| `config/sources.json` | What to track: macOS major/pin, apps, expected Team IDs (hand-edited) |
| `sources.lock.json` | Resolved image inputs: URLs, hashes, signers (generated, reviewed, committed) |
| `config/toolchain.env`, `config/keys/` | Host toolchain pins; HashiCorp's release-signing key |
| `tools/resolve.py` | `plan` · `resolve` · `verify` · `preflight` · `toolchain-pin` · `provenance` |
| `tools/bootstrap.sh` | Installs the pinned toolchain into `.toolchain/` |
| `tools/check.sh` | Static checks + security-invariant greps; run before every commit |
| `packer/01-vanilla.pkr.hcl`, `packer/02-apps.pkr.hcl` | Stage 1 (IPSW → vanilla) and stage 2 (apps + hardening) |
| `guest/install.sh`, `guest/finalize.sh` | Run inside the guest: verify + install; harden + seal + power off |
| `scripts/build.sh`, `smoke-test.sh`, `ssh.sh`, `env.sh`, `publish.sh` | Host-side orchestration |
| `.claude/skills/` | Guides for Claude Code sessions working on this repo |

## Toolchain

Nothing is installed from Homebrew. `config/toolchain.env` (committed) pins the version, URL and
SHA256 of each host tool. `tools/bootstrap.sh` installs them into the git-ignored `.toolchain/`:

| Tool | Release source | Pin accepted when… | Install-time checks |
|---|---|---|---|
| Tart | `github.com/openai/tart` `tart.tar.gz` | `tart_<v>_checksums.txt` == GitHub asset digest | sha256, codesign `--deep --strict`, notarization, `TART_TEAM_ID` |
| Packer | `releases.hashicorp.com` `darwin_arm64.zip` | `SHA256SUMS` passes GPG verification against HashiCorp key `C874 011F … 72D7 468F` (`config/keys/`) | sha256, codesign Team ID if signed and pinned |
| packer-plugin-tart | `github.com/cirruslabs/packer-plugin-tart` | `SHA256SUMS` == GitHub asset digest | sha256; installed with `packer plugins install --path` (never `packer init`) |
| uv | `github.com/astral-sh/uv` | `.sha256` file == GitHub asset digest | sha256 |

- `scripts/env.sh` (sourced by every script) puts `.toolchain/bin` first on PATH, sets
  `PACKER_PLUGIN_PATH` to the local plugin dir, and sets `CHECKPOINT_DISABLE=1` (no Packer phone-home).
  It also pins uv to a uv-managed Python 3.13 (`UV_PYTHON`, `UV_PYTHON_PREFERENCE=only-managed`),
  so the host's own `python3` is never used. uv checks its Python downloads against hashes
  built into the pinned uv release, so pinning uv pins the interpreter too.
- `preflight` fails if `tart`/`packer`/`uv` resolve anywhere else (e.g. a stray Homebrew install),
  if a version is off, or if `toolchain.env` changed since the last bootstrap.
- Bumping: `uv run tools/resolve.py toolchain-pin --latest` re-derives every hash and requires
  the two upstream views to agree (it needs `gpg`, so run it on any machine that has it; the only
  output is `config/toolchain.env`). Review the diff, commit, then re-run `bootstrap.sh`.
  If the plugin version changes, update `required_plugins` in `packer/*.pkr.hcl` to match.
- `.toolchain/INSTALLED` records the installed binaries' hashes; it is embedded in each
  VM's provenance record.

## Security posture of the final image

| Area | Setting | Enforced by |
|---|---|---|
| Password | 32 random letters and digits per vanilla install, kept only in the host login keychain. Packer receives it through `PKR_VAR_password` (env, never argv). The templates have no default and reject `admin`. | `build.sh`, HCL validation |
| Login | No auto-login (`/etc/kcpassword` absent, no `autoLoginUser`); screen lock left at the macOS default | finalize + smoke test |
| sudo | No `NOPASSWD`. Provisioning pipes the password into `sudo -S`, so there's nothing to forget to remove. | finalize + smoke test (`sudo -n` must fail) |
| SSH | Key-only (`010-rhubarb.conf`, which sorts before Apple's `100-macos.conf`): no password or keyboard-interactive auth, no root, `AllowUsers <admin>`, no agent/X11 forwarding, no `tun` devices. TCP port forwarding stays allowed (e.g. to reach ZAP from the host). Keys are restricted to `from="192.168.64.1"` (Tart's shared-NAT host). The config is checked with `sshd -T` in the guest; the smoke test confirms externally that the server offers `publickey` only. | finalize + smoke test |
| SSH (no keys given) | Remote Login is disabled | finalize + smoke test |
| Host keys | Deleted at seal time and regenerated on each clone's first boot, so clones can't impersonate each other | finalize + smoke test |
| Screen Sharing | Disabled. Use Tart's window; Packer uses host-side VNC. | finalize + smoke test (5900 closed) |
| Firewall | Application firewall on, stealth mode on | finalize + smoke test |
| Apple security data | XProtect / security responses / config data auto-install on; macOS upgrades stay manual | finalize |
| SIP / Gatekeeper | Enabled, asserted in both stages and in the smoke test | all |
| Build residue | Staged inputs, caches, shell histories, cached sudo credentials, Packer's temp scripts and unified logs removed | finalize |

Keys must be ed25519 or ecdsa (optionally `-sk` hardware keys); RSA is rejected. Set
`RHUBARB_SSH_FROM` if you use softnet or a non-default vmnet subnet (empty = no `from=`).

## Provenance model

| Input | Source | Hash anchor | Signature check |
|---|---|---|---|
| Host toolchain | Upstream releases (see [Toolchain](#toolchain)) | `config/toolchain.env` | See Toolchain |
| macOS 26 IPSW | `updates.cdn-apple.com` (host enforced) | Apple CDN `x-amz-meta-digest-sha256`, cross-checked against ipsw.me | Contents are Apple-signed firmware and OS components, enforced by the VM's boot chain |
| Chrome | Google enterprise pkg (`dl.google.com/…/gcem/GoogleChrome.pkg`) | Trust on first use (TOFU) at resolve time (the URL has no version in it) | Developer ID Installer + notarized, Team `EQHXZ8M8AV` (pinned) |
| ZAP | GitHub release `ZAP_<ver>_aarch64.dmg`, version from `zaproxy/zap-admin` `ZapVersions.xml` | GitHub release asset `digest` | codesign `--deep --strict`, notarized, Team ID (pin after first resolve) |
| Perimeter 81 | Your tenant portal → `vendor/` | TOFU at resolve time | Developer ID Installer + notarized, Team ID (pin after first resolve) |

Notes:

- **`--from-ipsw=latest` is not safe here.** Apple's catalog now points `latest` at macOS 27.0
  (26A428). The resolver filters on `macos.major = 26` and picks the newest 26.x restore image
  (26.6.2 / 25G83 as of 2026-09-25). Set `macos.pin_build` to freeze a build.
- **Team IDs**: `team_id: null` means "record and warn". Confirm the observed ID with the vendor
  out-of-band and pin it. From then on a signer change fails the build.
- **"Reproducible" here means the inputs and process are reproducible, not the disk bytes.** A
  macOS install is not bit-for-bit reproducible (machine identifier, APFS metadata, timestamps).
  The VM name comes from `inputs_sha256`, a hash of the artifact hashes and signer Team IDs only
  (no timestamps). Re-resolving to the same artifacts gives the same name, and the image records
  its installed-software set in `/Library/RhubarbTart/`.
- **Only verified images get the real name.** Stage 2 builds `…-unverified`. It is renamed only
  after `smoke-test.sh` passes; on failure it is kept under the `-unverified` name for inspection.
- Chrome's enterprise URL always serves the newest build, so keep `cache/` (or mirror it
  internally). Otherwise a rebuild from an older lock fails the hash check by design.
- **The vanilla VM is a build intermediate**, not a product. It keeps password SSH so stage 2
  can reach it, and it only ever runs on Tart's host-only NAT during builds. Don't clone it for use.
- **Images built from the same vanilla VM share its admin password.** The password is set once in
  stage 1, and every stage-2 image inherits it. `REBUILD_VANILLA=1` rotates it.
- The Perimeter 81 pkg is tenant-specific. Only push images to a **private** registry.

## Development

- Run `./tools/check.sh` before every commit. It runs bash/shellcheck/Packer/Python checks, plus
  greps for regressions of the rules above: Homebrew or `packer init` creeping back, default
  credentials, `NOPASSWD`, auto-login, password SSH, SIP/Gatekeeper disabled, bash-3.2
  incompatibilities, and plugin-version drift. It works on Linux; Packer checks need `.toolchain/`.
- Host and guest scripts run under macOS `/bin/bash` 3.2. Under `set -e`, a line like `! cmd`
  never fails the script, so write negative checks as `if cmd; then die …; fi`.
- Claude Code sessions: see `.claude/skills/` (building, updating pinned inputs, changing code safely).

## Known gaps / TODO

- [ ] Run a first build on 26.6.2 to validate the Setup Assistant `boot_command` (adapted from
      [cirruslabs/macos-image-templates](https://github.com/cirruslabs/macos-image-templates)
      `vanilla-tahoe.pkr.hcl` @ `2ff087f`) and confirm on-device: sshd Include ordering, host-key
      regeneration, and that the Tart host address is `192.168.64.1`. The smoke test fails loudly
      if any of these is wrong. Also confirm the stealth-mode status wording and that the ZAP
      disk image contains `ZAP.app`.
- [ ] **Perimeter 81 system/network extensions** need approval. Since Big Sur, those policy
      payloads can only be installed by an MDM, so without MDM the user approves them on first
      launch. Never bake a signed-in agent or tenant credentials into the image.
- [ ] Standard (non-admin) daily-use account; keep the admin account for maintenance only.
- [ ] Newer 26.x releases may ship only as a `softwareupdate`, with no IPSW. Decide whether to
      apply them in a stage 1.5 (the OS verifies them, but pinning is weaker).
- [ ] Chrome/GoogleUpdater auto-updates inside clones. Disable it via policy if clones must stay identical.
- [ ] Consider `--net-softnet` for builds and for running several clones side by side (isolates VMs from each other).
- [ ] `scripts/publish.sh` is an untested draft (tart push → crane digest → cosign sign/attest).
      `cosign` and `crane` are not in the pinned toolchain yet. Add them to `toolchain.env`
      and `bootstrap.sh` (both publish release checksums) before relying on it.
- [ ] Pin `TART_TEAM_ID` (and `PACKER_TEAM_ID` if Packer's binary is signed) after the first bootstrap.
