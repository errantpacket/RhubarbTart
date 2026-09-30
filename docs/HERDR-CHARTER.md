# herdr charter

*Status: accepted (#102) and implemented. This is the agreed boundary for herdr (#33), written
before any herdr code, per the build order on #33. It governs what herdr may and may not do. The
build followed in two parts: the control-plane service (#104) and the herdr integration (#108).
The "Decisions" section records each runtime decision and its rationale, and notes where the build
differs. The remaining work (budget enforcement, prompt capture, a per-engagement agent VM) is
tracked in #126.*

## Why this document exists

herdr is the one component of the plan that drives an **autonomous agent against live targets**.
That agent is the most capable and least trusted part of the system: it acts on its own, and a
target may subvert it. The rest of RhubarbTart keeps it contained: provenance-verified images,
per-engagement isolation, host-enforced scope, and sealed evidence. herdr must not become the hole
in that containment. This charter fixes the boundary first, so the
implementation has one thing to conform to and the security review has one thing to check.

The rule in one line: **herdr drives the agent, the control plane governs the range, the manifest
bounds both.**

## What herdr is, and is not

**herdr is** the management interface for agent-driven engagements: it launches and supervises
agent processes, shows their live state (working / blocked / idle), streams their PTYs, prompts
the operator for approvals, and enforces budgets and stops. It is the reference frontend for the
Phase 5 goal. (As built, approvals go through the control plane rather than herdr, and budgets are
not enforced yet; see "Decisions".)

**herdr is not** a place where trust decisions or privileged operations live. It never calls
`tart`, never touches the keychain, never opens a network path to a range, and never reads or
writes the evidence store directly. It asks the control plane to do those things, the same way the
CLI and TUI do.

This follows the plan's settled principle: **one audited core, many thin frontends**
([PLAN.md](PLAN.md#management-interface-and-control-plane-api)). The security-critical logic is
in `tools/rhubarb/` (the typed core: `api.py` over `clones.py` / `hostops.py` / `engagements.py`
/ `evidence.py` / `vault.py`). herdr is a client of that core and nothing more. The herdr glue
itself (`tools/rhubarb/herdr.py`, `agent.py`, `approvals.py`) lives in the same package and
reaches the core only through `api` and the service socket.

## Using herdr.dev as the runtime (decided)

We adopt [**herdr.dev**](https://herdr.dev) as the agent runtime rather than building our own
supervisor. It is a Rust, tmux-like, agent-aware terminal multiplexer: a background server owns
each agent's real PTY, sessions survive sleep / Wi-Fi / SSH drops, it tracks each agent as
working / blocked / idle / done, runs the agent CLIs (Claude Code, Codex, …) unmodified, and
exposes a **socket API and CLI wrappers** to drive panes and subscribe to events. It is a
runtime, not a scope or evidence layer. RhubarbTart fills that gap, so the two fit together as
long as the boundary below holds.

**Integration model: A now, C as a later manifest option, B never as the action path.**

- **A: herdr supervises; `rhubarb` is the only way into a range (default).** herdr runs each agent
  CLI in a host PTY, unmodified. The agent's *sole* tool for acting in a range is a thin
  `rhubarb exec` wrapper (a control-plane session), so every command is journaled as evidence
  (#85) and scope is enforced below the guest (#30). We **do not** use herdr's own "SSH a pane
  into a machine" feature to reach ranges. That would hand the agent the clone credential and a
  raw route, and bypass host-side journaling (**option B**, rejected as an action path; allowed at
  most as a read-only convenience view). Built in #108: the wrapper is `rbt-range`
  (`tools/rhubarb_agent.py`, `tools/rhubarb/agent.py`), pinned to one clone per agent.
- **C: per-engagement driver VM (manifest-selectable, later).** For sensitive engagements, herdr
  and the agent run inside a dedicated per-engagement driver VM that then reaches the range through
  the control plane. This puts another VM boundary between the agent and the host. Enabled in the
  manifest; deferred until after A ships. Not built.

**The socket API is the guardrail seam.** The plan was to use herdr's socket API to (1) read
agent state for the interface (working / blocked / idle), (2) **pause a pane for a tiered-action
approval** and inject the operator-approved command, and (3) subscribe to events so the run folds
into evidence. Every approval is itself an evidence entry. As built (#111), the seam moved to the
control plane: the exec path holds a tiered command, and the operator approves it with
`rhubarb herdr approve` (see "Approval UX" below). `arm` drives herdr through its CLI.

## Architecture and the trust boundary

```
  operator ── herdr (host, localhost only) ── control-plane API (tools/rhubarb/*) ── keychain / tart
                     │                                     │
                     │ launches + supervises               │ issues a scoped session into the range
                     ▼                                     ▼
              agent process (host)  ──── control-plane session ────▶  range VM (engagement N)
                     │                                                        ▲
                     └── model calls (optional AI gateway) ──▶ provider       │ host-enforced isolation (#30):
                                                                              │ no direct clone-to-clone route,
                                                                              │ explicit links only
```

Three boundaries, each already built or specified:

1. **Agent → range.** The agent runs on the host inside herdr and reaches its engagement's VMs
   **only through a control-plane session**: an `rhubarb`-issued handle to "the Kali box in
   engagement N". It never receives host credentials, the clone's password, or raw network access.
   The untrusted part stays one layer removed from both the host and the targets. On the host the
   agent runs as the operator, so this is the sanctioned and recorded path, not a kernel sandbox
   (see `tools/rhubarb/agent.py`); hard isolation of the agent is model C.
2. **Range → everything else.** The plan was host-enforced per-engagement default-deny egress: a
   range reaches only the manifest's in-scope targets and the evidence sink. As built (#30, #95),
   Tart's default NAT keeps clones from reaching each other, and attacker-to-target paths exist
   only as explicit manifest `links` opened by `rhubarb engagement connect`. Lab targets have no
   egress. SSH into clones is pinned to the host (`from="192.168.64.1"`). Softnet, which would
   give per-VM egress policy, was evaluated and rejected for now (reasons on #30).
3. **herdr → host.** The control plane that herdr talks to binds **no network interface**: it is
   a Unix socket at 0600 owned by the operator (`rhubarb serve`, #104). It is the layer that may
   drive VMs and touch the keychain *by asking the core*, so it gets its own security review.

## Invariants herdr inherits (non-negotiable)

herdr must preserve every existing guarantee. It may not weaken one for convenience:

- **No frontend touches `tart` or the keychain directly.** herdr calls the typed core.
- **No reusable credentials reach the agent.** Passwords stay in the keychain; the agent gets a
  scoped session, not a secret.
- **Scope is enforced below the agent,** in host networking, never by asking the agent to behave.
- **Every agent action is captured as evidence** (#85) and sealed (#86). Nothing the agent does is
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
- open a network path between clones that the manifest's `links` do not declare (Tart's NAT
  keeps clones apart; #30).

## Guardrails

- **Tiered actions.** Read / enumerate / run-in-VM proceed autonomously. Sensitive actions
  (anything reaching a production target, destructive operations, or exfiltration-shaped moves)
  **pause for operator approval** before they run. As built (#111), the herdr config marks them
  with regexes (see "Capability grants" below).
- **Hard stops.** The manifest's `agent_budget` (wall-clock, max spend, a kill-time) hard-stops
  the agent, and a stop can auto-seal the engagement. `engagements.py` validates these fields.
  Runtime enforcement by herdr is not built yet.
- **Everything is evidence.** Agent commands run through the same `api.exec` path that journals
  them on the host (#85). Model prompts and responses are not captured yet (see "AI gateway"
  below). After the fact the run is auditable, not only observed.

## Lifecycle

herdr operates over the engagement lifecycle already defined
([PLAN.md](PLAN.md), "Engagements"), owning only the **arm / run** steps:

```
define → provision → [arm → run] → collect → seal → teardown
         (built #84,   herdr        (built    (built   (built
          #30 links)   drives here,  #85)      #86)     #85/#86)
                       built #108)
```

- **arm:** herdr attaches the permitted agents to an engagement's ranges and hands each a scoped
  session plus its capability grant. Nothing runs yet. Built as `rhubarb herdr arm ID` (#110),
  which reads `engagements/<id>.herdr.json` and records an `arm` lifecycle entry in the evidence.
- **run:** the agents work; herdr supervises, tiered commands wait for approval, and budgets are
  meant to be enforced (not built yet).
- Everything before and after arm/run is existing control-plane work. herdr calls it and does not
  reimplement provision, collect, seal or teardown.

Sealing remains the one-way door: once sealed, evidence is immutable and ranges are gone. A re-test
is a new engagement that may reference the old vault, never a reopening of it.

## Prerequisites (status)

The build order on #33 requires scope enforcement below the agent and evidence capture to exist
before herdr. Status as of 2026-09-30:

| Prerequisite | Status |
|---|---|
| Engagement object + lifecycle (Phase 1) | done (#42, #44) |
| Network isolation / scope below the guest (Phase 2) | done, as NAT isolation + explicit links (#30, #95) |
| Evidence capture (Phase 3) | done (#85) |
| Vault + custody / seal (Phase 4) | done (#86) |
| Typed core the frontend imports | done (`tools/rhubarb/api.py`) |
| Control-plane service herdr sits on | done (`tools/rhubarb/service.py`, #104) |

All prerequisites are met. The charter was accepted (#102), the control-plane service shipped
(#104), and the herdr integration followed (#108).

## Decisions

Each of these shapes the implementation. All are resolved; the rationale is kept for the record,
with a note where the build differs.

- **Agent runtime. DECIDED: herdr.dev, integration model A now, C later.** See
  "Using herdr.dev as the runtime" above. We adopt herdr.dev unmodified; the agent reaches a range
  only through a `rhubarb exec` control-plane session (A), with the per-engagement driver VM (C) as
  a later manifest-selectable option and herdr's direct-SSH-into-clone path (B) excluded as an
  action path.
- **Service shape. DECIDED: Unix domain socket, 0600.** A service over the typed core, bound to
  a Unix domain socket at 0600 owned by the operator. There is no open TCP port and no token to
  manage; filesystem permissions are the boundary, matching the StrictModes clone-record model.
  It never binds a network interface by default. Remote access (TCP + token) would be a later,
  separately reviewed step, not part of this phase. The plan named FastAPI/ASGI; the build (#104)
  uses stdlib HTTP instead, so there is no web framework to pin (`tools/rhubarb/service.py`,
  `rhubarb serve [--socket PATH]`). It serves read-only views (#105), guarded actions (#106) and
  an NDJSON evidence event stream (#107).
- **AI gateway. DECIDED: direct Claude-subscription access now; gateway support later.** Model
  access is a Claude subscription (OAuth), so the agent CLI authenticates itself and there is no
  metered provider key for a gateway (Cloudflare AI Gateway, Bedrock AgentCore) to front. Spend is
  covered by the subscription, so `max_spend_usd` is advisory. **Wall-clock and kill-time budgets
  are meant to be enforced by herdr**; that enforcement is not built yet. Prompt/response logging
  was to go through the **evidence pipeline** (herdr socket events), not a gateway; it is not
  built yet either. The manifest has no gateway field yet. Cloudflare and Bedrock support for a
  future API-key setup is tracked in #103.
- **Approval UX. DECIDED: through herdr's socket API; changed during the build.** The plan was to
  read agent state for the sidebar, pause a pane for a tiered-action approval, inject the approved
  command, and subscribe to events into evidence, with each approval an evidence entry. As built
  (#111, `tools/rhubarb/approvals.py`), the control-plane exec path holds a tiered command instead
  of running it and journals a request. `rbt-range` shows the request in the agent's pane and
  waits. The operator lists holds with `rhubarb herdr pending` and releases one run with
  `rhubarb herdr approve`. Grants are single-use, and request, grant and use are all evidence
  entries. herdr's CLI has no way to post a notification, so the hold is not pushed to its
  sidebar.
- **Per-engagement driver VM. DECIDED: host-first, driver VM later (model C).** Ship with the
  agent on the host (model A), and add the per-engagement driver VM as a manifest-selectable option
  afterwards. The remaining detail is the driver VM's own profile and how it reaches the range,
  settled when C is built.
- **Capability grants and tiered-action markings. DECIDED: herdr configuration (host model), with
  the manifest as the home under the driver-VM model.** In the host-based default (model A) the
  per-agent **capability grant** and which actions are **tiered** (approval-gated) live in herdr's
  config, not the engagement manifest. The intent is **flexibility**: agent policy can be tuned
  without re-signing the engagement, while the manifest stays authoritative for the hard boundary
  (targets, ranges and `agent_budget`, validated by `engagements.py`). To keep this auditable,
  **herdr config is version-controlled and its effective policy is captured as an evidence entry
  at arm time**, so a sealed vault still shows what the agent was permitted to do. As built, the
  config is `engagements/<id>.herdr.json`: each agent's name, kind, assigned clone and optional
  model, plus the `tiered` regexes; `arm` records its hash. Under the future driver-VM design
  (model C), where herdr itself runs inside a per-engagement VM, that policy **moves into the
  manifest**, since the agent's own runtime is then part of the engagement's sealed scope.

## Non-goals

- Not a generic VM GUI, and not a place that re-implements clone/keychain logic (both rejected in
  PLAN.md, because they would fork or bypass the trust model).
- Not a multi-tenant or remote-exposed service in this phase; localhost operator use only.
- Not a scheduler across many hosts. That is Orchard, Phase 7, and stays behind the control plane.
