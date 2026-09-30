# Agent task (paste into the `recon` agent pane)

The demo agent is **Claude Code on Opus 4.8** (pinned in `engagements/juiceshop-lab.herdr.json`),
running in a herdr pane at the RhubarbTart repo. Its job is to **customize RhubarbTart** — real,
verifiable engineering work — not to attack anything. It shows the pinned-inputs + verification
model with a capable agent driving it.

Paste this into the `recon` pane after `arm`, then narrate while it works (~1 min):

---

You are working in the RhubarbTart repo (your cwd). RhubarbTart builds hardened Tart VM images from
pinned, verified inputs. Make a small customization to demonstrate the workflow:

1. Create `profiles/nixos-demo.json` for a NixOS research box on the `nixos-26.05` base, including
   the packages `chrome` and `tailscale`, with options `desktop: xfce` and `rosetta: true`. Model
   it on `profiles/nixos-research.json`.
2. Run `uv run tools/resolve.py plan nixos-demo` — this previews the pinned inputs without
   downloading. Point out that the base ISO is SHA-256 pinned and the packages inherit the base's
   nixpkgs revision.
3. Run `./tools/check.sh` to confirm the repo's security invariants still pass with the new profile.

Narrate each step briefly. Do not commit, push, or run a full build.

---

> Opus 4.8 completes this autonomously in the dry run (creates the profile, previews the pins, and
> check.sh passes). If it pauses on a permission prompt, approve it in the pane.
