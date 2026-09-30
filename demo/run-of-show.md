# RhubarbTart capability demo — run of show

A ~6–8 minute recorded walkthrough of the full agent-driven-engagement chain: **verified images →
engagement → network isolation → a real Claude agent under herdr → the scoped range client →
tiered-action approval → evidence → a signed, sealed vault.**

The centerpiece is the **herdr TUI**. Keep a second terminal ("operator terminal") beside it for
the `rhubarb` commands.

---

## Before you record (off camera)

1. From the repo root: `./demo/preflight.sh`
   It provisions the lab from verified images, boots both clones, starts the control-plane
   service, and opens the network link. Wait for **`READY`**.
2. Open **herdr** in the window you'll record. Have the operator terminal ready beside it.
3. Have `demo/agent-task.md` open to paste from.
4. Optional: `./demo/teardown.sh && ./demo/preflight.sh --reset` for a perfectly clean take.

Everything below runs from the repo root with `source scripts/env.sh` already done in the operator
terminal (or just use `./rhubarb …`, which loads the toolchain itself).

---

## Scene 1 — What we're starting from (30s)

**Say:** "Every guest here is built from pinned, verified inputs and proven hardened before it's
named. Work happens only in disposable clones."

```sh
./rhubarb images          # verified rbt-* images, current per profile
./rhubarb list            # the engagement's clones, cloned from those images
```

**Point out:** the clones are `jsl-attacker` (Kali) and `jsl-target` (Juice Shop), each with its
own rotated password, tagged to the `juiceshop-lab` engagement.

## Scene 2 — The engagement is a bounded scope (30s)

**Say:** "An engagement is a committed, reviewable scope manifest — no manifest, no run."

```sh
./rhubarb engagement list
cat engagements/juiceshop-lab.json        # ranges + the single sanctioned network link
```

## Scene 3 — Network isolation is real (60s)

**Say:** "The clones can't reach each other. The only path is the one link the manifest declares."

```sh
# Direct attacker -> target is blocked by vmnet isolation:
./rhubarb ssh jsl-attacker -- 'curl -s -m 5 -o /dev/null -w "direct target: %{http_code}\n" http://TARGET_IP:3000/ || echo "direct target: unreachable"'

# Over the declared link, the target shows up on the attacker's own loopback:
./rhubarb ssh jsl-attacker -- 'curl -s http://127.0.0.1:3000/ | grep -o "<title>[^<]*</title>"'
```

**Say:** "And the target has no way out — an exploited Juice Shop can't reach your LAN, the
internet, or your Mac." (Optional, quick:)

```sh
./rhubarb ssh jsl-target -- 'systemctl show -p IPAddressDeny --value juice-shop'
```

> Replace `TARGET_IP` with the address `tart ip jsl-target` prints (preflight showed it). Or skip
> the direct-check line and just show the link working — the isolation point still lands.

## Scene 4 — Arm a real agent under herdr (60s)

**Say:** "Now we hand this to an autonomous agent — but on a leash. `arm` launches it in herdr,
pinned to one clone, whose only tool to touch the range is the scoped `rbt-range` client."

```sh
cat engagements/juiceshop-lab.herdr.json   # which agent, what kind, which clone; and tiered rules
./rhubarb herdr arm juiceshop-lab
```

**Switch to herdr.** A workspace `rbt-juiceshop-lab` opened with a `recon` agent pane. Show the
sidebar/agent state.

## Scene 5 — The agent works, on the sanctioned path (90s)

Paste the prompt from **`demo/agent-task.md`** into the `recon` pane. The agent does its read-only
recon live:

1. confirms access (`rbt-range -- id`),
2. reads the local Juice Shop challenge API.

**Say:** "Everything it does goes through the control plane and is recorded as evidence on the
host — where the guest under test can't tamper with it. It has no other way to touch the box, and
no way off it."

