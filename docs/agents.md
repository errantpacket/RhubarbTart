# Agents with herdr

RhubarbTart can run AI agents against an [engagement](engagements.md) under
[herdr](https://herdr.dev), a terminal workspace manager for AI coding agents. Each agent works
in one assigned clone, every command it runs there is recorded as evidence, and commands that
match a pattern you set wait for your approval. The evidence stays on your Mac, outside the VMs.

The pieces, in the order you use them:

1. `rhubarbtart serve` runs the control-plane service that agents talk to.
2. `rhubarbtart herdr arm ID` starts the engagement's agents, each in its own herdr pane.
3. Each agent acts through `rbt-range`, which reaches only its own clone.
4. `rhubarbtart herdr pending ID` and `herdr approve ID REQUEST` release held commands.

| Command | What it does |
|---|---|
| `serve [--socket PATH]` | Runs the control-plane service on a Unix socket ([below](#the-control-plane-service)) |
| `herdr arm ID [--socket PATH]` | Starts the engagement's configured agents under herdr |
| `herdr pending ID` | Lists the tiered commands waiting for approval |
| `herdr approve ID REQUEST` | Grants one single-use approval for a pending request |

Not built yet: enforcing an engagement's agent budget, recording the agents' prompts and replies,
and running each engagement's agents in their own VM instead of on your Mac. The
[herdr charter](https://github.com/errantpacket/RhubarbTart/blob/main/docs/HERDR-CHARTER.md) sets the boundary for this work.

## The control-plane service

`rhubarbtart serve` runs a small HTTP service over a **Unix domain socket**
(0600, `service.sock` in the state directory by default, or `--socket PATH`). herdr agents reach
their range through it. There is no TCP port and no token: filesystem permissions on the socket
are the boundary, like the clone records. Every endpoint is a call into the same audited core;
the service never touches `tart` or the keychain itself.

- **GET** (read-only, the same data the TUI shows): `/health`, `/images`, `/clones`,
  `/engagements`, `/engagements/<id>/evidence`, `/engagements/<id>/evidence/verify`,
  `/provenance/<vm>`.
- **POST** (guarded actions, each one core call that already journals evidence):
  `/engagements/<id>/provision|collect|seal|teardown`, and `/clones/<name>/exec` (run a command;
  its output is base64 in the JSON, so any bytes come back unchanged).
- **Request bodies** are JSON. `exec` takes `command` (required) and an optional `timeout` in
  seconds; `teardown` takes `collect_first` (default `true`); `seal` takes an optional `out_dir`.
  The others need no body. `/health` also returns the RhubarbTart version.
- **Errors:** bad input is 400, an unknown route or id is 404, the wrong method on a known route
  is 405, and an unexpected failure is 500 with the error in the JSON.
- **Event stream:** `GET /engagements/<id>/events` tails that engagement's evidence journal as
  NDJSON. Add `?from=<seq>` to resume after an entry, or `?follow=false` to replay and stop. This
  is the control plane's live record of what happened, and the feed for herdr's sidebar.

## The rbt-range client

When herdr runs an agent against an engagement, the agent's
only tool for acting in its range is `rbt-range`. It runs a command in the one clone assigned to
it (`RBT_RANGE_CLONE`, through the service socket in `RBT_SERVICE_SOCKET`), so every command is
journaled as evidence and the clone is fixed, not chosen per call. Arguments are joined into one
command line, as with `ssh host <cmd>`, and its output and exit code are the remote command's.
This is the sanctioned, recorded path, not a sandbox: an agent on the host runs as you. Hard
isolation of an agent from other clones on the same host is the per-engagement driver VM
(model C in the [herdr charter](https://github.com/errantpacket/RhubarbTart/blob/main/docs/HERDR-CHARTER.md)), and the network limits what each clone can reach (#30).

## Arming agents

With an engagement provisioned and `rhubarbtart serve` running, `rhubarbtart herdr arm ID`
launches its configured agents under [herdr](https://herdr.dev). It creates a herdr workspace and
gives each agent its own pane. Each pane is pinned to one range clone (`RBT_RANGE_CLONE`) and the
control-plane socket; pass `--socket PATH` if `serve` uses another. `arm` puts the repo on the
pane's `PATH`, so `rbt-range` resolves, and starts the agent CLI there. Which agents run, of what kind, against which clone is set in committed herdr
config at `engagements/<id>.herdr.json`. Each agent has a `name`, a herdr `kind` (for example
`claude`), a `clone` and an optional `model`; the file may also hold `tiered` patterns (below). See
`engagements/juiceshop-lab.herdr.json`. `arm` records that config's hash and the agents it started
as an `arm` lifecycle entry in the evidence, so a sealed vault shows what each agent was permitted
to do.

The herdr config for the Juice Shop lab:

```json
{
  "agents": [
    { "name": "recon", "kind": "claude", "clone": "jsl-attacker", "model": "claude-opus-4-8" }
  ],
  "tiered": [
    "(^|[^a-zA-Z])curl\\s.*://(?!127\\.0\\.0\\.1|localhost)",
    "(^|[^a-zA-Z])(rm\\s+-rf|shutdown|mkfs)([^a-zA-Z]|$)"
  ]
}
```

## Approvals for tiered commands

The herdr config may mark commands **tiered** with a list of
regexes (`"tiered": ["curl\\s.*://…", "rm\\s+-rf"]`). When a matching command is run in one of
the engagement's clones, it is **held**: the command does not run, a request lands in the
engagement's evidence, and the call returns exit code 126 (`approval_required`). `rbt-range`
prints the request id and retries until the command is approved, for up to 10 minutes by default
(`RBT_APPROVAL_WAIT`, in seconds; `0` returns at once). The operator sees held commands with `rhubarbtart herdr pending <id>`
and releases one run with `rhubarbtart herdr approve <id> <request>`; the agent's command then
proceeds. Each grant is single-use, and the whole exchange (request, grant, use, run) is evidence,
so a sealed vault shows which sensitive actions were permitted. This is a workflow guardrail on
the sanctioned `rbt-range` path, not a kernel boundary (model A in the [herdr charter](https://github.com/errantpacket/RhubarbTart/blob/main/docs/HERDR-CHARTER.md)).
