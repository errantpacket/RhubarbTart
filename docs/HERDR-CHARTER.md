# herdr charter

*Status: draft for owner review. This is the agreed boundary for herdr (#33), written before any
herdr code, per the build order on #33. It governs what herdr may and may not do; the phased
build follows only once it is accepted. Open decisions for the owner are marked **[DECISION]**.*

## Why this document exists

herdr is the one component of the plan that drives an **autonomous agent against live targets**.
That agent is the most powerful and least trusted thing in the system: it acts on its own, and a
target may subvert it. Every other part of RhubarbTart exists to keep that power boxed in —
provenance-verified images, per-engagement isolation, host-enforced scope, and sealed evidence.
herdr must not become the hole in that box. This charter fixes the boundary first, so the
implementation has one thing to conform to and the security review has one thing to check.

The rule in one line: **herdr drives the agent, the control plane governs the range, the manifest
bounds both.**

## What herdr is, and is not

**herdr is** the management interface for agent-driven engagements: it launches and supervises
agent processes, shows their live state (working / blocked / idle), streams their PTYs, prompts
the operator for approvals, and enforces budgets and stops. It is the reference frontend for the
Phase 5 goal.

**herdr is not** a place where trust decisions or privileged operations live. It never calls
`tart`, never touches the keychain, never opens a network path to a range, and never reads or
writes the evidence store directly. It asks the control plane to do those things, exactly like the
CLI and TUI do.

This follows the plan's settled principle — **one audited core, many thin frontends**
([PLAN.md](PLAN.md), "Management interface & control-plane API"). The security-critical logic is
in `tools/rhubarb/` (today's typed core: `api.py` over `clones.py` / `hostops.py` / `engagements.py`
/ `evidence.py` / `vault.py`). herdr is a client of that core and nothing more.

## Architecture and the trust boundary

```
  operator ── herdr (host, localhost only) ── control-plane API (tools/rhubarb/*) ── keychain / tart
                     │                                     │
                     │ launches + supervises               │ issues a scoped session into the range
                     ▼                                     ▼
              agent process (host)  ──── control-plane session ────▶  range VM (engagement N)
                     │                                                        ▲
                     └── model calls (optional AI gateway) ──▶ provider       │ host-enforced isolation (#30):
                                                                              │ egress only to in-scope targets
                                                                              │ + the evidence sink
```

Three boundaries, each already built or specified:

1. **Agent → range.** The agent runs on the host inside herdr and reaches its engagement's VMs
   **only through a control-plane session** — an `rhubarb`-issued handle to "the Kali box in
   engagement N". It never receives host credentials, the clone's password, or raw network access.
   The powerful, untrusted thing stays one layer removed from both the host and the targets.
2. **Range → everything else.** Host-enforced per-engagement isolation (#30): a range reaches only
   the manifest's in-scope targets and the evidence sink; other engagements, the operator's LAN,
   and the public internet are default-deny, dropped below the guest. A subverted agent cannot
   widen its own reach.
3. **herdr → host.** herdr binds **localhost only**, authenticated, never a listening network
   interface by default. It is the layer that may drive VMs and touch the keychain *by asking the
   core*, so it earns its own security review before it ships.

## Invariants herdr inherits (non-negotiable)

herdr must preserve every existing guarantee. It may not weaken one for convenience:

- **No frontend touches `tart` or the keychain directly.** herdr calls the typed core.
- **No reusable credentials reach the agent.** Passwords stay in the keychain; the agent gets a
  scoped session, not a secret.
- **Scope is enforced below the agent,** in host networking, never by asking the agent to behave.
- **Every agent action is captured as evidence** (#85) and sealed (#86); nothing the agent does is
  only watched live.
- **The manifest is immutable at runtime.** herdr and the agent read it; neither rewrites it.
- **The vault is append-only until sealed, immutable after.** An agent writes evidence; it cannot
  alter what is already recorded.

## The capability model

What an agent may do is the **intersection** of the engagement's scope manifest and a per-agent
capability grant. Concretely, an agent **may**:

- run tools in its own engagement's range VMs (through the control-plane session),
- read its own engagement's state (its clones, its manifest scope),
- write evidence (command output and artifacts, into its engagement's store).

An agent **may not**:

- change the manifest, or its own budget/scope,
- reach another engagement's ranges, state, or evidence,
- alter the vault or any recorded evidence after it is written,
- touch the control plane's own configuration, the keychain, or `tart`,
- open a network path the manifest's scope does not allow (it physically cannot — #30).

## Guardrails

- **Tiered actions.** Read / enumerate / run-in-VM proceed autonomously. Actions the manifest
  marks sensitive — anything reaching a production target, destructive operations, or
  exfiltration-shaped moves — **pause for operator approval** in herdr before they run.
- **Hard stops are real.** The manifest's `agent_budget` (wall-clock, max spend, a kill-time)
  hard-stops the agent, and a stop can auto-seal the engagement. These are already validated
  syntactically by `engagements.py`; herdr enforces them at runtime.
- **Everything is evidence.** Agent commands run through the same `api.exec` path that journals
  them on the host (#85); model prompts/responses can be pulled in via the gateway (below). After
  the fact the run is auditable, not merely observed.

## Lifecycle

herdr operates over the engagement lifecycle already defined
([PLAN.md](PLAN.md), "Engagements"), owning only the **arm / run** steps:

```
define → provision → [arm → run] → collect → seal → teardown
         (built #84,   herdr        (built    (built   (built
          #30 links)   drives here)  #85)      #86)     #85/#86)
```

- **arm** — herdr attaches the permitted agents to an engagement's ranges and hands each a scoped
  session plus its capability grant. Nothing runs yet.
- **run** — the agents work; herdr supervises, prompts for tiered approvals, and enforces budgets.
- Everything before and after arm/run is existing control-plane work; herdr calls it, and does not
  reimplement provision, collect, seal or teardown.

Sealing remains the one-way door: once sealed, evidence is immutable and ranges are gone. A re-test
is a new engagement that may reference the old vault, never a reopening of it.

## Prerequisites (status)

The build order on #33 requires scope enforcement below the agent and evidence capture to exist
before herdr. As of this draft:

| Prerequisite | Status |
|---|---|
| Engagement object + lifecycle (Phase 1) | done (#42, #44) |
| Network isolation / scope below the guest (Phase 2) | done (#30) |
| Evidence capture (Phase 3) | done (#85) |
| Vault + custody / seal (Phase 4) | done (#86) |
| Typed core the frontend imports | done (`tools/rhubarb/api.py`) |

So herdr is **unblocked** on the safety prerequisites. What remains before code is this charter's
acceptance and the **[DECISION]** points below.

## Decisions for the owner

Each of these changes the implementation; none should be guessed.

- **[DECISION] Agent runtime.** herdr as described (PTY-based multi-agent supervisor) — do we
  adopt an existing herdr, wrap the Claude Agent SDK, or build a thin supervisor of our own? This
  sets how agents are launched, supervised, and attached to a range session.
- **[DECISION] Service shape.** Confirm herdr is a localhost-bound, authenticated FastAPI service
  over the typed core (per PLAN Phase 5), never binding a network interface by default. Auth
  mechanism for the local operator to settle (token file in the keychain vs OS-user trust).
- **[DECISION] AI gateway.** Route model calls through a gateway (Cloudflare AI Gateway, AWS
  Bedrock AgentCore) for one place to hold provider keys, cap spend, and log every prompt/response
  into evidence — or start with direct provider access (the plan's default) and add a gateway
  later. Pluggable per engagement in the manifest either way.
- **[DECISION] Approval UX.** How tiered-action approvals are surfaced and recorded — inline in the
  herdr PTY view, a separate approvals queue — and whether an approval is itself an evidence entry
  (recommended).
- **[DECISION] Per-engagement driver VM.** Ship with the agent on the host (simpler, the plan's
  default), or offer the stronger per-engagement driver-VM isolation from the start, enabled in the
  manifest. Recommend host-first, driver VM as a later manifest option.
- **[DECISION] Manifest additions.** The manifest already carries `agent_budget` and (validated)
  scope. Confirm the per-agent **capability grant** and **tiered-action** markings live in the
  manifest (reviewable, committed) rather than in herdr configuration.

## Non-goals

- Not a generic VM GUI, and not a place that re-implements clone/keychain logic (both rejected in
  PLAN.md — they would fork or bypass the trust model).
- Not a multi-tenant or remote-exposed service in this phase; localhost operator use only.
- Not a scheduler across many hosts — that is Orchard, Phase 7, and stays behind the control plane.
