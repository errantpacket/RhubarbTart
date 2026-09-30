<div align="center">

# 🍎 RhubarbTart

**Security-research VMs for Apple silicon, built from verified vendor installers.**
<br/>Describe a guest in a short JSON profile: an OS (macOS 26 or 27, NixOS or Kali) and the tools
you need. RhubarbTart downloads the vendor installers, checks each one against a pinned hash and,
where there is one, the vendor's signature, and builds a hardened [Tart](https://tart.run) VM image from them. Before
the image is used, a temporary copy of it is tested from outside the VM. You then work in
disposable clones of the image, each with its own password, and delete them when you're done.
<br/><br/>It's meant for security work where you need to know what was on the machine. When testing
macOS or iOS apps and their backends, you can state what the test machine contained and repeat a
test from the same starting point. When analysing malware, you run the sample in a clone and delete
it afterwards, so the next analysis starts clean and anything that wasn't in the image points to the sample. For
client work, each engagement gets its own clones, so one client's data doesn't carry over to the
next. The same setup is intended as the base for running AI agents on scoped research, penetration
tests and CTFs (planned).

![Apple silicon](https://img.shields.io/badge/host-Apple%20silicon-c9184a)
![Guests](https://img.shields.io/badge/guests-macOS%2026%20·%20macOS%2027%20·%20NixOS%20·%20Kali-c9184a)
![Toolchain](https://img.shields.io/badge/toolchain-no%20Homebrew-2b2d42)
![Provenance](https://img.shields.io/badge/inputs-pinned%20%2B%20verified-2b2d42)

</div>

---

## Why RhubarbTart

Research VMs are often built from a snapshot someone else made, then changed over time. After a
while it's hard to say what software the machine contained, to reproduce a result on the same
setup, or to be sure nothing from one job carried over into the next.

RhubarbTart is built to answer those questions:

| | |
|---|---|
| **Verified inputs** | Each guest starts from the OS vendor's own installer. Every download is pinned by its hash, checked against the vendor's signature where one exists, and checked again inside the guest before it's installed |
| **Tested before use** | A new image only gets its final name after a temporary copy of it passes a set of security checks run from outside the VM |
| **Hardened defaults** | No default passwords, no automatic login, no password-free `sudo` for users ([one narrow Kali exception](docs/trust-model.md)), SSH by key only or not at all, firewall on, and no machine or VPN identity shared between copies |
| **Secrets on the host** | Passwords are stored in your macOS keychain. Every copy gets its own password, and VPN sign-in happens per copy, never inside the image |
| **Configured in JSON** | A guest is defined by a short profile: an OS, a list of tools and a few options |

## How it fits together

There are four main parts:

> **Profile** → **Lock** → **Image** → **Clone**

1. A **profile** describes the guest you want: an OS and a list of tools.
2. **Resolving** the profile produces a **lock** file. It lists the exact version, download URL
   and hash of every **input** (each installer and package). You review the lock and commit it,
   like code.
3. **Building** installs everything the lock lists into a hardened **image**. The image is tested
   from the outside, then kept as a template and never started.
4. You **work in a clone** of the image: a disposable copy with its own password. When you're
   done, delete the clone. The next clone starts from the same state.

An image's name is derived from its inputs (`rbt-<profile>-<hash>`), so the same lock always gives
the same image name. A record of what went into each image is saved next to it.

> [!TIP]
> New to the terms? **[Key concepts](docs/concepts.md)** explains each one, including the build
> steps (vanilla VM, seal, smoke test, provenance) and optional features such as **stacked
> clones**.

## What it's for

Each use follows the same steps: build a hardened guest with known contents, work in a
**disposable clone**, and delete the clone afterwards.

<details>
<summary><b>macOS, iOS and Apple-app penetration testing</b></summary>

Testing a macOS app, an iOS app and its backend, or an Apple service needs a clean Apple
environment that you can set up the same way each time. RhubarbTart builds a hardened **macOS**
guest with your tools (Chrome, ZAP, a VPN or ZTNA client) pinned and verified. Each image's
contents are recorded and each clone is separate and disposable, so you can state what the
machine contained, repeat a test from the same starting point, and keep one client's data out of
the next engagement.

The macOS guest is a workstation for Apple work, such as simulators, intercepting proxies and
analysis tools. It isn't an iOS VM: Tart runs macOS and Linux guests.

Why Apple app testing is hard, and what each part of the tool does about it:
[Testing macOS and iOS apps](docs/testing-apple-apps.md).

</details>

<details>
<summary><b>Disposable machines for research and malware analysis</b></summary>

Running a malware sample or examining something hostile calls for a machine you can trust before
the run and throw away after it. Clone the image, do the work in the clone, then delete it. The
image itself is never started, so it can't be infected, and the next analysis starts from the same
clean state. Because the image's contents are recorded, anything you find that wasn't in the
image came from the sample, not from leftover tools.

NixOS and Kali give you a fully pinned Linux machine for analysis. macOS guests let you study
Mac malware on the platform it targets. Clones can't reach each other. An engagement can open an
explicit link between two of them, and a lab target like Juice Shop has no route out
([Engagements](docs/using.md#engagements)).

</details>

<details>
<summary><b>Where the project is going: agent-driven engagements</b></summary>

The longer-term goal is to run AI agents through a management interface (herdr) for scoped
research, penetration tests and CTFs inside these VMs, with evidence saved to a separate store
outside them. A verified, disposable guest is the building block for that. You can already
create and remove a whole set of clones for one scope as an
[engagement](docs/using.md#engagements), capture what happens inside as signed, sealed evidence,
and drive it toward the herdr interface whose boundary is set in the
[herdr charter](docs/HERDR-CHARTER.md). See [`docs/PLAN.md`](docs/PLAN.md) for the plan.

</details>

## Quick start

On an Apple silicon Mac running macOS 26 or later:

```sh
# 1. Install the pinned tools into ./.toolchain (no Homebrew, no sudo)
./tools/bootstrap.sh && source scripts/env.sh && uv run tools/resolve.py preflight

# 2. Resolve a guest's inputs into its lock file, then review the changes
uv run tools/resolve.py resolve kali-research && git diff locks/

# 3. Build and test the image (the SSH key is optional; without one, SSH is disabled)
ssh-add ~/.ssh/id_ed25519
RHUBARB_SSH_PUBKEYS=~/.ssh/id_ed25519.pub ./scripts/build.sh kali-research

# 4. Make a clone and start it; don't use the image directly
./rhubarb new web-1 --profile kali-research && ./rhubarb run web-1
```

<details>
<summary><b>What each step does</b></summary>

1. **Bootstrap** downloads Tart, Packer and its Tart plugin, uv, zot and cosign, and builds GnuPG
   from source. Each is checked against a pinned hash and installed into the repository's own
   `.toolchain/` folder. `preflight` then confirms that these are the versions on your `PATH`.
   Building GnuPG needs the Xcode Command Line Tools.
2. **Resolve** looks up the newest versions of the profile's inputs, verifies them, and writes
   `locks/kali-research.lock.json`. The four included profiles already have committed locks, so
   you only need this step when you want newer versions.
3. **Build** installs the OS from the vendor's installer, adds the tools, and **seals** the guest:
   it applies the hardening and checks that each setting took effect. It then starts a temporary
   copy and tests it from the outside. Only if those tests pass does the image get its final name.
   A build is a full OS install and can take 15 to 45 minutes, so run it in a separate terminal.
   `./scripts/build.sh --list` lists the available profiles.
4. **`rhubarb new`** creates a clone and sets its own password, which is stored in your keychain.
   `rhubarb run` starts it. [Using your VMs](docs/using.md) covers `ssh`, `enroll`, `reset`, `rm`
   and the `./rhubarb-tui` dashboard.

</details>

## Choose a guest

| Profile | OS | Tools | Suited to |
|---|---|---|---|
| `tahoe-research` | macOS 26 | Chrome, ZAP, WARP, Tailscale | macOS app and client testing on any macOS 26 or newer host |
| `goldengate-research` | macOS 27 | Chrome, ZAP, WARP, Tailscale | The current macOS release; **needs a macOS 27 host** |
| `nixos-research` | NixOS 26.05 | Chrome, ZAP, WARP, Tailscale · XFCE · Rosetta | The most reproducible option: the whole OS is pinned to a single commit |
| `kali-research` | Kali rolling | Chrome, ZAP, WARP, Tailscale · `kali-linux-default` · XFCE · Rosetta | Kali's standard set of security tools |

All guests are arm64, because Tart runs native guests on Apple silicon. Linux profiles with
Rosetta enabled can still run x86_64 Linux programs. If none of these fit, you can
[define your own guest](docs/profiles.md).

<details>
<summary><b>Which one should I pick?</b></summary>

```mermaid
%%{init: {'theme':'base','fontFamily':'ui-sans-serif, system-ui, -apple-system, Helvetica, Arial, sans-serif','themeVariables':{'primaryColor':'#ffffff','primaryTextColor':'#2b2d42','primaryBorderColor':'#c9184a','lineColor':'#8d99ae','edgeLabelBackground':'#ffffff','fontSize':'13px'},'flowchart':{'curve':'basis','nodeSpacing':45,'rankSpacing':55,'padding':8,'useMaxWidth':true}}}%%
flowchart TD
    Q{"What are you<br/>testing?"}
    Q -->|"macOS apps<br/>&amp; clients"| H{"Host on<br/>macOS 27?"}
    H -->|"yes"| GG["<b>goldengate-research</b><br/><i>macOS 27</i>"]
    H -->|"no"| TH["<b>tahoe-research</b><br/><i>macOS 26</i>"]
    Q -->|"Linux<br/>tooling"| P{"What matters<br/>most?"}
    P -->|"Reproducibility<br/>&amp; audit"| NX["<b>nixos-research</b><br/><i>NixOS 26.05</i>"]
    P -->|"Largest<br/>toolset"| KL["<b>kali-research</b><br/><i>Kali rolling</i>"]
    Q -->|"Something<br/>else"| OWN(["Define your<br/>own profile"])

    classDef q fill:#fff0f3,stroke:#c9184a,stroke-width:1.5px,color:#2b2d42
    classDef guest fill:#c9184a,stroke:#800f2f,stroke-width:1.5px,color:#ffffff
    classDef own fill:#2b2d42,stroke:#2b2d42,color:#ffffff
    class Q,H,P q
    class GG,TH,NX,KL guest
    class OWN own
```

</details>

## Documentation

| Page | Covers |
|---|---|
| **[Key concepts](docs/concepts.md)** | The terms used throughout: profile, input, lock, image, clone, seal, provenance, stacked clone |
| **[How it works](docs/how-it-works.md)** | The four build stages (Define, Resolve, Build, Prove) from start to finish |
| **[Using your VMs](docs/using.md)** | Working with clones: the `rhubarb` CLI and dashboard, SSH, VPN sign-in, engagements |
| **[Define your own guest](docs/profiles.md)** | Writing a profile: the format, which tools each OS supports, validation rules |
| **[Trust model and security posture](docs/trust-model.md)** | How each input is verified, and which security settings each OS applies and tests |
| **[Publishing and stacked clones](docs/publishing.md)** | Signing and publishing images to a registry, and making stacked macOS clones from them |
| **[macOS guests and containers](docs/macos-and-containers.md)** | Why macOS runs as a VM (not a container), what OCI packaging and clones give you, and running containers or devcontainers inside guests |
| **[Testing macOS and iOS apps](docs/testing-apple-apps.md)** | The challenges of testing Apple software and how each part of the tool addresses them |
| **[Reference](docs/reference.md)** | Updating inputs, configuration variables, host requirements and the toolchain |
| **[Development](docs/development.md)** | Changing the code safely: `check.sh`, testing without a Mac, the Claude Code skills |

Roadmap: [`docs/PLAN.md`](docs/PLAN.md). Contributing: [`CONTRIBUTING.md`](CONTRIBUTING.md).
Reporting security issues: [`SECURITY.md`](SECURITY.md).

## Open issues

Open work is tracked in the [issue tracker](https://github.com/errantpacket/RhubarbTart/issues):

| Type | Issue |
|---|---|
| Bug | [#63](https://github.com/errantpacket/RhubarbTart/issues/63) macOS 26 builds: the automated Setup Assistant step sometimes fails and the build times out; running it again usually works |
| Enhancement | [#74](https://github.com/errantpacket/RhubarbTart/issues/74) Publishing to remote registries that require a login |
| On hold | [#28](https://github.com/errantpacket/RhubarbTart/issues/28) A standard (non-admin) account for daily use; the design options are in the issue |
| Roadmap | [#33](https://github.com/errantpacket/RhubarbTart/issues/33) The `herdr` agent management service |
| Roadmap | [#37](https://github.com/errantpacket/RhubarbTart/issues/37) A published documentation site |

## License

RhubarbTart is licensed under the [Functional Source License 1.1 (Apache-2.0 future
license)](LICENSE.md) (`FSL-1.1-ALv2`): free for personal, internal, educational and research use;
the only restriction is using it to build a competing commercial product, and the license converts
to Apache-2.0 two years after each release. Third-party components keep their own licenses, listed
in [`THIRD-PARTY-NOTICES.md`](THIRD-PARTY-NOTICES.md).

---

<div align="center">
<sub>Setup Assistant automation adapted from <a href="https://github.com/cirruslabs/macos-image-templates">cirruslabs/macos-image-templates</a> ·
VMs run on <a href="https://github.com/openai/tart">Tart</a> · built with Packer, uv and Nix.</sub>
</div>
