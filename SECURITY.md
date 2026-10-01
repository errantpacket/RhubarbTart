# Security Policy

RhubarbTart builds and manages **hardened, provenance-verified VMs** for **authorized** security
work. Security is the product, so a flaw in the build/verify/harden pipeline, the provenance
chain, or how secrets and clone state are handled is a serious bug. Reports are welcome.

## Reporting a vulnerability

**Please do not open a public issue for a security problem.**

- Preferred: GitHub's **private vulnerability reporting**: the **Security** tab → **Report a
  vulnerability** (GitHub → Security Advisories). This keeps the report private to the maintainers.
- If that isn't available to you, email **security@errantpacket.com**.

Please include:

- What the issue is and which component (a `tools/` module, a Packer template, a `guest/` script,
  the `rhubarb` CLI, the provenance/verification logic, secret handling, or the network model).
- How to reproduce it, and what an attacker gains.
- The commit or version you're looking at, and your host OS.

We aim to acknowledge a report quickly and to fix confirmed issues before any disclosure. This is
an early-stage project maintained on a best-effort basis; there is no paid bounty.

## What's in scope

The things this project promises, and therefore wants to hear about if they break:

- **Provenance bypass:** a way to get an unpinned, unverified, or tampered input (OS image,
  package, or host tool) into a build without the hash/signature checks catching it, on the host
  or inside the guest.
- **Verification weaknesses:** flaws in the GPG, apt-repo, Tailscale `distsign`/Ed25519, NAR-hash,
  or macOS codesign/notarization checks (`tools/rhubarb/`).
- **Hardening escapes:** a built image that ships with a default/weak password, auto-login,
  passwordless sudo, password SSH, a disabled firewall, or shared identity, without the seal step
  or `scripts/smoke-test.sh` catching it.
- **Secret leaks:** a path where a VM password, VPN token, or enrollment secret reaches an image,
  a log, a committed file, `argv`, or another clone. Secrets should only move keychain →
  subprocess stdin → shred.
- **Clone-record integrity:** a way to make the `rhubarb` CLI act on the wrong VM or keychain
  entry via a crafted record (see the record model in `tools/rhubarb/clones.py`).
- **Isolation gaps:** a clone reaching another clone without a declared engagement link, or a lab
  target reaching the internet or the host (see
  [Host-side boundaries](docs/trust-model.md#host-side-boundaries)).
- **Evidence and vault integrity:** a way to change, drop or reorder evidence journal entries, or
  alter a sealed vault, without `rhubarb evidence verify` or `rhubarb vault verify` noticing.
- **Control-plane and agent boundaries:** a way to reach the control-plane socket without file
  permission to it, to make `rbt-range` act on a clone other than its assigned one, or to run a
  tiered command without a single-use approval.

The [Trust model](docs/trust-model.md#trust-model) and [Security posture](docs/trust-model.md#security-posture)
pages, and the threat model in [`docs/PLAN.md`](docs/PLAN.md), describe the intended guarantees.

## Out of scope

- **Misuse by an authorized operator.** This is a tool for permitted testing; a trusted operator
  pointing it at systems they're allowed to test is by design. Using it against systems you are
  **not** authorized to test is misuse, not a vulnerability in this project.
- **Vulnerabilities in the target systems** an operator tests with these VMs.
- **Vulnerabilities in upstream software** (Tart, Packer, the guest OSes, the bundled tools):
  report those to their maintainers. We will, however, update pins and re-verify in response.
- **A fully root-compromised host.** The host is the trust anchor; keep it hardened.

## A note on intended use

RhubarbTart is for authorized security research, scoped penetration testing, and CTFs. Reports,
issues, and contributions should assume that authorized-use context.
