# Third-party notices

RhubarbTart is licensed under the [Functional Source License, Version 1.1, ALv2 Future
License](LICENSE.md) (FSL-1.1-ALv2). This file records third-party material that has its own
terms.

## Code adapted into this repository

**cirruslabs/macos-image-templates**: MIT License. The macOS Setup Assistant `boot_command`
keystroke sequence in `packer/macos/*.pkr.hcl` was adapted from this project.

```
MIT License

Copyright (c) 2018 Cirrus Labs

Permission is hereby granted, free of charge, to any person obtaining a copy
of this software and associated documentation files (the "Software"), to deal
in the Software without restriction, including without limitation the rights
to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
copies of the Software, and to permit persons to whom the Software is
furnished to do so, subject to the following conditions:

The above copyright notice and this permission notice shall be included in all
copies or substantial portions of the Software.

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
SOFTWARE.
```

## Bundled runtime dependency

**Textualize/textual**: MIT License. Pinned and hash-locked for the `./rhubarbtart-tui` dashboard
(`tools/rhubarb_tui.py`), fetched by `uv` at run time; not redistributed in this repository.

## Tools RhubarbTart runs (fetched by `tools/bootstrap.sh`, invoked as separate programs)

These are **not** part of RhubarbTart's licensed code. RhubarbTart downloads them (pinned and
checksum-verified, see `config/toolchain.env`) and calls them as separate processes; it neither
links them nor redistributes their source. Each remains under its own license:

| Tool | License |
|---|---|
| Tart (`openai/tart`) | FSL-1.1-ALv2 |
| Packer (HashiCorp) | BUSL-1.1 |
| packer-plugin-tart (`cirruslabs/packer-plugin-tart`) | MPL-2.0 |
| cosign (Sigstore) | Apache-2.0 |
| zot (`project-zot/zot`) | Apache-2.0 |
| uv (`astral-sh/uv`) | Apache-2.0 |
| GnuPG and its libraries (libgcrypt, libgpg-error, libassuan, libksba, npth) | GPL / LGPL (built from source, invoked as binaries) |

If you use RhubarbTart to build a commercial product, review Tart's FSL and Packer's BUSL terms,
which place their own conditions on commercial and production use.

## Guest inputs

Operating-system installers and packages (Apple IPSW/macOS, Kali, NixOS, Google Chrome, ZAP,
Cloudflare WARP, Tailscale, and others named in `config/`) are downloaded from their vendors, then
pinned and verified. RhubarbTart does not redistribute them. Building or running a guest is subject
to the vendor's own terms; for example, Apple's macOS Software License Agreement (macOS runs only
on Apple-branded hardware) applies to whoever builds or runs a macOS guest.
