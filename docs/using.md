# Using your VMs

Built images are templates: you work in disposable **clones** of them (see
[Key concepts](concepts.md#what-you-work-in)). The `rhubarbtart` CLI manages clones, and the
`./rhubarbtart-tui` dashboard drives the same actions over the same core. Scoped sets of clones
and their evidence are covered in [Engagements and evidence](engagements.md); stacked macOS clones
from a registry in [Publishing and stacked clones](publishing.md).

## Everyday commands

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
| `new NAME --profile P` / `--image IMG` | Clones a **verified** image (never `-unverified`), records its lineage, then boots it headless and rotates it to a **unique random password** (keychain account = clone name), proving through `sudo` that the old one is rejected. `--no-rotate` keeps the image's password. `--from-registry` (macOS only): verify the image's published copy by digest (signature and provenance) and stack the clone on it, tens of MB instead of a full copy |
| `run NAME [--headless] [--detach]` | Starts the clone with the right flags for its profile (`--rosetta=rosetta` for Linux Rosetta profiles). It keeps the terminal until the VM shuts down; `--detach` returns it |
| `stop NAME` | Stops a running clone (the VM and its records stay) |
| `ssh NAME [-- CMD]` | Connects as the profile's user, host key pinned per clone name |
| `exec NAME -- CMD` | Runs one command over SSH and returns its output and exit code. In an engagement clone, the command and its output are recorded as evidence |
| `enroll NAME tailscale\|warp [--org TEAM]` | Runtime VPN/ZTNA enrollment (see [below](#vpn-and-ztna-enrollment)) |
| `images` | Lists built images and which one is current for each profile |
| `list` | Lists clones with their state, password mode and enrollments. A clone's image can show as `outdated` (the profile's lock changed), `image-deleted`, or, for a stacked clone, `base-missing` (the pulled base is gone) |
| `reset NAME [--same-image] [--no-rotate]` | Throws the clone away (identity, enrollment and all) and re-clones, from the current image by default. A stacked clone stays stacked |
| `rm NAME [--yes]` | Stops and deletes the clone, its keychain entry (only if it had a unique password) and its pinned host key. A stacked clone releases its base when no other clone uses it |

> **Note:** `rhubarbtart` only touches clones it created. It never modifies built images (`rbt-…`) or VMs
> made some other way, and clone names can't start with `rbt-`. Per-clone rotation needs key
> SSH (images built with `RHUBARB_SSH_PUBKEYS`, with the key in `ssh-agent` or `RHUBARB_SSH_IDENTITY`
> pointing at it). Without it the clone keeps the image's password, and `rhubarbtart list` says
> `inherited`. For an image built with SSH disabled, `new` skips the rotation without booting the
> clone and prints a hint to rebuild with `RHUBARB_SSH_PUBKEYS`.

To read a clone's password: `security find-generic-password -s RhubarbTart -a NAME -w` (use the
image name instead of `NAME` when `list` says `inherited`).

## The dashboard

`./rhubarbtart-tui` shows images, clones, provenance and logs, and runs `run`, `ssh`, `enroll`,
`reset`, `rm`, `new` and builds, with a confirmation before anything destructive.

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

## Your SSH key

`rhubarbtart` reaches clones over SSH with a key, never a password. The public key goes into the
image when you build it (`RHUBARB_SSH_PUBKEYS`). The private key stays on your Mac.

- **Use a key only for your VMs.** Don't reuse the key you use for GitHub or servers. If a VM key
  leaks, nothing else is exposed, and you can replace it on its own.
- **Create it with a passphrase.** This command asks for one:

  ```sh
  ssh-keygen -t ed25519 -a 100 -C rhubarbtart -f ~/.ssh/rhubarbtart_ed25519
  ```

  `ed25519` keys are short and fast, and the build accepts them. `-a 100` makes a passphrase much
  slower to guess if someone copies the private key file.
- **Keep the passphrase in your keychain.** `ssh-add --apple-use-keychain ~/.ssh/rhubarbtart_ed25519`
  loads the key into `ssh-agent` and stores the passphrase in your macOS keychain. After a
  restart, `ssh-add --apple-load-keychain` loads it again without asking. The build's smoke test
  and `rhubarbtart` use the key from the agent.
- **Use one key file instead of the agent** by setting `RHUBARB_SSH_IDENTITY` to the private key's
  path. `rhubarbtart` then uses only that key. The build's smoke test still uses the agent.
- **Authorize several keys** by listing their public keys in one file, one per line, and passing
  that file as `RHUBARB_SSH_PUBKEYS`. The build accepts `ed25519` and `ecdsa` keys and refuses RSA.
- **Hardware security keys** (`ed25519-sk`, for example a YubiKey) are accepted by the build, but
  macOS's built-in `ssh` can't use them without a separate FIDO2 provider. A passphrase-protected
  key in the keychain is the simpler choice.
- **Changing the key** means rebuilding: each image holds the public keys it was built with. Build
  again with the new `RHUBARB_SSH_PUBKEYS`, then `rhubarbtart reset NAME` moves a clone to the new
  image.

## VPN and ZTNA enrollment

Images never contain VPN identity. Each clone is enrolled at runtime. The secrets never touch
the repo, a profile, the image or a command line: they go from your keychain to the clone over
SSH stdin, into a 0600 temporary file that is shredded once read.

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


| Service | Command | Secret (keychain service `RhubarbTart-enroll`) | Left in the clone | Notes |
|---|---|---|---|---|
| Tailscale | `rhubarbtart enroll web-1 tailscale` | account `tailscale-authkey` | Linux: nothing. macOS: the key stays in Tailscale's managed preferences until you remove it (below) | Use a one-off, pre-approved, *tagged* key (ephemeral for throwaway clones). macOS: approve the system extension once per clone |
| Cloudflare WARP | `rhubarbtart enroll web-1 warp --org TEAM` | `warp-client-id`, `warp-client-secret` | The service token, in a root-only (0600) `mdm.xml` the WARP client reads | A service token allowed to enroll devices; dashboard version pushes are disabled |

Store a secret once with `security add-generic-password -s RhubarbTart-enroll -a tailscale-authkey -w`
(it prompts, so the secret never lands in your shell history). `rhubarbtart enroll` wraps
`scripts/enroll.sh` and records which services each clone is enrolled in.

On a macOS clone, once Tailscale has connected, remove the key from its preferences:
`sudo defaults delete /Library/Preferences/io.tailscale.ipn.macsys AuthKey` (the enroll output
repeats this). Because a clone's enrollment dies with it, prefer one-off and ephemeral keys, and
`reset` or `rm` a clone when the work is done.

## Clone records

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
