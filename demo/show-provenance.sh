#!/bin/bash
# Print the highlights of an image's provenance record for the demo (#112): what it was built
# from, how it was verified, and the posture it was sealed with.
#
#   ./demo/show-provenance.sh rbt-juiceshop-target-86656f1233f9
#   ./demo/show-provenance.sh jsl-target      # a clone name resolves to its image
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"
# shellcheck source=scripts/env.sh
source "$ROOT/scripts/env.sh"

NAME="${1:?usage: show-provenance.sh <image-or-clone-name>}"
uv run --quiet "$ROOT/tools/rhubarb_cli.py" list >/dev/null 2>&1 || true

PYTHONPATH="$ROOT/tools" uv run --quiet --no-project python - "$NAME" <<'PY'
import json, sys
from pathlib import Path
from rhubarb import api

name = sys.argv[1]
# Accept an image name, a clone name (-> its image), or a profile name (-> its current image).
try:
    for c in api.clones().clones:
        if c.name == name:
            name = c.image
            break
    else:
        for img in api.images():
            if img.profile == name and img.status == "current":
                name = img.name
                break
except Exception:
    pass

rec = Path("out") / f"{name}.provenance.json"
if not rec.is_file():
    sys.exit(f"no provenance record for {name} (out/{name}.provenance.json)")
d = json.loads(rec.read_text())
tc = d.get("toolchain", {})
lk = d.get("lock", {})
base = lk.get("base", {})
ssh = d.get("ssh", {})

def line(k, v): print(f"  {k:16} {v}")

print(f"\nProvenance — {d['vm']}")
print("  (the VM name is the sha256 of its inputs: same inputs, same name)")
line("profile", d["profile"])
line("built at", d["built_at"])
line("from commit", f"{(d.get('git_commit') or '?')[:12]}" + ("  (working tree dirty)" if d.get("git_dirty") else ""))
line("inputs sha256", d["inputs_sha256"])
print("  built with a pinned, signed toolchain:")
line("  tart", f"{tc.get('tart')}  signed by {tc.get('tart_signature',{}).get('signer','?')}"
      f"  notarized={tc.get('tart_signature',{}).get('notarized')}")
line("  packer", tc.get("packer"))
line("  host", tc.get("host_os"))
print("  from pinned, re-verified inputs:")
line("  OS base", base.get("release") or base.get("build") or base.get("version") or "?")
if base.get("nixpkgs"):
    line("  nixpkgs", f"rev {base['nixpkgs'].get('rev','?')[:16]}")
line("  packages", ", ".join(lk.get("packages", {}).keys()) or "(none)")
print("  sealed posture:")
if ssh.get("enabled"):
    line("  ssh", f"key-only from {ssh.get('from')}  key {(ssh.get('key_fingerprints') or ['?'])[0]}")
else:
    line("  ssh", "disabled")
print()
PY
