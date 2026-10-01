# Using your VMs

Built images are templates: you work in disposable **clones** of them (see
[Key concepts](concepts.md#what-you-work-in)). Clones are managed by the `rhubarbtart` CLI, or by the
`./rhubarbtart-tui` Textual dashboard, which drives the same actions over the same audited core:
browse images, clones, provenance and logs; run, ssh, enroll, reset, rm, new and build, with a
confirmation before anything destructive. For stacked macOS clones from a registry, see
[Publishing and stacked clones](publishing.md).

The dashboard is keyboard-driven. `1`–`4` open the tabs (Images, Clones, Provenance, Logs) and
put the cursor in them; Enter on an image or clone shows its provenance (a clone shows the image it
was cloned from); `r` refreshes, `?` lists every key and `q` quits. The footer shows only the keys
that apply: clone actions (`b` run, `s` ssh, `e` enroll, `x` reset, `d` remove) are on the Clones
tab, while `n` (new clone) and `B` (build) work anywhere. Actions run one at a time; the header
shows the one in progress. Data refreshes in the background, so a slow `tart list` never freezes
the screen. The Logs tab lists each clone's run log, the build logs written by `B`
(`logs/build-<profile>-<UTC time>.log` in the state directory, mode 0600, with the VNC password
redacted) and `events.log`. It follows a growing log while you're at its end, and holds still
while you scroll back. `B` runs `scripts/build.sh`, so like any build it needs the Mac's GUI login
session; it refuses to start over SSH.

Inside herdr it leaves the mouse to herdr; use `./rhubarbtart-tui --mouse` (or `RHUBARB_TUI_MOUSE=1`)
to capture it anyway, or `--no-mouse` to turn it off elsewhere. It also takes herdr's colour theme:
the `[theme]` in herdr's `config.toml`, including `auto_switch` (dark or light follows the Mac's
appearance) and any `[theme.custom]` colours. Use `--theme NAME` (or `RHUBARB_TUI_THEME=NAME`) to
pick a Textual theme instead, for example `nord` or `catppuccin-latte`.

```sh
./rhubarbtart images                              # built images; which one is current per profile
./rhubarbtart new web-1 --profile kali-research   # clone the current image; give it its OWN password
./rhubarbtart new mac-1 --profile tahoe-research --from-registry   # macOS: stack on the verified registry copy
./rhubarbtart run web-1                           # GUI (Rosetta applied if the profile uses it)
./rhubarbtart ssh web-1                           # key-only SSH, host key pinned per clone
./rhubarbtart enroll web-1 tailscale              # VPN identity for this clone only
./rhubarbtart list                                # clones: state, outdated image?, password, enrollment
./rhubarbtart reset web-1                         # destroy + fresh clone of the current image
./rhubarbtart rm web-1                            # delete the clone (+ keychain entry if it had a unique password)
```

| Command | What it does |
|---|---|
| `new NAME --profile P` / `--image IMG` | Clones a **verified** image (never `-unverified`), records its lineage, then boots it headless and rotates it to a **unique random password** (keychain account = clone name), proving through `sudo` that the old one is rejected. `--no-rotate` keeps the image's password. `--from-registry` (macOS only, #31): verify the image's published copy by digest (signature + provenance, `scripts/publish.sh`) and stack the clone on it (`tart clone --stacked`, tens of MB instead of a full copy). `list` shows `base-missing` if the pulled base is gone, and `rm` releases the base when no other clone uses it |
| `run NAME [--headless] [--detach]` | Starts the clone with the right flags for its profile (`--rosetta=rosetta` for Linux Rosetta profiles) |
| `stop NAME` | Stops a running clone (the VM and its records stay) |
| `ssh NAME [-- CMD]` | Connects as the profile's user, host key pinned per clone name |
| `enroll NAME tailscale\|warp [--org TEAM]` | Runtime VPN/ZTNA enrollment (see below) |
| `images` | Lists built images and which one is current for each profile |
| `list` | Lists clones and flags those whose image is **outdated** (the profile's lock changed) or **deleted**, and shows each clone's password mode and enrollments |
| `reset NAME [--same-image] [--no-rotate]` | Throws the clone away (identity, enrollment and all) and re-clones, from the current image by default. A stacked clone stays stacked |
| `rm NAME [--yes]` | Stops and deletes the clone, its keychain entry (only if it had a unique/rotated password) and its pinned host key |

> **Note:** `rhubarbtart` only touches clones it created. It never modifies built images (`rbt-…`) or VMs
> made some other way, and clone names can't start with `rbt-`. Per-clone rotation needs key
> SSH (images built with `RHUBARB_SSH_PUBKEYS`, with the key in `ssh-agent` or `RHUBARB_SSH_IDENTITY`
> pointing at it). Without it the clone keeps the image's password, and `rhubarbtart list` says
> `inherited`. For an image built with SSH disabled, `new` skips the rotation without booting the
> clone and prints a hint to rebuild with `RHUBARB_SSH_PUBKEYS`.

### Engagements

An **engagement** is a committed scope manifest (`engagements/<id>.json`) naming a set of ranges
(profile + count) that build and tear down as one unit; clones it provisions are tagged with the
engagement id. Committed examples: `demo.json`, `lab.json` and `juiceshop-lab.json` (a Kali
attacker linked to a Juice Shop target).

| Command | What it does |
|---|---|
| `engagement define FILE\|ID` | Validate a manifest and acknowledge it (strict: unknown keys, bad ranges or a missing `authorization` are rejected) |
| `engagement list` | Defined engagements and their live clone counts |
| `engagement provision ID` | Clone each range from its **verified** image into engagement-tagged clones |
| `engagement connect ID` | Open the manifest's `links` and hold them until Ctrl-C (both clones must be running) |
| `engagement teardown ID [--yes] [--no-collect]` | Collect evidence from running clones, then remove exactly the clones tagged to that engagement (nothing else) |
| `exec NAME -- CMD` | Run a command in a clone; for an engagement clone, the command and its output are recorded as evidence |
| `evidence collect\|list\|verify ID` | Pull each clone's `~/evidence` folder; show the journal; recompute the hash chain |

**Evidence.** Each engagement keeps a record on your Mac, under
`~/Library/Application Support/RhubarbTart/evidence/<id>/`, never in the clones:

- **Commands.** `rhubarbtart exec` (and agents, through the same API) records the command, user,
  exit code, times and full output of each command run in an engagement clone. Interactive
  `rhubarbtart ssh` sessions are not recorded.
- **Files.** Anything a clone puts in its user's `~/evidence/` folder (scan output, notes,
  findings) is pulled by `evidence collect` and hashed as it arrives. The folder comes over as a
  tar stream that is read, never unpacked, so symlinks and paths outside the folder are refused.
  Unchanged files aren't recorded twice. `teardown` collects first, because a clone's files die
  with it; a clone that is stopped at that point can't be collected.
- **Ground truth.** A package can declare an endpoint that says what really happened. For Juice
  Shop, `collect` saves `/api/Challenges/`, which lists the challenges actually solved, to check
  claimed findings against.
- **Approvals.** Each step of a tiered action (request, grant, use) is an entry
  (see **Tiered actions** below).
- **Lifecycle.** Provision (with the image each clone came from), connect and disconnect,
  collect, `herdr arm`, seal and teardown are recorded too.

Every entry is one line of `journal.jsonl` and includes the hash of the entry before it, and
outputs and files are stored by their sha256. `evidence verify` recomputes all of it and reports
an edited, reordered or deleted entry or an altered file.

**Control-plane service.** `rhubarbtart serve` runs a small HTTP service over a **Unix domain socket**
(0600, `service.sock` in the state directory by default, or `--socket PATH`). herdr agents reach
their range through it ([charter](HERDR-CHARTER.md)). There is no TCP port and no token: filesystem
permissions on the socket are the boundary, like the clone records. Every endpoint is a call into the same
audited core; the service never touches `tart` or the keychain itself.

- **GET** (read-only, the same data the TUI shows): `/health`, `/images`, `/clones`,
  `/engagements`, `/engagements/<id>/evidence`, `/engagements/<id>/evidence/verify`,
  `/provenance/<vm>`.
- **POST** (guarded actions, each one core call that already journals evidence):
  `/engagements/<id>/provision|collect|seal|teardown`, and `/clones/<name>/exec` (run a command;
  its output is base64 in the JSON, so any bytes come back unchanged). Bad input is 400, an
  unknown route or id is 404, and the wrong method on a known route is 405.

- **Event stream:** `GET /engagements/<id>/events` tails that engagement's evidence journal as
  NDJSON. Add `?from=<seq>` to resume after an entry, or `?follow=false` to replay and stop. This
  is the control plane's live record of what happened, and the feed for herdr's sidebar.

**Scoped range client (for agents).** When herdr runs an agent against an engagement, the agent's
only tool for acting in its range is `rbt-range`. It runs a command in the one clone assigned to
it (`RBT_RANGE_CLONE`, through the service socket in `RBT_SERVICE_SOCKET`), so every command is
journaled as evidence and the clone is fixed, not chosen per call. Arguments are joined into one
command line, as with `ssh host <cmd>`, and its output and exit code are the remote command's.
This is the sanctioned, recorded path, not a sandbox: an agent on the host runs as you. Hard
isolation of an agent from other clones on the same host is the per-engagement driver VM
([charter](HERDR-CHARTER.md) model C), and the network limits what each clone can reach (#30).

**Arming agents (`rhubarbtart herdr arm ID`).** With an engagement provisioned and `rhubarbtart serve`
running, `arm` launches its configured agents under [herdr](https://herdr.dev): it creates a herdr
workspace, gives each agent its own pane pinned to one range clone (`RBT_RANGE_CLONE`) and the
control-plane socket, puts the repo on the pane's `PATH` so `rbt-range` resolves, and starts the
agent CLI there. Which agents run, of what kind, against which clone is set in committed herdr
config at `engagements/<id>.herdr.json`. Each agent has a `name`, a herdr `kind` (for example
`claude`), a `clone` and an optional `model`; the file may also hold `tiered` patterns (below). See
`engagements/juiceshop-lab.herdr.json`. `arm` records that config's hash and the agents it started
as an `arm` lifecycle entry in the evidence, so a sealed vault shows what each agent was permitted
to do.

**Tiered actions (approvals).** The herdr config may mark commands **tiered** with a list of
regexes (`"tiered": ["curl\\s.*://…", "rm\\s+-rf"]`). When a matching command is run in one of
the engagement's clones, it is **held**: the command does not run, a request lands in the
engagement's evidence, and the call returns exit code 126 (`approval_required`). `rbt-range`
prints the request id and retries until the command is approved, for up to 10 minutes by default
(`RBT_APPROVAL_WAIT`, in seconds). The operator sees held commands with `rhubarbtart herdr pending <id>`
and releases one run with `rhubarbtart herdr approve <id> <request>`; the agent's command then
proceeds. Each grant is single-use, and the whole exchange (request, grant, use, run) is evidence,
so a sealed vault shows which sensitive actions were permitted. This is a workflow guardrail on
the sanctioned `rbt-range` path, not a kernel boundary (charter model A).

**Sealing a vault.** `rhubarbtart vault seal ID` turns the evidence store into a signed, sealed,
portable bundle you can hand off:

| Command | What it does |
|---|---|
| `vault seal ID [--out DIR]` | Verify the chain, then write a read-only `<id>-<time>.vault/` (default: `vaults/` in the repository) |
| `vault verify DIR [--pub KEY]` | Check the vault's signature and every hash, offline, on any machine |

The bundle holds `root.json` (the engagement scope, the chain head, and the sha256 of the
journal and of every item), its signature `root.bundle.json`, the journal and items themselves,
each range's build provenance (which verified image produced the evidence), and `cosign.pub`.
`root.json` is signed (`cosign sign-blob`, through `scripts/vault.sh`) with the same offline
cosign key that signs published images ([publishing](publishing.md)), so one signature over it
anchors the whole set: `vault verify` checks the signature, then re-derives every hash. The tree
is made read-only on seal. On another machine the bundle verifies on its own, with cosign.
`vault verify` uses the bundled `cosign.pub` unless you pass `--pub`; pass the publisher's
committed key (`config/keys/rhubarb-cosign.pub`) to also check who signed it. Confidentiality at rest currently rests on the host disk
(FileVault); per-engagement encryption is a follow-up. Sealing is repeatable: more evidence, then
seal again for a new timestamped bundle.

**Networking between clones.** Clones can't reach each other: Tart's default network drops
traffic between VMs. A manifest's `links` are the only path, and they go through your Mac:

```json
"links": [ { "from": "jsl-attacker", "to": "jsl-target", "ports": [3000] } ]
```

`from` and `to` name ranges by their clone-name stem (the `prefix`, or `<engagement>-<profile>`).
`to` must be a range of one clone, and each port must be one its profile declares (a package's
`"ports"`). `connect` opens an SSH remote forward into every `from` clone, so the target's port
shows up on that clone's own loopback: in the Juice Shop lab, Kali browses
`http://127.0.0.1:3000`. Nothing else crosses, and the path closes with `connect`.

A lab target also has no egress of its own. The Juice Shop service may only talk to loopback and
the Mac (systemd `IPAddressDeny=any`), and a firewall rule rejects any new outbound connection it
makes, so an exploited app can't reach your LAN, the internet, or services on your Mac. The build
asserts both, and the smoke test checks the service's filter.

<details>
<summary><b>Why not Softnet?</b></summary>

Tart's `--net-softnet` gives each VM its own network and an egress policy, but in September 2026
its release binary is unsigned and not notarized, it must run as root (SUID or passwordless
sudo), and it moves each VM onto a random subnet, which breaks the images' pinned SSH source
address (`from="192.168.64.1"`). The evaluation is in [#30](https://github.com/errantpacket/RhubarbTart/issues/30). When
Tart ships its native host-only network (no root helper), lab targets can move onto it.

</details>

<details>
<summary><b>Where clone records live, and why</b></summary>

Records are kept in `~/Library/Application Support/RhubarbTart/` (override with
`RHUBARB_STATE_DIR`):

- **Outside the repo**, so they're never committed or synced with it, and **outside Tart's VM
  folders**, so they never ship inside a VM bundle or registry push.
- **No secrets:** they hold only names, lineage, username and timestamps. Passwords stay in the
  macOS keychain.
- **Like OpenSSH's StrictModes,** a record is trusted only if the folder is `0700` and the file
  is `0600`, owned by you, and a regular file (not a symlink). Its schema is strict and its name
  must match the file, so a planted or corrupted record can't steer the CLI at the wrong VM or
  keychain entry. Writes are atomic.
- **`events.log`** keeps an append-only trail (no secrets) of `new`, `rotate`, `enroll`,
  `reset` and `rm`.
- **They're not a boundary against malware running as you,** which could drive `tart` and your
  keychain directly anyway. They're about correctness and least surprise.

</details>

### VPN and ZTNA enrollment

Images never contain VPN identity. Each clone is enrolled at runtime, and the secrets never
touch the repo, a profile, the image, or a command line:

```mermaid
%%{init: {'theme':'base','fontFamily':'ui-sans-serif, system-ui, -apple-system, Helvetica, Arial, sans-serif','themeVariables':{'primaryColor':'#fff0f3','primaryBorderColor':'#c9184a','primaryTextColor':'#2b2d42','actorBkg':'#fff0f3','actorBorder':'#c9184a','actorTextColor':'#2b2d42','actorLineColor':'#c9a3ae','signalColor':'#8d99ae','signalTextColor':'#2b2d42','labelBoxBkgColor':'#fff0f3','labelBoxBorderColor':'#c9184a','labelTextColor':'#2b2d42','noteBkgColor':'#c9184a','noteTextColor':'#ffffff','noteBorderColor':'#800f2f','sequenceNumberColor':'#ffffff','activationBkgColor':'#c9184a','activationBorderColor':'#800f2f'},'sequence':{'mirrorActors':false,'messageAlign':'center','boxMargin':10,'noteMargin':10,'width':170}}}%%
sequenceDiagram
    autonumber
    actor You
    participant KC as 🔑 Host keychain
    participant EN as rhubarbtart enroll
    participant VM as Clone · web-1
    You->>KC: store the token once<br/>(prompted, never in argv)
    You->>EN: rhubarbtart enroll web-1 tailscale
    EN->>KC: read the clone password + token
    EN->>VM: send both over SSH stdin
    Note over VM: secret → 0600 temp file<br/>enroll → shred
    VM-->>You: connected
```

| Service | Command | Secret (keychain service `RhubarbTart-enroll`) | Notes |
|---|---|---|---|
| Tailscale | `rhubarbtart enroll web-1 tailscale` | account `tailscale-authkey` | Use a one-off, pre-approved, *tagged* key (ephemeral for throwaway clones). macOS: approve the system extension once per clone |
| Cloudflare WARP | `rhubarbtart enroll web-1 warp --org TEAM` | `warp-client-id`, `warp-client-secret` | A service token allowed to enroll devices; dashboard version pushes are disabled |

Store a secret once with `security add-generic-password -s RhubarbTart-enroll -a tailscale-authkey -w`
(it prompts, so the secret never lands in your shell history). `rhubarbtart enroll` wraps
`scripts/enroll.sh` and records which services each clone is enrolled in.

← back to the [README](https://github.com/errantpacket/RhubarbTart/blob/main/README.md)
