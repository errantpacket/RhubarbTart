# RhubarbTart architecture

## Contents
- [Data flow](#data-flow)
- [Who owns what](#who-owns-what)
- [Credentials per family](#credentials)
- [Seal ordering](#seal-ordering)
- [Naming and keychain](#naming)
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
| Tool resolvers | `tools/rhubarb/packages.py` |
| Profiles, locks | `tools/rhubarb/profiles.py` |
| Toolchain pins, preflight | `tools/rhubarb/toolchain.py` |
| CLI; artifacts, inputs_sha256, staging, provenance | `tools/resolve.py` |
| Offline self-tests | `tools/test_rhubarb.py` |
| NixOS system definition | `nix/configuration.nix`, `nix/modules/{tart-vm,hardening,packages,desktop}.nix` |
| Kali unattended install | `kali/preseed.cfg.tmpl`, `tools/serve_preseed.py` |
| Orchestration, passwords, naming | `scripts/build.sh` |
| External proof | `scripts/smoke-test.sh` |
| Runtime enrollment | `scripts/enroll.sh` |
| Regression guard | `tools/check.sh` |

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

## Testing off-Mac

- **Resolvers:** `uv run tools/resolve.py plan <profile>` (no downloads), then
  `resolve <nixos-or-kali-profile> --skip-large` (skips OS images), then
  `verify <profile> --skip-image` (stages everything except the OS image).
- **NixOS evaluation:** run it against the real pinned nixpkgs in the Nix container this repo was
  tested with (the digest is pinned). First run `resolve nixos-research --skip-large` and
  `verify nixos-research --skip-image`, so that `cache/artifacts/` and `cache/stage/` exist:
  ```sh
  N=nixos/nix@sha256:85169a7ff4ac6928b70b15ced20c74770e07e8fbc7f97e92f64c1fca47ea9486
  mkdir -p /tmp/np /tmp/cfg && tar -xzf cache/artifacts/nixpkgs-*.tar.gz -C /tmp/np --strip-components=1
  cp -r nix/. /tmp/cfg/ && cp cache/stage/nixos-research/{profile.json,lock.json} /tmp/cfg/
  echo '{"from":"192.168.64.1"}' > /tmp/cfg/ssh.json && cp ~/.ssh/id_ed25519.pub /tmp/cfg/authorized_keys
  docker run --rm -v /tmp/np:/nixpkgs:ro -v /tmp/cfg:/cfg $N nix-instantiate \
    -I nixpkgs=/nixpkgs -I nixos-config=/cfg/configuration.nix '<nixpkgs/nixos>' -A system
  ```
  Evaluate specific settings with `--eval --strict --json -E '(import <nixpkgs/nixos> {}).config.<path>'`.
- **NAR hash vs Nix:** use the same container, with `nix hash path <dir>` or
  `nix-prefetch-url --unpack <url>` followed by `nix hash convert --to sri`.
- **Shell transports** (for example `enroll.sh`): run the embedded scripts with stub `sudo` and
  tool binaries on PATH, and assert what the stubs received and that temp secrets are gone.
