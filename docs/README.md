# 🍎 RhubarbTart

RhubarbTart builds security-research VMs for Apple silicon from verified vendor installers. You
describe a guest in a short JSON profile: an OS (macOS 26 or 27, NixOS or Kali) and the tools you
need. RhubarbTart downloads the installers, checks each one against a pinned hash and, where one
exists, the vendor's signature, and builds a hardened [Tart](https://tart.run) VM image. A
temporary copy of the image is tested from outside the VM before the image gets its final name.

You work in disposable clones of the image, each with its own password. In an engagement, the
commands you run and the files you collect are kept as evidence on your Mac, and you can seal
that evidence into a signed vault to hand over.

The source, issues and releases are on
[GitHub](https://github.com/errantpacket/RhubarbTart).

## Start here

1. [Quick start](quickstart.md): build a Kali image and start your first clone.
2. [Key concepts](concepts.md): the terms the other pages use.
3. [Using your VMs](using.md): the everyday commands and the dashboard.

## Get started

| Page | Covers |
|---|---|
| [Quick start](quickstart.md) | Requirements, the four steps from a fresh checkout to a running clone, and what each step does |
| [Key concepts](concepts.md) | Profile, input, lock, image, clone, seal, provenance, engagement, evidence, vault, stacked clone |
| [How it works](how-it-works.md) | The four build stages (Define, Resolve, Build, Prove) from start to finish |

## Guides

| Page | Covers |
|---|---|
| [Guests and profiles](profiles.md) | The included guests, how to choose one, and how to write your own profile |
| [Using your VMs](using.md) | The `rhubarbtart` CLI and dashboard, your SSH key, VPN sign-in, and where clone records live |
| [Engagements and evidence](engagements.md) | Scope manifests, evidence, sealed vaults, and links between clones |
| [Agents with herdr](agents.md) | The control-plane service, the `rbt-range` client, arming agents, and approvals |
| [Publishing and stacked clones](publishing.md) | Signing and publishing images to a registry, and making stacked macOS clones from them |

## Topics

| Page | Covers |
|---|---|
| [Testing macOS and iOS apps](testing-apple-apps.md) | The challenges of testing Apple software and how the tool addresses them |
| [macOS guests and containers](macos-and-containers.md) | Why macOS runs as a VM, what OCI packaging and clones give you, and containers inside guests |
| [Trust model and security posture](trust-model.md) | How each input is verified, which security settings each OS applies and tests, and the host-side boundaries |

## Reference

| Page | Covers |
|---|---|
| [Reference](reference.md) | Keeping inputs fresh, configuration variables, host requirements, the toolchain and the repository map |
| [Troubleshooting](troubleshooting.md) | Build and clone error messages, their causes and fixes |
| [Development](development.md) | Changing the code safely: `check.sh`, testing without a Mac, the Claude Code skills |

The design records behind the project (the plan, the interface and engagement plans, and the
herdr charter) are kept in the repository's
[`docs/` folder](https://github.com/errantpacket/RhubarbTart/tree/main/docs), not on this site.
They record plans and decisions; these pages describe what is built.
