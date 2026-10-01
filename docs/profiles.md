# Define your own guest

A guest is defined by a small JSON profile in `profiles/`: an OS base, a list of tools and a few
options. Validation is strict, so a typo can't silently build a different image.

```json
{
  "id": "kali-web",
  "description": "Kali for web-app testing",
  "base": "kali-rolling",
  "packages": ["chrome", "zap"],
  "username": "kaliweb",
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
<tr><td><code>juice-shop</code></td><td></td><td>✅ (lab target only)</td><td></td></tr>
</table>

`juice-shop` runs OWASP Juice Shop as a service on port 3000 with no outbound access. It belongs
in a target profile such as `juiceshop-target`, never on a machine you attack from.

| Key | Rule |
|---|---|
| `id` | Equals the filename; 2–41 characters: lowercase letters, digits and dashes, not starting with a dash |
| `base` | `macos-26`, `macos-27`, `nixos-26.05`, `kali-rolling` (files in `config/bases/`) |
| `packages` | Tools from the catalog above; each must support the base's OS |
| `username` | 3–16 characters, lowercase letters and digits, starting with a letter (default `admin`). Kali rejects names its installer reserves, including `admin`, so a Kali profile must set one |
| `vm` | `cpu` 2–64, `memory_gb` 4–256, `disk_gb` 40–2048 (defaults come from the base) |
| `options.desktop` | NixOS/Kali: `"none"` or `"xfce"` |
| `options.rosetta` | NixOS/Kali: run x86_64 binaries (`rhubarbtart run` then starts clones with `--rosetta=rosetta`) |
| `options.kali_metapackages` | Kali: a list of Kali packages, for example `kali-linux-default` or `kali-linux-headless` |

Unknown keys, options for the wrong OS, bad values and unsupported tools are all rejected. Check with
`uv run tools/resolve.py list`. Editing a profile changes its identity, so re-resolve its lock.
Adding a tool that isn't in the catalog needs a trustworthy source first; see
[Keeping inputs fresh](reference.md#keeping-inputs-fresh).

> [!NOTE]
> A tool with no public download (for example a licensed agent from a vendor's admin console) can
> still be added to a macOS guest as a **local** package (`resolver: "local"`, a `.pkg` or `.dmg`):
> its installer is placed under the git-ignored `vendor/` directory and pinned by hash on first
> resolve. See [`vendor/README.md`](../vendor/README.md). An image containing one is
> tenant-specific, so share it only through a private registry.

← back to the [README](../README.md)
