# Troubleshooting RhubarbTart builds

Find the failing message below (they're quoted as the scripts print them). Each entry gives the
likely cause, how to confirm it, and the fix. Fixes never loosen a check. If a check itself
is wrong for a new macOS build, fix it with the same strictness (see the `rhubarb-dev` skill).

## Contents
- [Toolchain: bootstrap / env.sh / preflight](#toolchain)
- [Inputs: resolve / verify](#inputs)
- [Stage 1: vanilla install](#stage-1)
- [Stage 2: install.sh / finalize.sh](#stage-2)
- [Smoke test](#smoke-test)
- [Inspecting a failed image](#inspecting)

## Toolchain

| Message | Cause | Fix |
|---|---|---|
| `toolchain missing; run ./tools/bootstrap.sh` | `.toolchain/` absent (fresh clone) | Run bootstrap |
| `tart resolves to …, not …/.toolchain/bin/tart` | env.sh not sourced; Homebrew or other install first on PATH | `source scripts/env.sh` in the same command |
| `config/toolchain.env changed since the last tools/bootstrap.sh` | Pins bumped (e.g. after a pull) | Re-run bootstrap |
| `sha256 mismatch for <url>` (bootstrap) | Upstream re-published the asset, download corrupted, or tampering | Delete `.toolchain/downloads/<sha>-*` and retry once. If it persists, run `uv run tools/resolve.py toolchain-pin` (same versions) on a machine with gpg and compare. Report the discrepancy; don't hand-edit the hash |
| `TART Team ID 'X' != pinned 'Y'` / `Tart.app Team ID …` | Tart's signer changed | Stop. Confirm with upstream release notes before re-pinning |
| `tart.app is not notarized` | Wrong/partial download or unsigned build | As for sha mismatch |
| `plugin not registered at v…` | `packer plugins install` layout changed | Check `PACKER_PLUGIN_PATH=.toolchain/packer-plugins packer plugins installed` |

## Inputs

| Message | Cause | Fix |
|---|---|---|
| `IPSW hash disagreement: apple=… ipsw.me=…` | Mirror metadata stale or wrong | Apple's header is authoritative; wait and retry. Never drop the cross-check |
| `IPSW URL is not on updates.cdn-apple.com` | Discovery source returned a non-Apple URL | Treat as suspicious; don't allow-list it |
| `cached file differs from lock … run resolve again` | Chrome's unversioned URL now serves a newer build and the cache was cleared, or cache corruption | Restore `cache/` from backup, or re-resolve (new lock → new VM name) and review the diff |
| `missing local file vendor/perimeter81/Perimeter81.pkg` | Tenant installer not placed | User downloads from the Harmony SASE portal (Devices → Downloads → Agents) |
| `…: not notarized` / `not signed with a Developer ID Installer cert` | Vendor shipped an unsigned build or a wrong file | Get a proper build from the vendor; don't relax the check |
| `…: Team ID X != pinned Y` | Vendor changed signing identity, or it's not the vendor's file | Verify out-of-band with the vendor before re-pinning in `config/sources.json` |
| HTTP 403 from api.github.com | Rate limit | `export GITHUB_TOKEN=…` |
| `signature verification needs macOS` | Ran resolve/verify on Linux | Run on the Mac (only `plan` is portable) |

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

## Stage 2

| Message | Cause | Fix |
|---|---|---|
| `hash mismatch inside guest` | Upload corrupted or stage dir stale | Re-run build (verify rebuilds the stage dir) |
| `perimeter81: no app bundle found in /Applications` | Vendor renamed the bundle | Check `ls /Applications` in the image; extend the `find` pattern in `guest/install.sh` |
| `sshd_config has no Include for sshd_config.d` | Apple changed the default sshd_config | Code change needed; keep key-only enforcement |
| `sshd -T missing '<setting>'` | Drop-in not taking precedence, or the setting was renamed | Inspect `/etc/ssh/sshd_config.d/`; adjust the file ordering or name |
| `stealth mode is off` / `firewall is not enabled` | New macOS changed `socketfilterfw` output wording | Check the real output; update the grep to match the new wording exactly |
| `Screen Sharing not disabled` | `launchctl print-disabled` format changed | Same approach as above |
| `guest did not power off within 300s` | finalize.sh failed before its shutdown, or the shutdown hung | Read the Packer output above it |

## Smoke test

The smoke test runs on a throwaway clone (`<vm>-smoke-<pid>`), which is deleted afterwards.

| Message | Cause | Fix |
|---|---|---|
| `no IP within 180s` | Guest didn't boot or DHCP is slow | Retry; check `tart run` of the `-unverified` image manually |
| `SSH port not reachable` | sshd not starting (e.g. host keys not regenerated), or firewall blocking it | Log in via GUI: `sudo launchctl print system/com.openssh.sshd`, `ls /etc/ssh/ssh_host_*` |
| `no ed25519 host key offered (host keys not regenerated?)` | macOS didn't recreate the host keys deleted at seal time | Code change: regenerate them at first boot (e.g. `ssh-keygen -A` via a launchd job). Never ship shared keys |
| `unexpected auth methods: …` | Server still offers password/keyboard-interactive | The drop-in isn't effective. See `sshd -T` above |
| `key login failed (key in agent? RHUBARB_SSH_FROM correct?)` | Key not in agent, or the host's vmnet address ≠ `from=` | `ssh-add -l`; on the host `ifconfig bridge100` shows the vmnet address. Set `RHUBARB_SSH_FROM` and rebuild |
| `in-guest posture checks failed` | One of: passwordless sudo, auto-login, SIP/Gatekeeper/firewall/stealth off, missing records | Run the heredoc lines from `scripts/smoke-test.sh` one by one over SSH |
| `SSH reachable but should be disabled` | `launchctl disable system/com.openssh.sshd` didn't persist | Code change in finalize.sh |
| `Screen Sharing (5900) reachable` | Screen Sharing still enabled | Same |

## Inspecting

A failed build leaves `rbt-tahoe-<build>-<sha>-unverified`. To look inside:

```sh
tart clone rbt-tahoe-<build>-<sha>-unverified debug-1   # keep the evidence untouched
tart run debug-1                                         # GUI; password = vanilla VM's entry:
security find-generic-password -s RhubarbTart -a rbt-tahoe-<build>-vanilla -w
```

Delete `debug-1` when done. Once the cause is fixed, rebuild; build.sh replaces the `-unverified` image.
