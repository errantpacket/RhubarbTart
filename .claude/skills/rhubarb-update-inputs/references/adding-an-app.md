# Adding an app to the image

The bar for any new input is the same as for the existing ones:

1. Downloaded over HTTPS from the vendor (or placed by the user from the vendor's portal).
2. A hash anchor: preferably one the vendor publishes (release digest, checksums file, signed
   manifest); otherwise trust-on-first-use recorded in the lock, and said so in the README.
3. A Developer ID signature, notarized, with a Team ID pinned after out-of-band confirmation.

Installers that fetch at install time (`curl | sh`, Homebrew casks, "web installers") break
provenance, because what lands in the image isn't what was verified. Prefer the vendor's
offline `.pkg`/`.dmg`, and explain to the user why a web installer can't be used.

## 1. Config entry: `config/sources.json`

The comments below are explanation only. `sources.json` is strict JSON, so leave them out.

```jsonc
{
  "id": "example",
  "kind": "dmg",                      // "pkg" (installer) or "dmg" (drag-install app)
  "resolver": "example",              // key in RESOLVERS, or "local" for vendor-portal files
  "app": "Example.app",               // dmg only: bundle name inside the image
  "team_id": null                     // pin after the first resolve + out-of-band check
}
```

For a vendor-portal file, use `"resolver": "local", "path": "vendor/example/Example.pkg"` and
make sure `.gitignore` excludes the binary. `vendor/**/*.pkg` covers pkgs; add a pattern for other types.

## 2. Resolver: `tools/resolve.py`

Add `plan_<id>(pkg) -> dict` next to `plan_zap` and register it in `RESOLVERS`. Return:

```python
{"version": "...", "url": "https://...", "file": "Example_1.2.3.dmg",
 "sha256": "<vendor-published>",   # omit only for TOFU; resolve then records the downloaded hash
 "size": 123, "hash_sources": ["<where the hash came from>"]}
```

Follow `plan_zap`: discover the version from vendor metadata, locate the exact asset, and raise
`VerifyError` when an expected field is missing rather than guessing. For TOFU sources, set
`hash_sources` to `["tofu:<reason>"]` so reviewers can see it.

## 3. Guest post-install check: `guest/install.sh`

The first loop installs everything in `packages.tsv` generically. The second loop re-verifies
the *installed* bundle (codesign, Gatekeeper, Team ID). Add a case for the new id with the
bundle's installed path. For pkgs this is the only check on what the installer put on disk,
so don't skip it.

## 4. Docs and checks

- README: add a row to the **Provenance model** table and mention the app in the intro.
- `./tools/check.sh` must pass.
- On the Mac: `resolve` (review the diff, including the new entry's signer), pin the Team ID,
  build, and confirm the app appears in `/Library/RhubarbTart/installed.txt` in the image.
