---
name: rhubarb-profiles
description: Design, create and customize RhubarbTart guest profiles, i.e. decide what goes into a security-research VM. Covers choosing the OS base (macOS 26, macOS 27, NixOS, Kali), picking tools from the package catalog and which OS each is available on (Chrome, OWASP ZAP, Cloudflare WARP, Tailscale, the Juice Shop lab target), VM size, username, desktop, Rosetta for x86 binaries, Kali metapackages, and validating the profile file. Use this whenever the user wants a new VM or guest for some kind of research or testing, asks what a guest can contain or which OS to pick, wants to change a profile's tools, options or sizes, or wants to copy or compare profiles. For refreshing versions or pins use rhubarb-update-inputs; for building and running use rhubarb-build.
---

# Designing RhubarbTart guest profiles

A profile (`profiles/NAME.json`) is the whole spec of a guest: one OS base, a list of tools, and
a few options. It's config only: no code changes, and every guest still gets the project's full
provenance, hardening and smoke-test pipeline. Your job is to turn "I need a VM for X" into a
valid profile, and be clear about the trade-offs of each choice.

## Choosing a base

| Base | Pick it when | Trade-offs |
|---|---|---|
| `macos-26` | Testing macOS clients/apps; any macOS 26+ host | Setup Assistant automated by keystrokes (timing-sensitive) |
| `macos-27` | Latest macOS; user has a **macOS 27 host** | Needs that host; cleanest setup (Apple's provisioning API) |
| `nixos-26.05` | Reproducibility matters most; declarative, auditable config | Whole OS pinned to one nixpkgs commit; smaller curated toolset than Kali |
| `kali-rolling` | Broad offensive-security toolset out of the box | Rolling: the ISO and vendor tools are pinned, but Kali-archive packages are "current at build time", with versions recorded in the image |

x86_64 guests aren't possible: Tart runs arm64 guests only on Apple silicon. For x86-only Linux
tools, set `"rosetta": true` on NixOS or Kali.

## Tool catalog

Check the live catalog with `ls config/packages/`; each file lists the OS families it supports.

| Tool | macOS | NixOS | Kali | Notes |
|---|---|---|---|---|
| `chrome` | ✓ | ✓ | ✓ | macOS pkg URL is unversioned, so its hash is trust-on-first-use |
| `zap` | ✓ | ✓ | ✓ | Kali: from the Kali archive (`zaproxy`) |
| `warp` | ✓ | ✓ | ✓ | Enrolled per clone with a service token (`rhubarb enroll NAME warp --org TEAM`) |
| `tailscale` | ✓ | ✓ | ✓ | macOS: one system-extension approval per clone |
| `juice-shop` | | ✓ | | Lab **target** (OWASP Juice Shop on port 3000), never for an attacker VM. Runs with no egress; reached from other clones only through an engagement's `links` |

A tool that isn't in the catalog needs a trustworthy source first (vendor-signed or
vendor-hashed). That's a `rhubarb-update-inputs` task (`references/adding-inputs.md`), not
something to improvise in a profile.

## Schema (enforced by `tools/rhubarb/profiles.py`)

```json
{
  "id": "kali-web",
  "description": "Kali for web-app testing",
  "base": "kali-rolling",
  "packages": ["chrome", "zap"],
  "username": "webtester",
  "vm": { "cpu": 6, "memory_gb": 12, "disk_gb": 100 },
  "options": { "desktop": "xfce", "rosetta": false, "kali_metapackages": ["kali-linux-headless"] }
}
```

| Key | Rule |
|---|---|
| `id` | Must equal the filename; `[a-z0-9][a-z0-9-]{1,40}` |
| `base` | A file in `config/bases/` |
| `packages` | Catalog ids, no duplicates, each with a variant for the base's family |
| `username` | 3–16 lowercase letters/digits, starting with a letter (default `admin`). Kali rejects names the Debian installer reserves, including `admin`, so Kali profiles must set one |
| `vm` | Only `cpu` (2–64), `memory_gb` (4–256), `disk_gb` (40–2048); omitted keys use the base's defaults |
| `options.desktop` | NixOS/Kali only: `"none"` or `"xfce"` |
| `options.rosetta` | NixOS/Kali only: `true`/`false`; clones must then run with `tart run --rosetta=rosetta` |
| `options.kali_metapackages` | Kali only: package names (e.g. `kali-linux-default`, `kali-linux-headless`) |

Anything else is rejected, including unknown keys, options for the wrong family, and bad values.
That's deliberate: a typo must never silently produce a different image. Metapackages that grant
passwordless root fail the build's seal checks. `kali-grant-root`, a hard dependency of the XFCE
desktop, is purged and pinned out automatically (#59).

## Workflow

1. Write or edit `profiles/NAME.json`. To start from a shipped profile, copy it and change `id`.
2. Validate with `uv run tools/resolve.py list`. Nothing may say `INVALID`; fix what it reports.
3. Resolve on the right host: macOS bases need the Mac; NixOS and Kali only need `gpg`:
   `uv run tools/resolve.py resolve NAME`. Any change to a profile changes its hash, so its lock
   must be re-resolved (build refuses a stale lock).
4. Show the user the new `locks/NAME.lock.json`, commit it with their go-ahead, then build (the
   `rhubarb-build` skill), and work in clones: `./rhubarb new web-1 --profile NAME`.
5. Add the profile to the README **Choose a guest** table.

## Confirm with the user

- **The research goal.** It decides the base: Kali's toolset versus NixOS's reproducibility, and
  macOS for testing Apple clients.
- **VPN/ZTNA needs.** Enrollment is always per clone and at runtime, never baked, so they'll
  need tokens or auth keys in their host keychain (the `rhubarb-build` skill covers
  `rhubarb enroll`).
- **Whether the image will be shared.** Tools installed from a vendor portal (licensed,
  tenant-specific installers) mean private registries only.
- **Sizing.** Kali with `kali-linux-default` plus a desktop wants 80 GB+ of disk and 8 GB+ of RAM.
