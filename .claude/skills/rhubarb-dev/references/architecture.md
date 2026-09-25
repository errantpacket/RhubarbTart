# RhubarbTart architecture

## Contents
- [Data flow](#data-flow)
- [Who owns what](#who-owns-what)
- [Credentials and access during a build](#credentials)
- [Ordering constraints in the seal step](#seal-ordering)
- [Naming and keychain](#naming)

## Data flow

```
config/toolchain.env ──bootstrap.sh──▶ .toolchain/{tart.app,bin/,packer-plugins/,INSTALLED}
                                         (sourced into PATH by scripts/env.sh)
config/sources.json ──resolve.py resolve──▶ sources.lock.json   (reviewed + committed)
                                   │
                    resolve.py verify: cache/ vs lock ──▶ cache/stage/
                                   │        {pkgs, dmgs, SHA256SUMS, packages.tsv, sources.lock.json}
build.sh ── stage 1: packer/01-vanilla.pkr.hcl  (IPSW → rbt-tahoe-<build>-vanilla)
         ── stage 2: packer/02-apps.pkr.hcl     (clone → install.sh → finalize.sh → poweroff)
                                               → rbt-tahoe-<build>-<inputs12>-unverified
         ── smoke-test.sh on a disposable clone → tart rename → rbt-tahoe-<build>-<inputs12>
         ── resolve.py provenance → out/<vm>.provenance.json
```

## Who owns what

| Concern | File | Notes |
|---|---|---|
| Toolchain install + verification | `tools/bootstrap.sh` | Strict KEY allow-list parser for `toolchain.env`; URL allow-list; codesign/notarization for Tart |
| Toolchain pin derivation | `resolve.py cmd_toolchain_pin` | Two-source agreement; HashiCorp GPG with pinned fingerprint `HASHICORP_FPR` |
| PATH / env isolation | `scripts/env.sh` | `.toolchain/bin` first, `PACKER_PLUGIN_PATH`, `CHECKPOINT_DISABLE`, `UV_PYTHON` |
| Toolchain assertion | `resolve.py cmd_preflight` | PATH resolution, versions, `INSTALLED` stamp vs pins, Tart Team ID |
| Discovery + lock | `resolve.py plan_*`, `cmd_resolve` | IPSW: ipsw.me discovery, Apple CDN header is the authority |
| Host verification + staging | `resolve.py cmd_verify`, `check_signature` | Re-hashes the cache every build; builds `packages.tsv` for the guest |
| Setup Assistant automation | `packer/01-vanilla.pkr.hcl` `boot_command` | Timing-sensitive; the only place the password is typed |
| Guest install + re-verify | `guest/install.sh` | Hash, notarization, Team ID before install; bundle re-verify after |
| Hardening + seal | `guest/finalize.sh` | Last step; asserts posture; powers off |
| External proof | `scripts/smoke-test.sh` | Throwaway clone; password refused, key works, posture, ports |
| Orchestration, secrets, naming | `scripts/build.sh` | Keychain, `PKR_VAR_*`, key validation, `-unverified` → rename |
| Regression guard | `tools/check.sh` | Lint + invariant greps; the list of `check` lines is the executable spec |

## Credentials

- Stage 1 types the generated password into Setup Assistant over VNC. The vanilla VM keeps
  password SSH; it is a local intermediate only.
- Stage 2 connects with that password. Root steps use
  `echo '<pw>' | sudo -S -p '' /usr/bin/env {{ .Vars }} {{ .Path }}` (the `as_root` local).
  `/usr/bin/env` avoids depending on sudoers' policy for inline `VAR=value`.
- The Packer plugin's own graceful shutdown also uses `sudo -S` over the existing connection,
  which is why nothing needs `NOPASSWD`.

## Seal ordering

`guest/finalize.sh` runs over Packer's open SSH session, so the order is load-bearing:

1. Security-update prefs, firewall + stealth, `launchctl disable` Screen Sharing. Services
   are *disabled* (effective next boot), not booted out, so the session isn't cut.
2. SSH: write `/etc/ssh/sshd_config.d/010-rhubarb.conf` (sorts before Apple's
   `100-macos.conf`; sshd takes the first value it reads), then `authorized_keys` with
   `from=`/no-forwarding options. Validate with `sshd -t` and `sshd -T`, which need host keys,
   so this happens before step 4. Without keys: `launchctl disable system/com.openssh.sshd`.
3. Assertions (SIP, Gatekeeper, firewall, stealth, no auto-login, no sudoers entries, Screen
   Sharing disabled), then residue removal (stage dir, caches, histories, sudo timestamps,
   Packer temp scripts, `log erase`).
4. Delete `/etc/ssh/ssh_host_*`. This comes last; the live session keeps its keys in memory.
5. Background `shutdown -h now` after 3 s; the provisioner has `expect_disconnect = true`.
   A `shell-local` step waits until `tart get` reports `Running: false`.

New connections after step 2 can't use a password, so nothing after finalize.sh may need a
fresh SSH login. That's why the guest powers itself off instead of relying on Packer.

## Naming

- `inputs_sha256` = sha256 over `macos <build> <sha>` + `<id> <sha> <team_id>` lines, sorted
  by id. No timestamps, so re-resolving to the same artifacts keeps the name.
- Keychain service `RhubarbTart`: account `<vanilla VM>` (written in stage 1) and account
  `<final VM>` (written after the rename). Same password: images inherit it from the vanilla VM.
- The smoke clone is `<candidate>-smoke-<pid>` and is always deleted by its EXIT trap.
