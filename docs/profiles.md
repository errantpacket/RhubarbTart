# Define your own guest

A guest is a small JSON profile — an OS base plus tools and options — validated strictly so a typo can never silently build a different image.

A profile is a small JSON file in `profiles/`:

```json
{
  "id": "kali-web",
  "description": "Kali for web-app testing",
  "base": "kali-rolling",
  "packages": ["chrome", "zap"],
  "username": "admin",
  "vm": { "cpu": 6, "memory_gb": 12, "disk_gb": 100 },
  "options": { "desktop": "xfce", "rosetta": false, "kali_metapackages": ["kali-linux-headless"] }
}
```

<table>
<tr><th>Tool</th><th>macOS</th><th>NixOS</th><th>Kali</th></tr>
<tr><td><code>chrome</code></td><td>✅</td><td>✅</td><td>✅</td></tr>
<tr><td><code>zap</code></td><td>✅</td><td>✅</td><td>✅ (Kali archive)</td></tr>
<tr><td><code>warp</code></td><td>✅</td><td>✅</td><td>✅</td></tr>
<tr><td><code>tailscale</code></td><td>✅</td><td>✅</td><td>✅</td></tr>
</table>

| Key | Rule |
|---|---|
| `id` | Equals the filename; lowercase letters, digits, dashes |
| `base` | `macos-26`, `macos-27`, `nixos-26.05`, `kali-rolling` (files in `config/bases/`) |
| `packages` | Tools from the catalog above; each must support the base's OS |
| `username` | 3–16 chars, lowercase letters/digits, **must start with a letter** (default `admin`) |
| `vm` | `cpu` 2–64, `memory_gb` 4–256, `disk_gb` 40–2048 (defaults come from the base) |
| `options.desktop` | NixOS/Kali: `"none"` or `"xfce"` |
| `options.rosetta` | NixOS/Kali: run x86_64 binaries (start clones with `--rosetta=rosetta`) |
| `options.kali_metapackages` | Kali: e.g. `kali-linux-default`, `kali-linux-headless` |

Validation is strict. Unknown keys, options for the wrong OS, bad values or unsupported tools are
rejected, so a typo can never silently build a different image. Check with
`uv run tools/resolve.py list`. Editing a profile changes its identity, so re-resolve its lock.
Adding a tool that *isn't* in the catalog requires a trustworthy source first; see
[Keeping inputs fresh](reference.md#keeping-inputs-fresh).

> [!NOTE]
> A tool with no public download (for example a licensed agent from a vendor's admin console) can
> still be added as a **local** package (`resolver: "local"`): its installer is placed under the
> git-ignored `vendor/` directory and pinned by hash on first resolve. See
> [`vendor/README.md`](../vendor/README.md). An image containing one is tenant-specific, so share it
> only through a private registry.

← back to the [README](../README.md)
