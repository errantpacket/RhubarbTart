# Publishing and stacked clones

**Optional.** Everything else in RhubarbTart works without this. Publishing puts a verified image
into an OCI registry, signed and with its provenance attached, so it can be verified by digest,
reused as a read-only base for **stacked clones** (macOS), or, later, shared with other Macs.
New to the terms? See [Key concepts](concepts.md#sharing-images-optional).

## The pieces

| Piece | What it is |
|---|---|
| `scripts/registry.sh start\|stop\|status` | A private OCI registry ([zot](https://zotregistry.dev), pinned in `.toolchain`) listening on **127.0.0.1 only**, port 5780 (`RHUBARB_REGISTRY_PORT` changes it). It stores images under the git-ignored `cache/registry/` |
| `scripts/signing-key.sh init\|status` | Creates the **cosign** key pair once. The private key and its passphrase live only in your macOS keychain; the public key is committed at `config/keys/rhubarb-cosign.pub` |
| `scripts/publish.sh publish <image>` | Pushes a smoke-passed image, signs it, attaches its provenance record as a signed attestation, verifies the result, and records `out/<image>.published.json` |
| `scripts/publish.sh verify <ref@digest>` | Checks a published image's signature and provenance against the public key |
| `rhubarb new NAME --profile P --from-registry` | Verifies the published copy, then makes a **stacked clone** on it (macOS only) |

## Walkthrough

```sh
./scripts/signing-key.sh init                      # once per publisher; commit the public key
./scripts/registry.sh start                        # localhost-only registry
./scripts/publish.sh publish rbt-tahoe-research-edbb1008a6c0
./rhubarb new mac-1 --profile tahoe-research --from-registry
./rhubarb list                                     # IMAGE column: current / outdated / base-missing
./rhubarb rm mac-1                                 # also frees the pulled base once nothing uses it
./scripts/registry.sh stop
```

Only images that passed their smoke test **and** have a matching provenance record can be
published; `-unverified` candidates are refused. Anything that fails verification is never cloned.

<details>
<summary><b>Why signing is offline, and what that means</b></summary>

`cosign` normally records signatures in Sigstore's **public** transparency log. For private
security-research images that would publish metadata (image digests and registry names), so
RhubarbTart signs **offline**:

- The key reaches cosign only through its environment: never on a command line, never in a file.
- The committed signing config (`config/cosign/signing-config-offline.json`) lists no public
  certificate authority, transparency log or timestamp service.
- cosign would still try to download Sigstore's public trust data, so `publish.sh` only lets it
  reach the registry host. Everything else goes to a dead proxy, and you'll see a harmless
  `Could not fetch trusted_root.json` warning.

The trade-off: with no public log, trust rests on the committed public key alone. Anyone
verifying needs that key (`RHUBARB_COSIGN_PUB` points `verify` at another publisher's).

</details>

<details>
<summary><b>Stacked clones: how they work and what they cost</b></summary>

Tart can build a clone as a thin **overlay** (tens of MB) on an immutable base pulled from a
registry, instead of a copy-on-write copy of a local image. Tart supports this **only for macOS
images, and only from a registry**, which is why it needs publishing first.

- **Disk.** The pulled base is about as big as the image (~26 GB for macOS) and is shared by every
  stacked clone of that image. On a single Mac this is *extra* space on top of the local image
  and the registry copy, so it's opt-in. It pays off with several Macs sharing one registry.
- **Where the base lives.** In Tart's internal content store. Once Tart's cache listing is pruned,
  `tart list` no longer shows it, so `rhubarb` records each stacked clone's base and shows
  `base-missing` in `rhubarb list` if it disappears.
- **Cleanup.** `rhubarb rm` releases the base when the last clone using it is removed.
  `rhubarb reset` keeps a stacked clone stacked, and checks the image is published before
  destroying anything.

</details>

<details>
<summary><b>Freeing the space again</b></summary>

```sh
./scripts/registry.sh stop
rm -rf cache/registry out/*.published.json      # the registry's storage + its publication records
```

Published images can be recreated any time with `publish.sh publish`. The signing key stays in the
keychain for next time. The same key also signs sealed evidence vaults
([Using your VMs](using.md#engagements)).

</details>

**Not yet supported:** remote registries that require a login ([#74](https://github.com/errantpacket/RhubarbTart/issues/74)).
`RHUBARB_REGISTRY` can point at another registry, but the digest lookup after pushing doesn't
authenticate yet, and `--from-registry` assumes the image was built on this Mac.

← back to the [README](../README.md)
