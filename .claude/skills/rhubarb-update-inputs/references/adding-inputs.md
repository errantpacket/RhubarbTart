# Adding guests, tools and OS bases

## Contents
- [The bar for any new input](#the-bar)
- [A new tool](#new-tool)
- [A new OS base or release](#new-base)

## The bar

New *profiles* made from the existing catalog are covered by the `rhubarb-profiles` skill.
This file is for new inputs, which must meet the same bar as the existing ones:

1. It is downloaded over HTTPS from the vendor, or placed by the user from the vendor's portal.
2. It has a hash anchor. Prefer a vendor-published one: a signed apt `InRelease`, a GPG-signed
   `SHA256SUMS`, a GitHub release digest, a vendor `.sha256`, or a signature chain like
   Tailscale's distsign. Otherwise it is trust-on-first-use, recorded as `tofu:` in
   `hash_sources` and called out in the README provenance table.
3. It carries a signature check where the platform has one. macOS: Developer ID + notarization +
   a pinned Team ID. Linux: apt/GPG with a pinned key *file* in `config/keys/` **and** a pinned
   fingerprint (`key_fpr`).

Anything that fetches at install time (`curl | sh`, Homebrew casks, web installers, a `.deb` that
adds its own apt source) breaks provenance, because what lands in the image isn't what was
verified. Prefer the vendor's offline `.pkg`, `.dmg` or `.deb`, and explain why a web installer
can't be used.

## New tool

### 1. `config/packages/<id>.json`

A `description` plus a `variants` map keyed by family (`macos`, `nixos`, `kali`). Include only
the families that have a trustworthy source. Existing resolvers:

| resolver | family | fields | anchor |
|---|---|---|---|
| `apt` | kali | `repo suite component package arch key key_fpr` (+ `kind: "deb"`) | signed InRelease → Packages → .deb |
| `distro` | kali | `package` | Kali archive (apt-verified); version recorded in image |
| `nix` | nixos | `attr` (package) or `module` (`services.<module>.enable`), optional `unfree` | pinned nixpkgs |
| `local` | macos | `path` under `vendor/`, `kind`, `team_id`, optional `app` | TOFU + signature |
| `chrome-mac`, `zap-mac`, `warp-mac`, `tailscale-mac` | macos | see existing files | vendor-specific |

For a signed apt repo, add its key to `config/keys/` (verify the fingerprint from a second source)
and reuse `apt`. macOS variants need `app` (the bundle name in `/Applications`) so the guest can
re-verify the installed bundle. The one exception is Perimeter 81, whose bundle name varies: it
has no `app`, and a pattern hardcoded in `guest/macos/install.sh` finds it.

### 2. A new resolver (only for a new download source)

Add a function in `tools/rhubarb/packages.py` and register it in `RESOLVERS`. It returns
`url, file, sha256` (None = TOFU), plus `version`, `size` and `hash_sources`. Follow `zap_mac`:
discover the version from vendor metadata, locate the exact asset, and raise `VerifyError`
instead of guessing. Any new crypto or parsing gets a case in `tools/test_rhubarb.py` that uses
reference-implementation values.

### 3. Guest side

- **macOS:** `guest/macos/install.sh` installs any `pkg`/`dmg` and re-verifies `/Applications/<app>`.
  Add a self-updater disable if the tool has one; see the WARP updater.
- **Kali:** `guest/kali/install.sh` installs `deb` and `distro` kinds generically. If the `.deb`
  adds an apt source, neutralize that the way Chrome's is.
- **NixOS:** usually nothing, since `nix/modules/packages.nix` maps `attr`/`module`. Check with
  the Docker eval recipe in `rhubarb-dev`.
- **Runtime identity:** if the tool keeps any (VPN or agent enrollment), wipe it in the family's
  seal step and add an `enroll.sh` case. Never bake it.

### 4. Docs and checks

Update the README Provenance table (and Profiles, if it's used). `./tools/check.sh` must pass.
Then resolve, review the diff (including the new signer) and build.

## New OS base or release

- **New macOS release, 26.x → 26.y:** that's just a refresh (see SKILL.md).
- **New macOS major:**
  1. Add `config/bases/macos-<major>.json`.
  2. Pick `setup: keystrokes | provisioning`. Provisioning needs a host of that major.
  3. Set `packer` to the stage-1 template; `build.sh` uses whatever the base declares.
     Provisioning bases also need `requires_host_major`, and can reuse `vanilla-27.pkr.hcl` (it's
     major-agnostic). A keystroke flow needs its own template, re-validated against upstream
     cirruslabs templates, that takes the same variables as `vanilla-26.pkr.hcl` (build.sh passes
     `ipsw_path`, `vm_name` and the `PKR_VAR_*` credentials).
  4. Re-check the output strings asserted in `guest/macos/finalize.sh` and `scripts/smoke-test.sh`.
- **New NixOS release:** add `config/bases/nixos-<ver>.json` with the new channel. Nothing else
  to bump: `system.stateVersion` follows the pinned nixpkgs release automatically, which is
  correct because every image is a fresh install. Evaluate the config against the new nixpkgs
  with the Docker recipe from `rhubarb-dev` first; options get renamed between releases.
- **New Linux family:** that's a project. It needs `FAMILIES` in `profiles.py` (and any
  family-specific `OPTIONS`), a `bases.py` planner, a Packer template, guest install and finalize
  scripts, a `build.sh` case, smoke-test checks, and README posture rows. Use `rhubarb-dev`.
