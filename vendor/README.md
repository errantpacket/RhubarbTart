# vendor/: locally supplied installers

Some tools aren't on a public URL. They're downloaded from a vendor/tenant portal behind a
login, such as a licensed agent from a vendor's admin console. Their installers go here,
one subdirectory per tool, at the exact `path` named in `config/packages/<id>.json`:

```
vendor/<id>/<file>          e.g. vendor/acme-agent/AcmeAgent.pkg
```

**These files are never committed.** `.gitignore` excludes `vendor/**/*.pkg`, `vendor/**/*.dmg` and `vendor/**/*.cer`
because tenant installers are often licensed and always host-specific. Only this README is tracked.

## Adding one

1. `config/packages/<id>.json` with `"resolver": "local"`, `"kind": "pkg"` or `"dmg"`,
   `"path": "vendor/<id>/<file>"`, `"team_id": null` (pin after first resolve), and `"app"` for a
   dmg. Add `"signed": false` only if the installer carries no Apple signature.
2. Drop the downloaded installer at that `path` on the build host.
3. `uv run tools/resolve.py resolve <profile>` pins its sha256 (trust-on-first-use), verifies
   the Developer ID signature if signed, and prints the observed Team ID. Pin that `team_id` in
   the package file and re-resolve so it's enforced. A missing file fails resolve with the path.
4. Add `<id>` to the profile's `packages` and build.

Full procedure: the `rhubarb-update-inputs` skill → `references/adding-inputs.md`
("Local / tenant installer").
