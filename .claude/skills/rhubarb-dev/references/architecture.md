# RhubarbTart architecture

## Contents
- [Data flow](#data-flow)
- [Who owns what](#who-owns-what)
- [Credentials per family](#credentials)
- [Seal ordering](#seal-ordering)
- [Naming and keychain](#naming)
- [Clones and records](#clones-and-records)
- [Engagements, evidence and the control plane](#control-plane)
- [TUI](#tui)
- [Testing off-Mac](#testing-off-mac)

## Data flow

```
config/toolchain.env ──bootstrap.sh──▶ .toolchain/ (PATH via scripts/env.sh)

profiles/<id>.json + config/bases/<base>.json + config/packages/*.json
      │  tools/rhubarb/profiles.py  (merge; per-family package variants; profile_sha256)
      ▼
resolve.py resolve <id>:  bases.PLANNERS[family] + packages.RESOLVERS[resolver]
      │  download, hash-check, signature-check (macos.py / gpg.py / apt.py / distsign.py / nar.py)
      ▼
locks/<id>.lock.json  (reviewed, committed)
      │
resolve.py verify <id>: cache vs lock ─▶ cache/stage/<id>/ {artifacts, SHA256SUMS, packages.tsv,
      │                                   profile.json, lock.json}  + JSON for build.sh
      ▼
build.sh <id>:
  macos  packer/macos/vanilla-{26,27} (reused per base+build) ─▶ packer/macos/apps ─▶ guest/macos/*
  nixos  packer/linux/nixos ─▶ guest/nixos/install.sh (nixos-install from nix/) ─▶ finalize.sh
  kali   serve_preseed.py + packer/linux/kali ─▶ guest/kali/install.sh ─▶ finalize.sh
      ▼
rbt-<id>-<inputs12>-unverified ─▶ smoke-test.sh (throwaway clone) ─▶ tart rename ─▶ provenance
```

## Who owns what

| Concern | File |
|---|---|
| HTTP (HTTPS-only), hashing, paths | `tools/rhubarb/common.py` |
| OpenPGP with pinned key file + fingerprint | `tools/rhubarb/gpg.py` |
| Signed apt repo → .deb (no apt needed) + dpkg version order | `tools/rhubarb/apt.py` |
| Tailscale distsign (pure-Python Ed25519, RFC 8032) | `tools/rhubarb/distsign.py` |
| Nix NAR hash (pure Python) | `tools/rhubarb/nar.py` |
| macOS pkg/dmg/app signature + Team ID | `tools/rhubarb/macos.py` |
| OS installers per family | `tools/rhubarb/bases.py` |
| GnuPG release signatures (pure-Python Ed25519 OpenPGP, used to pin gpg itself) | `tools/rhubarb/pgp_ed25519.py` |
| Tool resolvers (`RESOLVERS`) | `tools/rhubarb/packages.py` |
| Profiles (strict schema, `profile_sha256`) | `tools/rhubarb/profiles.py` |
| Locks, pinned artifacts, `inputs_sha256` (image identity) | `tools/rhubarb/locks.py` |
| Toolchain pins, preflight | `tools/rhubarb/toolchain.py` |
| Resolver CLI: plan, resolve, verify/staging, provenance, preflight, toolchain-pin | `tools/resolve.py` |
| Offline self-tests; headless TUI render test | `tools/test_rhubarb.py`; `tools/test_rhubarb_tui.py` |
| NixOS system definition | `nix/configuration.nix`, `nix/modules/{tart-vm,hardening,packages,desktop,juice-shop}.nix` |
| Kali unattended install | `guest/kali/preseed.cfg.tmpl`, `tools/serve_preseed.py` |
| Toolchain install; toolchain PATH | `tools/bootstrap.sh`; `scripts/env.sh` |
| Orchestration, passwords, naming | `scripts/build.sh` |
| External proof | `scripts/smoke-test.sh` |
| Runtime enrollment; raw SSH to a VM | `scripts/enroll.sh`; `scripts/ssh.sh` |
| Image publishing: local OCI registry, offline cosign key, push/sign | `scripts/registry.sh`, `scripts/signing-key.sh`, `scripts/publish.sh` |
| Vault root signing/verification (offline cosign) | `scripts/vault.sh` |
| Regression guard | `tools/check.sh` |
| Clone management CLI (thin adapter over the core) | `./rhubarb` → `tools/rhubarb_cli.py` → `tools/rhubarb/cli.py` → `tools/rhubarb/api.py` |
| Typed core: the single import surface for every frontend. Clone and engagement orchestration (`new`/`run`/`ssh`/`exec`/`enroll`/`reset`/`rm`, `provision`/`connect`/`teardown`, `collect`, `seal_vault`), logs (`list_logs`/`read_log`/`tail_log`) | `tools/rhubarb/api.py` |
| Clone records (StrictModes-style store, `events.log` audit trail) | `tools/rhubarb/clones.py` |
| tart / keychain / SSH / GUI-session ops, per-clone password rotation | `tools/rhubarb/hostops.py` (`ROTATE_SCRIPT`) |
| Engagement manifests (load + strict validation, no tart/keychain/network) | `tools/rhubarb/engagements.py` |
| Evidence store: append-only, hash-chained journal + content-addressed items | `tools/rhubarb/evidence.py` |
| Evidence vault: seal to a signed, read-only bundle; verify | `tools/rhubarb/vault.py` |
| Control-plane HTTP service on a 0600 Unix socket, plus its client | `tools/rhubarb/service.py` (`rhubarb serve`) |
| Scoped range client, the agent's only door into a range | `tools/rhubarb/agent.py` (`./rbt-range` → `tools/rhubarb_agent.py`) |
| herdr driver: `arm` launches an engagement's agents, pinned to clones | `tools/rhubarb/herdr.py` (config `engagements/<id>.herdr.json`) |
| Tiered-action approvals, ledgered in the evidence journal | `tools/rhubarb/approvals.py` |
| TUI: launcher with the pinned Textual; app, dispatch, panes, actions | `./rhubarb-tui` → `tools/rhubarb_tui.py` (+ `.lock`) → `tools/rhubarb/tui/` |

## Credentials

| Family | Stage | How the password gets there |
|---|---|---|
| macOS 26 | vanilla | Typed over VNC into Setup Assistant (`boot_command`); final password from the start |
| macOS 27 | vanilla | Provisioning API gets a random bootstrap password (host argv). Over SSH, `dscl -passwd` sets the final one from an uploaded 0600 file; `dscl -authonly` proves the bootstrap is dead; the guest powers itself off |
| macOS | apps | Password SSH to the vanilla clone; root via `echo pw \| sudo -S /usr/bin/env …` |
| NixOS | live ISO | `boot_command` sets a throwaway password for the live `nixos` user. The final password arrives as a 0600 file → `mkpasswd -m yescrypt -s` → `/var/lib/rhubarbtart/password.hash` (outside the store) → `hashedPasswordFile`, with `mutableUsers = false` |
| Kali | installer | The preseed carries a random bootstrap password (served only on the vmnet address). `finalize.sh` runs `chpasswd` from stdin with the final password (0600 file), verifies it with perl reading the file, shreds it, and powers off |

## Seal ordering

macOS and Kali finalize over Packer's open SSH session on the system being sealed, so order is
load-bearing:

1. **Posture:** firewall, updaters, Screen Sharing (macOS).
2. **SSH policy:** write the drop-in plus `authorized_keys`, then validate with `sshd -t` and
   `sshd -T`. Both need host keys, so this comes before step 5.
3. **Assertions:** SIP/Gatekeeper, sudo, auto-login, root, firewall, and so on.
4. **Residue:** remove the stage dir, caches, histories, logs, sudo timestamps and Packer temp
   scripts, plus **VPN state** (stop the services first on Linux).
5. **Identity:** delete SSH host keys, empty the machine-id. Kali adds a first-boot unit to
   regenerate keys; macOS regenerates by itself; NixOS never generated any.
6. **Password rotation (Kali):** it goes last because nothing after it may need sudo.
7. **Delayed self-poweroff** plus `expect_disconnect`, then a `shell-local` step that waits for
   `tart get` to report `Running: false`.

**NixOS is different.** `guest/nixos/finalize.sh` runs in the live ISO against the *installed but
never-booted* system in `/mnt`. It asserts on the built configuration (the generated
`sshd_config`, sudoers, LightDM, and the password-hash mode) rather than running `sshd -t`/`-T`.
It deletes `/etc/machine-id` (NixOS creates it on first boot) instead of emptying it, removes VPN
state, unmounts, and powers off. No password rotation is needed: the final password only ever
existed as a hash.

## Naming

- The VM name is `rbt-<profile>-<inputs_sha256[:12]>`. The build candidate gets
  `-unverified` appended, and the smoke clone is `<candidate>-smoke-<pid>`.
- The macOS vanilla VM is `rbt-<base>-<build>-vanilla`, reused across profiles on that base.
- Keychain service `RhubarbTart`, account = VM name (and the vanilla name for macOS).
  Enrollment secrets use service `RhubarbTart-enroll`.

## Clones and records

`rhubarb new` clones a verified image (from the profile's committed lock, or `--image`), then
writes `~/Library/Application Support/RhubarbTart/clones/NAME.json` with the clone's profile,
family, source image, username, Rosetta flag, `password_account`, `created_at`, its `engagement`
tag (`None` for ad-hoc clones), an optional `base` (stacked registry clones, #31) and
enrollments. It then boots the clone headless and runs `ROTATE_SCRIPT` over SSH. stdin carries the current and new
passwords; macOS uses `dscl -passwd`, NixOS writes a new yescrypt hash to
`/var/lib/rhubarbtart/password.hash` and applies it with `chpasswd -e`, and Kali uses `chpasswd`.
The script proves via `sudo -v` that the old password is rejected and the new one works. On
success `password_account` becomes the clone's name. Every mutating command appends to
`events.log`. Each clone's run log is `logs/<clone>.log` in the state dir.

## Control plane

Engagements, evidence, the service and agents all live on the host and reach VMs only through
`api.py`. Keep these properties; each has a test in `tools/test_rhubarb.py`.

- **Engagement manifests** (`engagements/<id>.json`) are strictly validated like profiles: an
  unknown key or bad value is rejected. `provision` creates the ranges' clones (existing names
  are skipped), tagged with the engagement. `connect` opens each `links` entry as an SSH remote
  forward (`ssh -R 127.0.0.1:P:<target-ip>:P`) into the `from` clone, only for ports the target's
  profile declares. Clones otherwise can't reach each other on Tart's default NAT (#30).
- **Evidence** (`state_dir/evidence/<id>/`): `journal.jsonl` is opened `O_APPEND|O_NOFOLLOW`,
  0600, under an exclusive `flock`. Each entry carries `prev` (the previous entry's hash) and its
  own `hash`, so `verify()` catches an edit, reorder or deletion. Items are content-addressed
  (`items/<sha256>`). `api.exec` journals a command in an engagement clone before returning;
  `api.collect` pulls `~/evidence` from each clone and parses it. check.sh forbids
  `extract`/`extractall` in `api.py` and `evidence.py`, because the guest is under test.
- **Vaults**: `vault.seal` refuses a chain that doesn't verify, writes `root.json` (scope, chain
  head, journal and item hashes, each range's provenance), signs it through `scripts/vault.sh`
  (offline cosign key from the keychain service `RhubarbTart-signing`), then makes the tree
  read-only (0400 files, 0500 dirs). `vault verify` checks the signature, then re-derives every
  hash.
- **Service** (`service.py`): HTTP over a Unix socket created under `umask 0o177` and chmod 0600;
  no TCP, no token, filesystem permissions are the boundary. It refuses to replace a non-socket
  at its path. Every endpoint is one `api` call. check.sh forbids `subprocess`/`hostops`/
  `security` in `service.py` and `agent.py`.
- **Range client** (`agent.py`, `rbt-range`): runs a command in the one clone named by
  `RBT_RANGE_CLONE` through `POST /clones/<clone>/exec` on `RBT_SERVICE_SOCKET`. The agent holds
  no clone password or route. On the host (charter model A) this is the sanctioned, recorded
  path, not a kernel sandbox; don't describe it as one.
- **herdr** (`herdr.py`): `arm` refuses unless the engagement is provisioned, each agent's clone
  belongs to it, the service is running and herdr is installed. It records the config's hash as
  an `arm` evidence entry. check.sh forbids direct `hostops`/`tart`/`security` in `herdr.py`.
- **Approvals** (`approvals.py`): a command matching the herdr config's `tiered` regexes is held
  (`requested` entry) instead of run. `rhubarb herdr approve` records one `granted` entry; the
  next identical exec records `consumed` and runs. One grant allows one run. The ledger is the
  evidence journal; there is no other state.

## TUI

`tools/rhubarb_tui.py` is only the launcher: it carries Textual pinned to an exact version and
hash-locked in `tools/rhubarb_tui.py.lock` (check.sh verifies the pin and the lock, and that
`tools/test_rhubarb_tui.py` pins the same version). The app is in `tools/rhubarb/tui/`:

- `app.py`: layout, polling and provenance routing. `dispatch.py`: the action-dispatch mixin.
- Panes (`images_pane`, `clones_pane`, `provenance_pane`, `logs_pane`) are read-only. Each reads
  in `fetch()` on a worker thread and applies it in `render_data()` on the UI thread, so a slow
  `tart list` never freezes the UI. `tables.py` keeps the selected row across rebuilds.
- `actions/{build,enroll,new,reset,rm,run,ssh}.py` each expose `handle(ctx)` and import `api`
  lazily. Destructive actions (`rm`, `reset`) go through `confirm.py` first; `prompt.py`
  gathers names and choices. `build` alone shells out to `scripts/build.sh` (builds are not in
  the core), refuses in an SSH session, and writes `logs/build-<profile>-<UTC>.log` (0600, via
  `api.new_build_log`) with the VNC password redacted.
- The TUI is a thin client of `api.py`: it never calls `tart` or the keychain directly.
- `api.read_log`/`tail_log` accept only ids from `list_logs()` (`events.log` or
  `logs/<name>.log`), open them with `O_NOFOLLOW` and require a regular file inside the state
  dir. `tail_log` returns only the bytes added since its cursor.

## Testing off-Mac

- **Resolvers:** `uv run tools/resolve.py plan <profile>` (no downloads), then
  `resolve <nixos-or-kali-profile> --skip-large` (skips OS images), then
  `verify <profile> --skip-image` (stages everything except the OS image).
- **NixOS evaluation:** run it against the real pinned nixpkgs in the Nix container this repo was
  tested with (the digest is pinned). First run `resolve nixos-research --skip-large` and
  `verify nixos-research --skip-image`, so that `cache/artifacts/` and `cache/stage/` exist:
  ```sh
  N=nixos/nix@sha256:85169a7ff4ac6928b70b15ced20c74770e07e8fbc7f97e92f64c1fca47ea9486
  mkdir -p /tmp/np /tmp/cfg && tar -xzf cache/artifacts/*/nixpkgs-*.tar.gz -C /tmp/np --strip-components=1
  cp -r nix/. /tmp/cfg/ && cp cache/stage/nixos-research/{profile.json,lock.json} /tmp/cfg/
  echo '{"from":"192.168.64.1"}' > /tmp/cfg/ssh.json && cp ~/.ssh/id_ed25519.pub /tmp/cfg/authorized_keys
  mkdir -p /tmp/cfg/artifacts   # a profile whose module builds from a staged file (juice-shop) needs it here
  docker run --rm -v /tmp/np:/nixpkgs:ro -v /tmp/cfg:/cfg $N nix-instantiate \
    -I nixpkgs=/nixpkgs -I nixos-config=/cfg/configuration.nix '<nixpkgs/nixos>' -A system
  ```
  Evaluate specific settings with `--eval --strict --json -E '(import <nixpkgs/nixos> {}).config.<path>'`.
- **NAR hash vs Nix:** use the same container, with `nix hash path <dir>` or
  `nix-prefetch-url --unpack <url>` followed by `nix hash convert --to sri`.
- **Shell transports** (for example `enroll.sh`): run the embedded scripts with stub `sudo` and
  tool binaries on PATH, and assert what the stubs received and that temp secrets are gone.
- **rhubarb CLI:** `tools/test_rhubarb.py` drives the real CLI against stand-in `tart`,
  `security` and `ssh` programs (`test_cli_lifecycle`), and the rotation script against a
  simulated guest for all three OS paths (`test_rotation_script`). Both run in `check.sh`.
- **Control plane:** the same file covers evidence (`test_evidence_store`,
  `test_evidence_exec_collect`), vaults (`test_vault_seal_verify`, with a fake signer), the
  service and range client (`test_control_plane_service`, `test_scoped_range_client`), herdr and
  approvals (`test_herdr_arm`, `test_tiered_approvals`) and logs (`test_logs_api`).
- **TUI:** `uv run --script tools/test_rhubarb_tui.py` renders the app headless (no `tart`).