> **Who runs steps 3–4.** Claude Code's own auto-mode classifier gates in-guest filesystem writes,
> so a fully-autonomous agent may stop before the write/cleanup steps. Two reliable options:
> - **Operator-driven (recommended for a recording):** you run steps 3–4 in your operator terminal
>   with the same `rbt-range` (env already exported by `arm` in the agent pane; in your terminal
>   set `RBT_SERVICE_SOCKET` and `RBT_RANGE_CLONE=jsl-attacker`). Deterministic, always completes.
> - **Agent-driven:** allow `./rbt-range` in the agent's Claude Code permissions first, then the
>   agent completes 3–4 itself (step 4 pauses for your approval).
>
> Either way the evidence and the approval exchange are identical. The rest of this script uses the
> operator terminal for steps 3–4.

Operator terminal (steps 3–4 continue in Scene 6):

```sh
export RBT_SERVICE_SOCKET="$HOME/Library/Application Support/RhubarbTart/service.sock"
export RBT_RANGE_CLONE=jsl-attacker
# step 3 — write a note into the clone (not tiered, runs immediately):
./rbt-range -- "mkdir -p ~/evidence && printf '# Note\nJuice Shop reachable on the lab link.\n' > ~/evidence/note.md && echo saved"
```

## Scene 6 — A sensitive action pauses for approval (90s)

Now run the **destructive** step (a scratch cleanup). Destructive operations are marked **tiered**
in the herdr config, so it's held for approval. Run it in the background so you can approve it:

```sh
# step 4 — a destructive cleanup; this is held for approval:
./rbt-range -- "mkdir -p ~/scratch && date > ~/scratch/tmp && rm -rf ~/scratch && echo cleaned" &
```

`rbt-range` prints `APPROVAL REQUIRED … request <id>` and waits. Show the held request:

```sh
./rhubarb herdr pending juiceshop-lab      # the held command + its request id
```

**Say:** "The operator decides. Grants are single-use and recorded."

```sh
./rhubarb herdr approve juiceshop-lab <REQUEST_ID>
```

The backgrounded `rbt-range` resumes and prints `cleaned`. The point is the *gate*, and that the
whole request → grant → consume → run exchange lands in the evidence journal.

## Scene 7 — The evidence (60s)

**Say:** "Every command, output, artifact, and the approval exchange is one hash-chained journal."

```sh
./rhubarb evidence collect juiceshop-lab   # pull the agent's ~/evidence + Juice Shop ground truth
./rhubarb evidence list juiceshop-lab      # exec, artifact, ground_truth, approval, lifecycle
./rhubarb evidence verify juiceshop-lab    # recompute the chain and every item
```

## Scene 8 — Seal a signed, portable vault (45s)

**Say:** "Finally we seal it: a signed, read-only bundle that verifies anywhere with nothing but
the bundle."

```sh
./rhubarb vault seal juiceshop-lab --out /private/tmp/rbt-demo/vaults
./rhubarb vault verify /private/tmp/rbt-demo/vaults/juiceshop-lab-*.vault
```

**Close:** "Verified image, bounded engagement, host-enforced isolation, an agent that can only
reach its range through a recorded, gated path, and signed evidence at the end. That's the whole
chain."

---

## After you record

```sh
./demo/teardown.sh
# optional, to remove this run's evidence store:
# rm -rf ~/Library/Application\ Support/RhubarbTart/evidence/juiceshop-lab
```

## If something stalls on camera

- **Agent ignores `rbt-range`:** re-paste step 1 from `demo/agent-task.md`; the prompt says it's
  the only tool.
- **Link check fails:** `./rhubarb ssh jsl-attacker -- 'curl -sf http://127.0.0.1:3000/ >/dev/null && echo ok'`;
  if not ok, the link (a background `engagement connect`) may have dropped — re-run
  `./demo/preflight.sh` to reopen it.
- **`herdr arm` says the service isn't running:** `./demo/preflight.sh` restarts it.
- **You want a clean slate:** `./demo/teardown.sh && ./demo/preflight.sh --reset`.
