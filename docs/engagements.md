# Engagements and evidence

An **engagement** is a committed scope manifest (`engagements/<id>.json`). It names a set of
ranges (a profile and a count) that are built and torn down as one unit, records who authorized
the work, and says which clones may reach each other. Clones it provisions are tagged with the
engagement id, and what happens in them is kept as evidence on your Mac. To run AI agents in an
engagement, see [Agents with herdr](agents.md).

Committed examples: `demo.json`, `lab.json` and `juiceshop-lab.json` (a Kali attacker linked to a
Juice Shop target).

## Commands

| Command | What it does |
|---|---|
| `engagement define FILE\|ID` | Validate a manifest and acknowledge it (strict: unknown keys, bad ranges or a missing `authorization` are rejected) |
| `engagement list` | Defined engagements and their live clone counts |
| `engagement provision ID` | Clone each range from its **verified** image into engagement-tagged clones |
| `engagement connect ID` | Open the manifest's `links` and hold them until Ctrl-C (both clones must be running) |
| `engagement teardown ID [--yes] [--no-collect]` | Collect evidence from running clones, then remove exactly the clones tagged to that engagement (nothing else) |
| `exec NAME -- CMD` | Run a command in a clone; for an engagement clone, the command and its output are recorded as evidence |
| `evidence collect\|list\|verify ID` | Pull each clone's `~/evidence` folder; show the journal; recompute the hash chain |
| `vault seal ID [--out DIR]` | Verify the chain, then write a signed, read-only bundle ([below](#sealing-a-vault)) |
| `vault verify DIR [--pub KEY]` | Check a vault's signature and every hash, offline, on any machine |

A typical run of the Juice Shop lab:

```sh
./rhubarbtart engagement define juiceshop-lab
./rhubarbtart engagement provision juiceshop-lab      # jsl-attacker (Kali) and jsl-target (Juice Shop)
./rhubarbtart run jsl-target --detach && ./rhubarbtart run jsl-attacker --detach
./rhubarbtart engagement connect juiceshop-lab        # in its own terminal; Ctrl-C closes the link
./rhubarbtart exec jsl-attacker -- curl -s http://127.0.0.1:3000/   # recorded as evidence
./rhubarbtart evidence collect juiceshop-lab
./rhubarbtart vault seal juiceshop-lab
./rhubarbtart engagement teardown juiceshop-lab
```

## The manifest

```json
{
  "id": "juiceshop-lab",
  "label": "Juice Shop lab: Kali attacker against an OWASP Juice Shop target",
  "operator": "errantpacket",
  "authorization": "internal lab; the target is an intentionally vulnerable app we build and own",
  "ranges": [
    { "profile": "kali-research", "count": 1, "prefix": "jsl-attacker" },
    { "profile": "juiceshop-target", "count": 1, "prefix": "jsl-target" }
  ],
  "links": [ { "from": "jsl-attacker", "to": "jsl-target", "ports": [3000] } ]
}
```

| Key | Rule |
|---|---|
| `id` | Equals the filename |
| `label`, `operator`, `authorization` | Required, non-empty text. Without an `authorization`, nothing is provisioned |
| `ranges` | At least one. Each has a `profile` (one in `profiles/` that can be built) and a `count` of 1 or more |
| `ranges[].prefix` | Optional clone-name stem: lowercase letters, digits and dashes, up to 40, not starting with `rbt-`. The default is `<engagement>-<profile>`. A range of one clone uses the stem as its name; a larger range adds `-1`, `-2` and so on. Two ranges can't share a stem |
| `links` | Optional; see [Links between clones](#links-between-clones) |
| `targets` | Optional lists of `hosts`, `cidrs`, `domains` and `urls` |
| `agent_budget` | Optional `agents`, `wall_clock_minutes`, `max_spend_usd` and `kill_time` |
| `evidence` | Optional `vault` and `retention_days` |

`targets`, `agent_budget` and `evidence` are checked for shape only; nothing enforces them yet.
A sealed vault records the `label`, `operator` and `authorization`. Unknown keys anywhere are
rejected. Engagement clones are ordinary clones, so `run`, `ssh`, `list`
and `stop` work on them too.

## Evidence

Each engagement keeps a record on your Mac, under
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
  (see [Approvals](agents.md#approvals-for-tiered-commands)).
- **Lifecycle.** Provision (with the image each clone came from), connect and disconnect,
  collect, `herdr arm`, seal and teardown are recorded too.

Every entry is one line of `journal.jsonl` and includes the hash of the entry before it, and
outputs and files are stored by their sha256. `evidence verify` recomputes all of it and reports
an edited, reordered or deleted entry or an altered file.

## Sealing a vault

`rhubarbtart vault seal ID` turns the evidence store into a signed, sealed,
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

## Links between clones

Clones can't reach each other: Tart's default network drops
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

### Why not Softnet?

Tart's `--net-softnet` gives each VM its own network and an egress policy. In September 2026,
though, its release binary is unsigned and not notarized, and it must run as root (SUID or
passwordless sudo). It also moves each VM onto a random subnet, which breaks the images' pinned
SSH source address (`from="192.168.64.1"`). The evaluation is in [#30](https://github.com/errantpacket/RhubarbTart/issues/30). When
Tart ships its native host-only network (no root helper), lab targets can move onto it.
