# Agent task (paste into the `recon` agent pane)

This is the rehearsed prompt for the live Claude agent in the demo. It demonstrates
RhubarbTart's **scoped range client** and the **tiered-approval** workflow with benign commands
in an isolated lab VM we own — no attack simulation, no data exfiltration. Copy the block below
into the `recon` pane after `arm`, then narrate while it works.

> Every action goes through `rbt-range` (recorded as evidence). Step 4 is a **destructive**
> command (a scratch cleanup), which is a *tiered* action, so the operator-approval pause happens
> on camera. Nothing leaves the box.

---

You are demonstrating RhubarbTart's scoped range tooling on an authorized internal lab. In this
armed engagement, `rbt-range -- <command>` runs a shell command inside the lab VM through the
control plane, and every command is recorded as tamper-evident evidence on the host. The lab's
OWASP Juice Shop service (which we build and own) is reachable at `http://127.0.0.1:3000`.

Please run these steps with `rbt-range`, briefly narrating each:

1. Confirm access: `rbt-range -- id`
2. Read the local service's challenge list (this is our own app, on loopback):
   `rbt-range -- "curl -s http://127.0.0.1:3000/api/Challenges/ | head -c 300"`
3. Write a short note into the evidence folder (stays on the box):
   `rbt-range -- "mkdir -p ~/evidence && printf '# Note\n\nJuice Shop reachable on the lab link; challenge API responded.\n' > ~/evidence/note.md && echo saved"`
4. Clean up a scratch directory. This is a **destructive** command, so it is a tiered action and
   will pause for operator approval — wait for it to be approved, then confirm it completed:
   `rbt-range -- "mkdir -p ~/scratch && date > ~/scratch/tmp && rm -rf ~/scratch && echo cleaned"`

Stop after step 4 and summarize what you did. Do not run any command that sends data off the box.
