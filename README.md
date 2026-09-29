<div align="center">

# 🍎 RhubarbTart

**Provenance-first, hardened security-research VMs for Apple silicon.**
<br/>Pick a profile, get a sealed [Tart](https://tart.run) guest whose every input is pinned,
verified and proven, then work in disposable clones of it.

![Apple silicon](https://img.shields.io/badge/host-Apple%20silicon-c9184a)
![Guests](https://img.shields.io/badge/guests-macOS%2026%20·%20macOS%2027%20·%20NixOS%20·%20Kali-c9184a)
![Toolchain](https://img.shields.io/badge/toolchain-no%20Homebrew-2b2d42)
![Provenance](https://img.shields.io/badge/inputs-pinned%20%2B%20verified-2b2d42)

</div>

---

## Why RhubarbTart

**A security result is only as trustworthy as the machine it came from.** Research VMs usually
start from someone else's snapshot and drift from there. You can't say what was really on the
box, you can't reproduce a finding a month later, and one job's contamination (or one client's
data) can leak into the next.

RhubarbTart removes that doubt:

| | |
|---|---|
| 🧾 **Known inputs** | Guests start from the OS vendor's own installer. Every download is pinned by hash, checked against the vendor's signature where one exists, and verified twice: on the host, then again inside the guest |
| 🔬 **Proven, not assumed** | An image only gets its final name after a throwaway copy has passed a hardening check from the outside |
| 🧱 **Hardened by default** | No default passwords, no auto-login, no passwordless sudo for users ([one scoped Kali exception](docs/trust-model.md)), key-only SSH or none, firewall on, no shared machine or VPN identity |
| 🔐 **Secrets stay yours** | Passwords live in your macOS keychain, every clone gets its own, and VPN enrollment happens per clone, never baked in |
| 🧩 **Configurable** | A guest is a small JSON profile: OS + tools + options. No code needed |

## How it fits together

Four words carry most of the design:

> **Profile** → **Lock** → **Image** → **Clone**

1. A **profile** says what you want: an OS and a list of tools.
2. **Resolving** it produces a **lock**: the exact version, download URL and hash of every
   **input** (each installer and package). You review the lock like code and commit it.
3. **Building** turns the lock into a hardened **image**. The image is proven from the outside,
   then kept as a template that is never booted.
4. You **work in clones** of the image: disposable copies, each with its own password. Delete a
   clone when you're done; the next one starts identical.

The image's name is derived from its inputs (`rbt-<profile>-<hash>`), so the same lock always
means the same image, and a record of exactly what went into it is kept alongside.

> [!TIP]
> New here? **[Key concepts](docs/concepts.md)** explains every term in plain language, including
> the build steps (vanilla VM, seal, smoke test, provenance) and optional extras like
> **stacked clones**.

## What it's for

The pattern is always the same: build a hardened guest whose contents you can prove, work in a
**throwaway clone**, delete it when done.

<details>
<summary><b>macOS / iOS / Apple-app penetration testing</b></summary>

Assessing a macOS app, an iOS app and its backend, or an Apple-ecosystem service needs a clean
Apple environment you can trust and repeat. RhubarbTart gives you a hardened **macOS** guest with
your tooling (Chrome, ZAP, a VPN/ZTNA agent) pinned and verified: no drift from a colleague's
snapshot, no mystery software. Because each image's provenance is recorded and each clone is
isolated and disposable, findings are **attributable and reproducible**. You can state exactly
what the box contained, re-run from an identical base, and never carry one client's state into
the next engagement.

The macOS guest is the *workbench* for Apple work (simulators, intercepting proxies, static and
dynamic tooling). It isn't an iOS VM; Tart runs macOS and Linux guests.

</details>

<details>
<summary><b>Ephemeral boxes for research and malware analysis</b></summary>

Detonating a sample or probing something hostile needs a box you can trust *before* the run and
discard *after* it. Clone the verified image, do the work in the clone, delete it. The image
itself never boots, so it can't be contaminated, and the next analysis starts from the same
**known-clean** state. Because the provenance is recorded, anything present that *wasn't* in the
image is the sample's doing, not leftover tooling.

NixOS and Kali give a fully pinned Linux analysis box; macOS guests let you study Mac-targeted
samples on the platform they target. Per-clone network isolation is on the roadmap
([#30](https://github.com/errantpacket/RhubarbTart/issues/30)).

</details>

<details>
<summary><b>Where this is heading: agent-driven engagements</b></summary>

The goal is to run agents through a management interface (herdr) for scoped research, pentests
and CTFs inside these VMs, capturing evidence to a secure per-engagement vault outside them. A
verified, disposable guest is the unit that builds on. Today you can already stand up and tear
down a whole scoped set of clones as an [engagement](docs/using.md#engagements). See
[`docs/PLAN.md`](docs/PLAN.md).

</details>

## Quick start

On an Apple silicon Mac running macOS 26 or later:

```sh
# 1. Install the pinned toolchain into ./.toolchain (no Homebrew, no sudo)
./tools/bootstrap.sh && source scripts/env.sh && uv run tools/resolve.py preflight

# 2. Resolve a guest's inputs into its lock file, then review it
uv run tools/resolve.py resolve kali-research && git diff locks/

# 3. Build, harden, seal and prove the image (the SSH key is optional: no key = no SSH)
ssh-add ~/.ssh/id_ed25519
RHUBARB_SSH_PUBKEYS=~/.ssh/id_ed25519.pub ./scripts/build.sh kali-research

# 4. Work in a clone, never in the image itself
./rhubarb new web-1 --profile kali-research && ./rhubarb run web-1
```

<details>
<summary><b>What each step does</b></summary>

1. **Bootstrap** downloads Tart, Packer and its Tart plugin, uv, zot and cosign, and builds GnuPG from source, each
   checked against a pinned hash, into the repo-local `.toolchain/`. `preflight` confirms the
   right versions are the ones on your `PATH`. It needs the Xcode Command Line Tools.
2. **Resolve** finds the newest versions of the profile's inputs, verifies them, and writes
   `locks/kali-research.lock.json`. The four bundled profiles already have committed locks, so
   you can skip this step until you want newer versions.
3. **Build** installs the OS from the vendor's installer, adds the tools, **seals** the guest
   (hardening plus checks that the hardening holds), then smoke-tests a throwaway copy from the
   outside. Only then does the image get its final name. It's a full OS install, so expect tens
   of minutes; run it in a spare terminal. `./scripts/build.sh --list` shows every profile.
4. **`rhubarb new`** makes a disposable clone and gives it its own password (kept in your
   keychain). `rhubarb run` starts it. See [Using your VMs](docs/using.md) for `ssh`, `enroll`,
   `reset`, `rm` and the `./rhubarb-tui` dashboard.

</details>

## Choose a guest

| Profile | OS | Tools | Good for |
|---|---|---|---|
| `tahoe-research` | macOS 26 | Chrome, ZAP, WARP, Tailscale | macOS client and app testing on any macOS 26+ host |
| `goldengate-research` | macOS 27 | Chrome, ZAP, WARP, Tailscale | The latest macOS; **needs a macOS 27 host** |
| `nixos-research` | NixOS 26.05 | Chrome, ZAP, WARP, Tailscale · XFCE · Rosetta | Maximum reproducibility: the whole OS is pinned to one commit |
| `kali-research` | Kali rolling | Chrome, ZAP, WARP, Tailscale · `kali-linux-default` · XFCE · Rosetta | Batteries-included offensive tooling |

Guests are arm64 (Tart runs native guests on Apple silicon). Linux profiles with Rosetta can
still run x86_64 Linux binaries. Want something else? [Define your own guest](docs/profiles.md).

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

| Page | Read it when you want to… |
|---|---|
| **[Key concepts](docs/concepts.md)** | understand the vocabulary: profile, input, lock, image, clone, seal, provenance, stacked clone |
| **[How it works](docs/how-it-works.md)** | see the Define → Resolve → Build → Prove pipeline end to end |
| **[Using your VMs](docs/using.md)** | work with clones: `rhubarb` CLI and TUI, SSH, VPN/ZTNA enrollment, engagements |
| **[Define your own guest](docs/profiles.md)** | write a profile: the format, which tools exist per OS, validation rules |
| **[Trust model and security posture](docs/trust-model.md)** | know exactly what's verified how, and which hardening each OS asserts and proves |
| **[Publishing and stacked clones](docs/publishing.md)** | sign and publish images to a registry, and make stacked macOS clones from them |
| **[Reference](docs/reference.md)** | keep inputs fresh, set configuration variables, check host requirements and the toolchain |
| **[Development](docs/development.md)** | change the code safely: `check.sh`, off-Mac validation, the project's Claude Code skills |

Direction and roadmap: [`docs/PLAN.md`](docs/PLAN.md). Contributing: [`CONTRIBUTING.md`](CONTRIBUTING.md).
Security issues: [`SECURITY.md`](SECURITY.md).

## Open issues

Everything still open is tracked in the [issue tracker](https://github.com/errantpacket/RhubarbTart/issues):

| | Issue |
|---|---|
| 🐞 **Bug** | [#63](https://github.com/errantpacket/RhubarbTart/issues/63) macOS 26 builds: the automated Setup Assistant step occasionally misses and the build times out (a rerun passes) |
| ✨ **Enhancement** | [#30](https://github.com/errantpacket/RhubarbTart/issues/30) Network isolation between clones (`--net-softnet`) |
| ✨ **Enhancement** | [#74](https://github.com/errantpacket/RhubarbTart/issues/74) Publishing to remote registries that require a login |
| ⏸️ **On hold** | [#28](https://github.com/errantpacket/RhubarbTart/issues/28) A standard (non-admin) daily-use account; design options are in the issue |
| 🧭 **Roadmap** | [#33](https://github.com/errantpacket/RhubarbTart/issues/33) The `herdr` agent-platform service · [#37](https://github.com/errantpacket/RhubarbTart/issues/37) A published docs site |
| 📝 **Decision** | [#34](https://github.com/errantpacket/RhubarbTart/issues/34) Choose a license |

---

<div align="center">
<sub>Setup Assistant automation adapted from <a href="https://github.com/cirruslabs/macos-image-templates">cirruslabs/macos-image-templates</a> ·
VMs by <a href="https://github.com/openai/tart">Tart</a> · built with Packer, uv, Nix and a healthy distrust of <code>latest</code>.</sub>
</div>
