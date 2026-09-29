# Troubleshooting RhubarbTart builds (all families)

Find the failing message below (they're quoted as the scripts print them). Each entry gives the
likely cause, how to confirm it, and the fix. Fixes never loosen a check. If a check itself
is wrong for a new macOS build, fix it with the same strictness (see the `rhubarb-dev` skill).

## Contents
- [Toolchain: bootstrap / env.sh / preflight](#toolchain)
- [Profiles and locks](#profiles-and-locks)
- [Inputs: resolve / verify](#inputs)
- [Stage 1: vanilla install](#stage-1) (macOS 26 keystrokes, macOS 27 provisioning)
- [Stage 2: install.sh / finalize.sh](#stage-2) (macOS)
- [NixOS](#nixos)
- [Kali](#kali)
- [Enrollment](#enrollment)
- [rhubarb CLI](#rhubarb-cli)
- [Smoke test](#smoke-test)
- [Inspecting a failed image](#inspecting)

## Toolchain

| Message | Cause | Fix |
|---|---|---|
| `toolchain missing; run ./tools/bootstrap.sh` | `.toolchain/` absent (fresh clone) | Run bootstrap |
| `tart resolves to …, not …/.toolchain/bin/tart` | env.sh not sourced; Homebrew or other install first on PATH | `source scripts/env.sh` in the same command |
| `config/toolchain.env changed since the last tools/bootstrap.sh` | Pins bumped (e.g. after a pull) | Re-run bootstrap |
| `sha256 mismatch for <url>` (bootstrap) | Upstream re-published the asset, download corrupted, or tampering | Bootstrap already deleted the bad download, so retry once. If it persists, run `uv run tools/resolve.py toolchain-pin` (same versions) on a machine with gpg and compare. Report the discrepancy; never hand-edit the hash |
| `TART Team ID 'X' != pinned 'Y'` / `Tart.app Team ID …` | Tart's signer changed | Stop. Confirm with upstream release notes before re-pinning |
| `tart.app is not notarized` | Wrong/partial download or unsigned build | As for sha mismatch |
| `tart.app signature invalid` / `URL not on the allow-list: …` | Corrupt or unexpected download; `toolchain.env` points somewhere new | Retry once; a URL change must come from `toolchain-pin` plus a reviewed diff, never a hand edit |
| preflight: `tart X != pinned Y` / `packer … != pinned …` / `could not verify Tart.app signature` | Toolchain out of date, or a non-repo binary first on PATH | Re-run `./tools/bootstrap.sh`; `source scripts/env.sh` |
| `plugin not registered at v…` | `packer plugins install` layout changed | Check `PACKER_PLUGIN_PATH=.toolchain/packer-plugins packer plugins installed` |

## Profiles and locks

| Message | Cause | Fix |
|---|---|---|
| `resolve.py list` shows `INVALID: …` | Profile schema violation (unknown key/option, wrong family, bad value, duplicate, missing variant) | Fix the profile as reported (`rhubarb-profiles` skill has the schema) |
| `locks/<profile>.lock.json missing; run resolve <profile> first` | New profile or lock not committed | Resolve on the right host, review, commit |
| `profiles/<id>.json changed since <lock> was written; re-resolve` | Profile edited after resolving (its hash is part of the image identity) | Re-resolve and review; don't revert the check |
| `unsupported lock schema; re-resolve` | Lock written by an older resolver | Re-resolve |

## Inputs

| Message | Cause | Fix |
|---|---|---|
| `IPSW hash disagreement: apple=… ipsw.me=…` | Mirror metadata stale or wrong | Apple's header is authoritative; wait and retry. Never drop the cross-check |
| `IPSW URL is not on updates.cdn-apple.com` | Discovery source returned a non-Apple URL | Treat as suspicious; don't allow-list it |
| `…: cached <file> differs from lock. If upstream moved (…), re-resolve.` | Chrome's unversioned URL now serves a newer build and the cache was cleared, or cache corruption | Restore `cache/artifacts/` from backup, or re-resolve (new lock → new VM name) and review the diff |
| `missing local file vendor/perimeter81/Perimeter81.pkg …` | Tenant installer not placed, or named differently | The user downloads it from the Harmony SASE portal (Devices → Downloads → Agents) and saves it as exactly `vendor/perimeter81/Perimeter81.pkg` |
| `…: not notarized` / `not signed with a Developer ID Installer cert` | Vendor shipped an unsigned build or a wrong file | Get a proper build from the vendor; don't relax the check |
| `…: Team ID X != pinned Y` | Vendor changed signing identity, or it's not the vendor's file | Verify out-of-band with the vendor before re-pinning in `config/packages/<id>.json` |
| HTTP 403 from api.github.com | Rate limit | `export GITHUB_TOKEN=…` |
| `macOS profiles must be resolved on macOS …` (resolve) / `signature verification needs macOS …` (verify) | A macOS profile's resolve/verify ran on Linux | Run it on the Mac. Linux profiles resolve anywhere with gpg |

## Stage 1

Stage 1 runs with a visible VNC window (not headless), so ask the user what screen it's stuck on.

- **Hangs or times out waiting for SSH**: the Setup Assistant `boot_command` fell out of sync.
  Usual causes are a slow host (a `<waitNNs>` too short) or Apple changing a screen in a new build.
  Compare against the latest upstream `vanilla-tahoe.pkr.hcl` in cirruslabs/macos-image-templates,
  adjust the affected step, and keep the credential typing via `${var.username}` / `${var.password}`.
  `PACKER_LOG=1` gives detail; the password is masked because the variable is `sensitive`.
- `The password must be 20-64 letters or digits…` means `PKR_VAR_password` wasn't set by build.sh.
  Run the build through `scripts/build.sh`, not `packer build` directly.
- `… exists but its password is not in the keychain` means the vanilla VM predates this keychain
  entry. Use `REBUILD_VANILLA=1`.
- Stage-1 assertions (`csrutil status | grep 'status: enabled\.'` etc.) failing means something
  changed the security posture. Investigate; don't remove the assertion.

### macOS 27 (provisioning API)
- `--provisioning-opts requires the host to be running macOS 27` / build.sh `needs a macOS 27 host`:
  the host is older, and there's no workaround. Use a `macos-26` profile or upgrade the host.
- SSH never comes up: the provisioning API didn't create the account. Check `tart run` output in
  the Packer log; confirm Tart ≥ 2.33 (`tart --version`).
- `bootstrap password still valid`: the rotation didn't take. Treat it as a failure; investigate
  `dscl . -passwd` output rather than skipping the check.
- Hang at the end of stage 1: the self-shutdown step failed. The plugin's own shutdown can't work
  after rotation, by design. Check that `/tmp/rhubarb-password` was uploaded.

## Stage 2

| Message | Cause | Fix |
|---|---|---|
| `hash mismatch inside guest` | Upload corrupted or stage dir stale | Re-run build (verify rebuilds the stage dir) |
| `perimeter81: installed app bundle unknown …` / `…: /Applications/… missing after install` | Vendor renamed the bundle, or `app` is wrong in the package config | `ls /Applications` in the `-unverified` image; set `"app"` in `config/packages/<id>.json` (or extend the Perimeter 81 pattern in `guest/macos/install.sh`) |
| `sshd_config has no Include for sshd_config.d` | Apple changed the default sshd_config | Code change needed; keep key-only enforcement |
| `sshd -T missing '<setting>'` | Drop-in not taking precedence, or the setting was renamed | Inspect `/etc/ssh/sshd_config.d/`; adjust the file ordering or name |
| `stealth mode is off` / `firewall is not enabled` | New macOS changed `socketfilterfw` output wording | Check the real output; update the grep to match the new wording exactly |
| `Screen Sharing not disabled` | `launchctl print-disabled` format changed | Same approach as above |
| `guest did not power off within 300s` | finalize.sh failed before its shutdown, or the shutdown hung | Read the Packer output above it |

## Smoke test

The smoke test runs on a throwaway clone (`<vm>-smoke-<pid>`), which is deleted afterwards.

| Message | Cause | Fix |
|---|---|---|
| `no IP within 300s` | Guest didn't boot, or slow DHCP | Retry; `tart run` the `-unverified` image manually. Linux smoke tests already use `--resolver arp` |
| `SSH port not reachable` | sshd not starting (e.g. host keys not regenerated), or firewall blocking it | Log in via GUI: `sudo launchctl print system/com.openssh.sshd`, `ls /etc/ssh/ssh_host_*` |
| `no ed25519 host key offered (host keys not regenerated?)` | macOS didn't recreate the host keys deleted at seal time | Code change: regenerate them at first boot (e.g. `ssh-keygen -A` via a launchd job). Never ship shared keys |
| `unexpected auth methods: …` | Server still offers password/keyboard-interactive | The drop-in isn't effective. See `sshd -T` above |
| `key login failed (key in agent? RHUBARB_SSH_FROM correct?)` | Key not in agent, or the host's vmnet address ≠ `from=` | `ssh-add -l`; on the host `ifconfig bridge100` shows the vmnet address. Set `RHUBARB_SSH_FROM` and rebuild |
| `in-guest posture checks failed` | macOS: passwordless sudo, auto-login, SIP/Gatekeeper/firewall/stealth off, missing `/Library/RhubarbTart` records. Linux: passwordless sudo, LightDM auto-login, firewall inactive (`nftables` on Kali, `firewall` on NixOS), empty `/etc/machine-id`, missing `/var/lib/rhubarbtart/installed.txt`, missing Rosetta binfmt | Run the matching heredoc lines from `scripts/smoke-test.sh` one by one over SSH |
| `SSH reachable but should be disabled` | `launchctl disable system/com.openssh.sshd` didn't persist | Code change in finalize.sh |
| `Screen Sharing (5900) reachable` | Screen Sharing still enabled | Same |

## Inspecting

A failed build leaves `rbt-<profile>-<sha>-unverified`. To look inside:

```sh
tart clone rbt-<profile>-<sha>-unverified debug-1   # keep the evidence untouched
tart run debug-1                                     # Linux+Rosetta: add --rosetta=rosetta
# password: Linux -> the final image name's entry (stored before the build)
security find-generic-password -s RhubarbTart -a rbt-<profile>-<sha> -w
# macOS -> the vanilla VM's entry
security find-generic-password -s RhubarbTart -a rbt-<base>-<build>-vanilla -w
```

Delete `debug-1` when done. Once the cause is fixed, rebuild; build.sh replaces the `-unverified` image.

## NixOS

| Symptom | Cause | Fix |
|---|---|---|
| Packer times out waiting for SSH | `boot_command` typed before the live console was ready | Raise the initial `<wait75s>` in `packer/linux/nixos.pkr.hcl` |
| `nixpkgs NAR hash … != locked …` | Staged tarball doesn't match the lock | Re-run build (verify restages). If it persists, re-resolve and compare. Never edit the hash |
| `nixos-install` evaluation error | Profile/config mismatch (option or package name changed in this nixpkgs) | Reproduce off-Mac with the Docker eval recipe in the `rhubarb-dev` skill |
| Download/signature errors during install | cache.nixos.org unreachable or a path not signed | Retry. Never set `require-sigs = false` or add substituters |
| Clone hangs at boot | Rosetta mount without the share | The mount is `nofail`; if it still hangs, start with `--rosetta=rosetta` and report it |
| `tart ip` finds nothing | DHCP client-id | networkd is set to `ClientIdentifier=mac`; the smoke test uses `--resolver arp`, and `ssh.sh`/`enroll.sh` fall back to it |

## Kali

| Symptom | Cause | Fix |
|---|---|---|
| `Kali needs RHUBARB_SSH_FROM to be the vmnet host IPv4` | Preseed server needs a concrete bind address | Set it to the vmnet host address (default `192.168.64.1`) |
| `[serve-preseed] … never came up` | vmnet bridge didn't appear, or wrong address | Check `ifconfig bridge100`; match `RHUBARB_SSH_FROM` |
| Installer stops at a question | Preseed didn't load (GRUB line mistyped) or a new question | Watch the VNC console. Fix the key in `kali/preseed.cfg.tmpl` without adding secrets |
| GRUB shows no prompt / boots the wrong entry | `c` pressed too early or late | Adjust `<wait10s>` in `packer/linux/kali.pkr.hcl` |
| Packer times out after 120 min | Slow mirror or a stuck install | Retry; check the console |
| `hash mismatch inside guest` | Stage/lock mismatch | Re-run build |
| apt install fails on a vendor `.deb` | Dependency not in the current Kali rolling | Report it; re-resolve (newer vendor build) or drop the package from the profile |
| `kali-grant-root … installed` / `NOPASSWD in sudoers` | A metapackage pulled passwordless root | Remove that metapackage from the profile's `kali_metapackages` |

## Enrollment

| Message | Cause | Fix |
|---|---|---|
| `unsupported key type …` (build) | RSA or other key in `RHUBARB_SSH_PUBKEYS` | Use ed25519/ecdsa (optionally a hardware `-sk` key) |
| `no keychain item RhubarbTart-enroll/…` | Secret not stored | Add it as shown in `scripts/enroll.sh` (the `-w` prompt keeps it out of argv) |
| `no keychain password for <name> (for a clone, pass --image …)` | Enrolling a clone: its password lives under the image name | `enroll.sh <clone> <service> --image rbt-<profile>-<sha>` |
| Tailscale on macOS doesn't connect | System extension not approved | The user approves it in the VM (System Settings > General > Login Items & Extensions) |
| WARP doesn't register | Service token lacks Service Auth enrollment rights, or wrong `--org` | Fix it in the Cloudflare dashboard; re-run enroll |

## rhubarb CLI

| Message | Cause | Fix |
|---|---|---|
| `no rhubarb clone named …` | Not created by `rhubarb new`, or its record was removed | `rhubarb list`; create clones with `rhubarb new`. It deliberately won't manage VMs it didn't create |
| `… is not built on this Mac (./scripts/build.sh P)` | The profile's current image (from its committed lock) doesn't exist | Build it; or `rhubarb new NAME --image` an existing verified image |
| `no keychain password for rbt-…` | Image built on another Mac, or its entry was deleted | Rebuild here |
| `… must be a regular file owned by you with mode 0600; refusing it` | Record permissions loosened, or a symlink | Check nobody else wrote it; `chmod 600` only if you're sure it's yours, otherwise delete the record and the VM (`tart delete`) |
| `… is not owned by you` / `must be a real directory` (state dir) | State dir is a symlink or owned by another user | Investigate before changing anything. Point `RHUBARB_STATE_DIR` at a directory you own |
| `name … does not match its file` / `unexpected keys` / `invalid …` | Corrupted or hand-edited record | Delete the record (`rm ~/Library/Application Support/RhubarbTart/clones/NAME.json`) and the VM; re-create |
| `SSH refused our key …; keeping the image's password` | Key not loaded in `ssh-agent`, or the image was built for other keys | `ssh-add`, then `rhubarb reset NAME --same-image` |
| `SSH not reachable after 2 boots …; keeping the image's password` | The clone's first boot was slow, or an image with no recorded SSH mode has sshd off | Raise `RHUBARB_SSH_WAIT` and `rhubarb reset NAME --same-image`; if the image has no SSH keys, rebuild with `RHUBARB_SSH_PUBKEYS` |
| `image … was built with SSH disabled (RHUBARB_SSH_PUBKEYS unset) …` | The image's provenance records no authorized keys, so sshd is off — rotation is refused up front (no boot) | Rebuild the image with `RHUBARB_SSH_PUBKEYS=<pubkey file>`; a retry can't help |
| `… ssh failed on the host side, not waiting for it: …` | A client-side ssh problem (bad `RHUBARB_SSH_IDENTITY`, a broken `-sk`/`SecurityKeyProvider`, or a host-key mismatch) — not the guest still booting | Fix the reported ssh issue; `rhubarb` surfaces it immediately instead of waiting out the full timeout |
| `password rotation failed: …` | Rotation script error in the guest (see the message) | The clone keeps its old password and the new keychain entry is removed. Retry with `reset NAME --same-image` |
| `a VM named … already exists` | Name collision with any Tart VM | Pick another name |
