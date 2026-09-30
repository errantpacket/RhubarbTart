# macOS guests and containers

Two questions come up often: can a macOS guest be "containerized", and can a guest run containers
or devcontainers. The short answers are no and yes, and the reasons are worth knowing because they
follow from how Apple silicon virtualization works, not from a RhubarbTart choice.

| Question | Answer |
|---|---|
| Run a macOS guest as an OS-level container (shared kernel, `docker run`) | **No.** macOS runs only as a full VM. |
| Package and distribute a macOS guest like a container image | **Yes.** Images are OCI artifacts, signed and pushed to a registry. |
| Spin up cheap, throwaway copies of a guest | **Yes.** Clones are copy-on-write and disposable. |
| Run Linux containers or devcontainers **inside** a Linux guest | **Yes**, on any Apple silicon. |
| Run containers **inside** a macOS guest | Only on an **M3 or newer** host (needs nested virtualization). |

## Why a macOS guest is a VM, not a container

A container shares the host's kernel and isolates a process tree. macOS has no such mode: Apple
runs macOS only as a full virtual machine, through
[Virtualization.framework](https://developer.apple.com/documentation/virtualization), on Apple
hardware. Tart (which RhubarbTart builds on) uses that framework, so every macOS guest is a
complete OS with its own kernel, booted as a VM.

There is no macOS container runtime, and there is no shared-kernel "macOS container". Tools that
claim to "run macOS in a container" run a QEMU VM inside a Linux container on non-Apple hardware,
which is a different thing and is outside Apple's licence for macOS. Apple's own
`container` tool (introduced with macOS 26) runs **Linux** containers on a Mac; it does not make
macOS itself a container.

So when people ask to "containerize" a macOS guest, the useful parts of what they want, a small
distributable artifact and cheap throwaway instances, are things RhubarbTart already provides in
the two sections below. The one thing that is not possible is a shared-kernel macOS container.

## What "containerized" does mean here

### Images are OCI artifacts (packaged and signed like container images)

Tart stores a VM image as an [OCI](https://opencontainers.org/) artifact, the same object format a
container registry holds, and can push and pull it to and from any OCI registry. A macOS image is
therefore distributed exactly like a container image: a content-addressed artifact you can host,
mirror, and pull by name. It is booted as a VM rather than `docker run`, but the packaging and
supply chain are the container-registry ones.

RhubarbTart wraps this so the supply chain is verifiable:

- `scripts/publish.sh publish` pushes a built image to a registry (a local
  [zot](https://zotregistry.dev/) by default) and signs it with [cosign](https://docs.sigstore.dev/).
- `scripts/publish.sh verify` (and `rhubarb new --from-registry`) check that signature before use.

See [Publishing and stacked clones](publishing.md) for the full flow.

### Clones are cheap, disposable instances

You never boot the sealed image. `rhubarb new` makes an **APFS clone**: a copy-on-write copy that
is near-instant and takes almost no extra disk until it is written to. You work in the clone and
delete it when done, and `rhubarb reset` swaps in a fresh one from the image.

This is the container-like part of the experience, fast to create, isolated, and throwaway, applied
to full VMs. It is how RhubarbTart keeps the "spin up, use, discard" model for macOS guests without
any container runtime. See [Key concepts](concepts.md) (clone) and [Using your VMs](using.md).

## Running containers inside a guest

A different question: not containerizing the guest, but running containers (including
[devcontainers](https://containers.dev/)) inside one. This depends entirely on **where the
container's kernel comes from**.

### Linux guests: native, on any Apple silicon

A Linux container needs a Linux kernel, and a Linux guest already is one. Install a runtime
(`docker`, `podman`) in a Linux profile and containers run directly in that guest's kernel, with no
extra virtualization. This works on any Apple silicon Mac, including M1 and M2.

This is the natural home for devcontainers in RhubarbTart: the container runs inside a hardened,
provenance-pinned, disposable Linux clone. To add it, put the runtime in a Linux profile's package
list and rebuild (see [Define your own guest](profiles.md)).

### macOS guests: only with nested virtualization (M3 or newer)

A macOS guest has an XNU kernel, not a Linux one, so any Linux container engine on macOS (Docker
Desktop, Colima, or Apple's `container` tool) first starts a lightweight **Linux VM** to hold the
containers. Starting a VM inside a VM is **nested virtualization**, which Apple silicon supports
only on **M3 and newer** chips (and the host must expose it).

Consequences:

- On an **M1 or M2** host, containers cannot run inside a macOS guest at all, because nested
  virtualization is unavailable. Check with `sysctl hw.optional.arm.FEAT_NV` (absent or `0` means
  no nested virtualization).
- On an **M3 or newer** host it is possible. Tart exposes the switch as `tart run --nested`
  ("enable nested virtualization if possible"). Two things are still needed:
  1. `rhubarb run` does not pass `--nested` today, so a profile option to request it would have to
     be added and recorded in the clone's lineage.
  2. A container runtime must be present in the macOS image. Apple's native `container` tool
     (macOS 26+) is the lightweight, licence-free choice over Docker Desktop.

<details>
<summary><b>Rule of thumb</b></summary>

- Want **devcontainers today**, on any Mac: use a **Linux guest** with `docker`/`podman`.
- Want containers **inside macOS**: you need an **M3+ host**, `--nested` wired into the profile, and
  a runtime in the image.
- Want to **distribute** a guest like a container image, or spin up **throwaway copies**: that is
  already how RhubarbTart works (OCI registry + signing, and APFS clones), for macOS and Linux
  alike.

</details>
