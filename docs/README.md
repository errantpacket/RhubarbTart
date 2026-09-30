# Documentation

Guides and reference pages describe RhubarbTart as it works today. Design records, the files with
uppercase names, keep the plans and decisions behind it; each notes where the build differs from
the plan.

## Guides

| Page | Covers |
|---|---|
| [Key concepts](concepts.md) | The terms used throughout: profile, input, lock, image, clone, seal, provenance, engagement, evidence, vault, stacked clone |
| [How it works](how-it-works.md) | The four build stages (Define, Resolve, Build, Prove) from start to finish |
| [Using your VMs](using.md) | The `rhubarb` CLI and dashboard, SSH, VPN sign-in, engagements, evidence and vaults, the control-plane service and herdr agents |
| [Define your own guest](profiles.md) | Writing a profile: the format, which tools each OS supports, validation rules |
| [Publishing and stacked clones](publishing.md) | Signing and publishing images to a registry, and making stacked macOS clones from them |
| [macOS guests and containers](macos-and-containers.md) | Why macOS runs as a VM, what OCI packaging and clones give you, and containers inside guests |
| [Testing macOS and iOS apps](testing-apple-apps.md) | The challenges of testing Apple software and how the tool addresses them |

## Reference

| Page | Covers |
|---|---|
| [Trust model and security posture](trust-model.md) | How each input is verified, which security settings each OS applies and tests, and the host-side boundaries |
| [Reference](reference.md) | Updating inputs, configuration variables, host requirements, the toolchain and the repository map |
| [Development](development.md) | Changing the code safely: `check.sh`, testing without a Mac, the Claude Code skills |

## Design records

| Record | Covers |
|---|---|
| [PLAN.md](PLAN.md) | The agent-driven research range: goals, architecture, threat model and roadmap |
| [INTERFACE-PLAN.md](INTERFACE-PLAN.md) | The typed core API and the TUI (Phase 0.5) |
| [ENGAGEMENT-PLAN.md](ENGAGEMENT-PLAN.md) | The engagement object and scope manifest (Phase 1) |
| [HERDR-CHARTER.md](HERDR-CHARTER.md) | What the herdr agent interface may and may not do, and where its trust boundary lies |

`images/` holds screenshots used in the top-level README.
