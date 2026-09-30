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

Paste the prompt from **`demo/agent-task.md`** into the `recon` pane. Narrate as it:

1. confirms access (`rbt-range -- id`),
2. reads the Juice Shop challenge API (ground truth),
3. writes a finding to `~/evidence`.

**Say:** "Everything it does goes through the control plane and is recorded as evidence on the
host — where the guest under test can't tamper with it."

## Scene 6 — A sensitive action pauses for approval (90s)

The agent's last step tries to **exfiltrate** to an external host. That command is **tiered**, so
it's held.

**In the agent pane:** it prints `APPROVAL REQUIRED … request <id>` and waits.

**Switch to the operator terminal:**

```sh
./rhubarb herdr pending juiceshop-lab      # the held command + its request id
```

**Say:** "The operator decides. Grants are single-use and recorded."

```sh
./rhubarb herdr approve juiceshop-lab <REQUEST_ID>
```

**Switch back to herdr:** the agent resumes and completes. (Exfil to `evil.example` fails to
resolve — fine; the point is the *gate*, and that it's on the record.)

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
