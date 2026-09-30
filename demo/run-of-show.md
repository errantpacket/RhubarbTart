# RhubarbTart capability demo — run of show

A ~7–9 minute recorded walkthrough of **RhubarbTart**: provenance-verified guest images, disposable
clones with per-clone identity, engagements as a bounded scope, host-enforced network isolation, a
control-plane over one audited core, tamper-evident evidence, and a signed portable vault — plus,
as one capability among them, handing a range to an autonomous agent on a leash.

**Layout.** Your **operator terminal** (the `rhubarb` CLI) is the star and stays on screen the
whole time. Open a **herdr** window too — you only switch to it for Scene 6 (the agent). herdr is
the agent runtime here, not the point; RhubarbTart is.

Run everything from the repo root. `./rhubarb …` loads the pinned toolchain itself; for the
`rbt-range` steps, export the two env vars shown in Scene 6.

---

## Before you record (off camera)

1. `./demo/preflight.sh` — provisions the lab from verified images, boots both clones, starts the
   control-plane service, and opens the network link. Wait for **`READY`**.
2. Open **herdr** in the window you'll record (only needed at Scene 6).
3. Have `demo/agent-task.md` open to paste from.
4. For a perfectly clean take: `./demo/teardown.sh && ./demo/preflight.sh --reset`, and clear any
   prior evidence: `rm -rf ~/Library/Application\ Support/RhubarbTart/evidence/juiceshop-lab`.

Note `jsl-target`'s IP for Scene 4: `tart ip jsl-target`.

---

## Scene 1 — Provenance you can trust (75s)

**Say:** "Every guest is built from pinned inputs, verified twice, hardened, and proven from the
outside before it's even named. The name *is* the hash of its inputs."

```sh
./rhubarb images                              # rbt-<profile>-<inputs-sha>, current per profile
./demo/show-provenance.sh jsl-target          # what it was built from, and how
```

**Point out:** the OS base and packages are pinned; the build toolchain (tart) is signed and
notarized; it records the exact git commit; SSH was sealed key-only from the host. Nothing floats.

## Scene 2 — Disposable clones, unique identity (45s)

**Say:** "You never run the image. You run throwaway clones — each with its own rotated password,
no shared identity."

```sh
./rhubarb list          # jsl-attacker + jsl-target, each PASSWORD=unique, tagged to the engagement
```

## Scene 3 — An engagement is a bounded, reviewable scope (45s)

**Say:** "A whole scope is one committed manifest — ranges to stand up, and the one network path
allowed. No manifest, no run. It provisions and tears down as a unit."

```sh
./rhubarb engagement list
cat engagements/juiceshop-lab.json
```

## Scene 4 — Host-enforced network isolation (75s)

**Say:** "Clones can't reach each other. The only path is the one link the manifest declares — and
it's enforced below the guest, so a compromised agent can't widen it."

```sh
TIP=$(tart ip jsl-target)
# direct attacker -> target: blocked
./rhubarb ssh jsl-attacker -- "curl -s -m5 -o /dev/null -w 'direct: %{http_code}\n' http://$TIP:3000/ || echo 'direct: unreachable'"
# over the declared link, the target is on the attacker's own loopback:
./rhubarb ssh jsl-attacker -- 'curl -s http://127.0.0.1:3000/ | grep -o "<title>[^<]*</title>"'
# and the target itself has no way out:
./rhubarb ssh jsl-target -- 'systemctl show -p IPAddressDeny --value juice-shop'
```

## Scene 5 — One audited core, many surfaces (45s)

**Say:** "The CLI, the dashboard, and the service all call one audited core — nothing else touches
`tart` or the keychain. Here's the control-plane service on a private socket."

```sh
./rhubarb serve            # already running from preflight — show it, or `rhubarb list` again
```

*(Optional visual: `./rhubarb-tui` for the image/clone/provenance dashboard, then back.)*

## Scene 6 — Hand a range to an agent, on a leash (90s) — herdr

**Say:** "RhubarbTart can also hand a range to an autonomous agent. herdr runs it; RhubarbTart
bounds it. The agent's *only* way to touch the box is a scoped client, and everything it runs is
recorded."

```sh
cat engagements/juiceshop-lab.herdr.json     # which agent, kind, clone; and the tiered rules
./rhubarb herdr arm juiceshop-lab            # launches the agent in herdr, pinned to jsl-attacker
```

**Switch to herdr**, paste the prompt from `demo/agent-task.md` into the `recon` pane, and let it
do its read-only recon (`rbt-range -- id`, read the challenge API). Narrate: every call goes
through the control plane and is journaled on the host.

> The agent's own Claude Code classifier gates in-guest writes, so you drive the write + the gated
> action yourself in Scene 7 (same tool, deterministic). To let the agent do it instead, allow
> `./rbt-range` in its Claude Code permissions first.

## Scene 7 — The scoped client + a gated sensitive action (90s)

Back in the **operator terminal**:

```sh
export RBT_SERVICE_SOCKET="$HOME/Library/Application Support/RhubarbTart/service.sock"
export RBT_RANGE_CLONE=jsl-attacker

# a normal action runs and is recorded:
./rbt-range -- "mkdir -p ~/evidence && printf '# Note\nJuice Shop reachable on the lab link.\n' > ~/evidence/note.md && echo saved"

# a destructive action is TIERED — it's held for approval:
./rbt-range -- "mkdir -p ~/scratch && date > ~/scratch/tmp && rm -rf ~/scratch && echo cleaned" &
./rhubarb herdr pending juiceshop-lab                 # the held command + its request id
./rhubarb herdr approve juiceshop-lab <REQUEST_ID>    # single-use grant
```

**Say:** "Sensitive actions pause for a human. The grant is single-use, and the whole
request → grant → run exchange is on the record." The backgrounded command prints `cleaned`.

## Scene 8 — Tamper-evident evidence (60s)

**Say:** "Every command, output, artifact, approval, and the app's own ground truth — one
hash-chained journal on the host, where the guest under test can't touch it."

```sh
./rhubarb evidence collect juiceshop-lab     # pull artifacts + Juice Shop's challenge ground truth
./rhubarb evidence list juiceshop-lab        # exec / artifact / ground_truth / approval / lifecycle
./rhubarb evidence verify juiceshop-lab      # recompute the chain and every item
```

## Scene 9 — A signed, portable vault (45s)

**Say:** "Finally, seal it: a signed, read-only bundle that verifies anywhere with nothing but the
bundle — the same offline key that signs our images."

```sh
./rhubarb vault seal juiceshop-lab --out /private/tmp/rbt-demo/vaults
./rhubarb vault verify /private/tmp/rbt-demo/vaults/juiceshop-lab-*.vault
```

**Close:** "Verified, hardened images. Disposable clones. A bounded engagement. Isolation enforced
below the guest. A recorded, gated path for anything that runs — human or agent. And signed
evidence at the end. That's RhubarbTart."

---

## After you record

```sh
./demo/teardown.sh
# optional: rm -rf ~/Library/Application\ Support/RhubarbTart/evidence/juiceshop-lab
```

## If something stalls on camera

- **Link check fails:** `./rhubarb ssh jsl-attacker -- 'curl -sf http://127.0.0.1:3000/ >/dev/null && echo ok'`;
  if not ok, re-run `./demo/preflight.sh` to reopen the link.
- **`herdr arm` says the service isn't running:** `./demo/preflight.sh` restarts it.
- **Agent won't do the write steps:** expected — drive Scene 7 yourself; the tooling is identical.
- **Clean slate:** `./demo/teardown.sh && ./demo/preflight.sh --reset`.
