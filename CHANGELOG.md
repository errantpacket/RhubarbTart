# Changelog

All notable changes to RhubarbTart. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and versions follow
[Semantic Versioning](https://semver.org/). While the version is 0.x, a minor release may change
anything; the version covers the `rhubarb` CLI, the profile, engagement and lock file formats, and
provenance records, not the internals of `tools/rhubarb/api.py`.

Built images are named from their inputs, not from this version: `rbt-<profile>-<inputs-sha>` is
the same for the same profile and lock whatever release built it. Each provenance record notes the
release that built it (`rhubarbtart_version`).

## [Unreleased]

## [0.1.0] - 2026-09-30

The first versioned release.

### Images
- Guests from vendor installers only: macOS 26 and 27, NixOS and Kali, each defined by a JSON
  profile. Profiles included: `tahoe-research` (macOS 26), `goldengate-research` (macOS 27),
  `nixos-research`, `kali-research`, and the `juiceshop-target` lab target.
- Every input pinned by a tool-written lock and verified twice: on the host before the build and
  again inside the guest. Signed sources need a pinned key file and fingerprint.
- Hardened seal: no default passwords, no auto-login, no password-free sudo for users, SSH by key
  only or off, firewall on, no shared machine or VPN identity, no build residue.
- A smoke test proves the hardening from outside the VM before an image gets its final name. Linux
  images are booted twice, with `/` read-write and fstab mounts checked on both boots.
- Provenance record per image (`out/<vm>.provenance.json`); optional signed publishing to a
  localhost registry and stacked macOS clones.

### Clones and engagements
- `rhubarb` CLI: per-clone passwords in the host keychain, run, ssh, exec, enroll (Tailscale,
  Cloudflare WARP), reset and rm, only ever on clones it created.
- Engagements: scope manifests that provision and tear down a set of clones together. Clones can't
  reach each other except over declared links (`rhubarb engagement connect`); lab targets have no
  egress.
- Evidence kept on the host: a hash-chained journal of commands and collected files, sealed into
  signed, portable vaults (`rhubarb vault seal|verify`).

### Control plane and agents
- Typed core API (`tools/rhubarb/api.py`) shared by every frontend.
- Control-plane service over a 0600 Unix socket (`rhubarb serve`): read endpoints, guarded actions
  and a live evidence stream.
- herdr integration: `rhubarb herdr arm` starts an engagement's agents, each limited to one clone
  through `rbt-range`; commands matching configured patterns wait for a single-use approval.
- `./rhubarb-tui` dashboard: images, clones, provenance and logs, with background refresh and
  herdr's colour theme when run inside herdr.

### Project
- `./tools/check.sh` (lint, security invariants, self-tests, TUI tests), also run by CI on every
  pull request.
- Licensed under FSL-1.1-ALv2, with third-party notices.

### Known issues
- [#63](https://github.com/errantpacket/RhubarbTart/issues/63): macOS 26 builds sometimes miss a
  Setup Assistant keystroke and time out; running the build again usually works.

[Unreleased]: https://github.com/errantpacket/RhubarbTart/compare/v0.1.0...HEAD
[0.1.0]: https://github.com/errantpacket/RhubarbTart/releases/tag/v0.1.0
