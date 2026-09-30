# RhubarbTart capability demo — run of show

A recorded walkthrough of **RhubarbTart** told as one connected story: a capable agent
(**Claude Code on Opus 4.8**, in herdr) **customizes RhubarbTart and builds a new guest image**,
the platform **verifies and hardens** it from pinned inputs, and then a clone of that very image is
run under an **engagement** with every action captured as **tamper-evident evidence** and sealed
into a **signed vault**. The agent builds; the platform keeps it honest; the record outlives the box.

**Layout.** Your **operator terminal** (the `rhubarb` CLI) is the star and stays on screen. Open a
**herdr** window for the one agent scene. Speed up the build/boot waits in post.

Run from the repo root. `./rhubarb …` loads the pinned toolchain itself.

---

## Before you record (off camera)

1. `./demo/preflight.sh` — starts the control-plane service and makes sure the slate is clean
   (removes any leftover `profiles/nixos-demo.json` and `locks/nixos-demo.lock.json` so the agent
   creates them live; confirms the NixOS inputs are cached so the build is fast). Wait for `READY`.
2. Open **herdr** in the window you'll record.
3. Have `demo/agent-task.md` open to paste from.

---

## Scene 1 — Provenance you can trust (60s)

**Say:** "Every guest is built from pinned inputs, verified twice, hardened, and proven from the
outside before it's named. The name *is* the hash of its inputs."

```sh
./rhubarb images                              # rbt-<profile>-<inputs-sha>, current per profile
./demo/show-provenance.sh nixos-research      # what an image was built from, and how
```

## Scene 2 — An agent customizes RhubarbTart (2 min) — herdr, Opus 4.8

**Say:** "Let's have an agent extend RhubarbTart. It's Claude Code on Opus 4.8, in herdr, working
in the repo."

Launch the agent in a herdr pane at the repo:

```sh
herdr workspace create --label rbt-build-demo --cwd "$PWD" --no-focus     # note the returned pane id
herdr agent start builder --kind claude --pane <PANE_ID> -- --model claude-opus-4-8
```

**Switch to herdr**, paste the task from `demo/agent-task.md` into the `builder` pane, and narrate
as Opus 4.8:

1. writes a new `profiles/nixos-demo.json`,
2. runs `uv run tools/resolve.py plan nixos-demo` — **previews the pinned inputs** (base ISO
   SHA-256, packages inheriting the nixpkgs revision, nothing floating),
3. runs `./tools/check.sh` — the repo's security invariants still pass.

**Say:** "The agent proposes the change; the platform's pins and invariants decide whether it's
trustworthy — the reviewer sees exactly what would go in."

## Scene 3 — Build the agent's image (2–5 min, sped up)

Back in the **operator terminal**, turn the agent's profile into a real, verified guest:

```sh
uv run tools/resolve.py resolve nixos-demo                       # write the lock (pins every input)
RHUBARB_SSH_PUBKEYS=~/.ssh/id_ed25519.pub ./scripts/build.sh nixos-demo
```

**Say:** "It installs from the vendor image only, verifies every input against the lock, hardens
the guest, then proves the hardening from *outside* with a smoke test — and only a guest that
passes gets its final name."

## Scene 4 — Provenance of what the agent just built (45s)

```sh
./rhubarb images | grep nixos-demo            # the new rbt-nixos-demo-<sha>, current
./demo/show-provenance.sh nixos-demo          # pinned inputs, signed toolchain, sealed posture
```

**Say:** "Same guarantee as every other image — the agent's work is pinned and provable, not
trusted on faith."

## Scene 5 — Run a clone under an engagement; capture evidence (90s)

**Say:** "Now put the agent-built image to work in a bounded engagement, and record everything."

```sh
cat engagements/nixos-demo-build.json                 # a committed scope: one range, the new profile
./rhubarb engagement provision nixos-demo-build       # clone demo-box from the agent-built image
./rhubarb exec demo-box -- uname -a                   # a recorded command in the clone
./rhubarb exec demo-box -- tailscale version          # the tool the agent added, present
./rhubarb evidence collect nixos-demo-build
./rhubarb evidence list nixos-demo-build              # exec / artifact / lifecycle, hash-chained
./rhubarb evidence verify nixos-demo-build            # recompute the chain and every item
```

## Scene 6 — A signed, portable vault (45s)

**Say:** "Seal it: a signed, read-only bundle — including which verified image produced this
evidence — that verifies anywhere with nothing but the bundle."

```sh
./rhubarb vault seal nixos-demo-build --out /private/tmp/rbt-demo/vaults
./rhubarb vault verify /private/tmp/rbt-demo/vaults/nixos-demo-build-*.vault
```

## Scene 7 — Disposable: reset to pristine, evidence survives (45s)

**Say:** "The clone is throwaway. Reset gives a clean copy from the verified image; the signed
evidence on the host is untouched."

```sh
./rhubarb reset demo-box                              # destroy + re-clone from the verified image
./rhubarb list                                        # demo-box: fresh, new password
./rhubarb evidence verify nixos-demo-build            # the record is intact
./rhubarb vault verify /private/tmp/rbt-demo/vaults/nixos-demo-build-*.vault
```

**Close:** "An agent extended the platform; the platform pinned, verified, and hardened the result;
a clone of that image ran under a bounded scope with every action recorded; and the signed evidence
outlives the box. That's RhubarbTart."

---

## After you record

```sh
./demo/teardown.sh
# optional: remove the demo profile/lock and evidence for a fresh take:
#   git checkout -- . ; rm -f profiles/nixos-demo.json locks/nixos-demo.lock.json
#   rm -rf ~/Library/Application\ Support/RhubarbTart/evidence/nixos-demo-build
```

## If something stalls on camera

- **Agent pauses on a permission prompt:** approve it in the pane; Opus 4.8 otherwise completes the
  task autonomously.
- **`build.sh` fails:** re-run it; the inputs are cached, so it resumes quickly. `./tools/check.sh`
  must be green first.
- **`provision` says the image isn't built:** the build didn't finish or wasn't named — check
  `./rhubarb images` for `rbt-nixos-demo-<sha>` (final, not `-unverified`).
- **Clean slate:** `./demo/teardown.sh`, then remove the demo profile/lock as above.
