# RhubarbTart plan: iOS app testing

_Status: design record, written 2026-10-02 (#176). Nothing in it is built yet unless a section
says so. The hands-on spike is #172; the public page describing what works today is
[Testing macOS and iOS apps](testing-apple-apps.md)._

## Goal

Make a RhubarbTart macOS guest a verified, disposable workstation for iOS app security testing.
The guest holds Xcode, the iOS Simulator and the testing tools, all pinned, verified and recorded
like any other input. The app under test runs in the Simulator, its traffic and storage are
examined in the clone, and what happens is kept as engagement evidence on the host. A physical
device is an optional extension for the parts the Simulator can't cover.

Non-goals: virtualizing iOS itself (no VM can), jailbreaking, and anything that needs an Apple
Account or other secret baked into an image.

## Where things stand

Built and merged:

- Clean, pinned, disposable macOS 26 and 27 guests, with backend testing through ZAP and
  engagement evidence. App bundles can be examined with the built-in `codesign`, `plutil` and
  `ditto`.
- The two-macOS-VM limit is reported at once instead of as misleading timeouts (#166).
- Guests get no host microphone or clipboard channel (#170).
- The public docs describe what exists for iOS and Apple's limits (#167, #174).
- Tart 2.40.1 is being validated (#171).

Not built: Xcode, the Simulator runtime, an iOS profile, app intake, Simulator evidence, and any
physical-device path. Before #167, the docs implied the toolchain existed and suggested installing
Xcode by hand. A hand install leaves software in a clone that its image doesn't record, so it is
not part of this design.

## Principles applied

The [`rhubarb-dev` invariants](https://github.com/errantpacket/RhubarbTart/blob/main/.claude/skills/rhubarb-dev/SKILL.md)
hold for this work without exception:

| Invariant | What it means for iOS testing |
|---|---|
| Only vendor installers | Xcode and the Simulator runtime come from Apple. No third-party Xcode images or mirrors |
| Pinned by a tool, re-verified in the guest | Each installer is pinned by hash and Apple's signature, checked on the host and again in the guest |
| No reusable credentials | No Apple Account, signing identity, pairing record or provisioning profile in any image. Anything account-bound happens per clone at runtime and dies with it |
| Sealed posture | SIP, Gatekeeper and the firewall stay on; no auto-login; no passwordless `sudo`. Any change for debugging is a recorded decision with a seal assertion |
| The seal asserts; the smoke test proves | The image is named only after a Simulator demonstrably boots in a test clone (if that can be done without a desktop session; see the spike) |
| Names derive from inputs | Xcode and runtime hashes become part of the image name |
| Licensing | Images containing Xcode are never published publicly; the tooling enforces it |
| Evidence on the host | Traffic records, app storage snapshots, screenshots and logs leave the clone as engagement evidence |

## Platform facts and limits

| Fact | Effect | Source |
|---|---|---|
| A Mac runs at most two macOS VMs at once; Linux VMs don't count | A build needs a free slot; at most two macOS clones run together. Reproduced on an M1 with Tart 2.38.0 | [Tart discussion](https://github.com/cirruslabs/tart/discussions/1054), #166 |
| Downloading Xcode needs an Apple Account | It can't be fetched by a resolver. It is supplied by the operator as a local installer under `vendor/`, like other tools with no public download | [xcodeinstall](https://github.com/sebsto/xcodeinstall) |
| Xcode `.xip` archives are signed by Apple | `pkgutil --check-signature` can verify the archive before it is expanded | [AppleInsider](https://appleinsider.com/articles/16/07/28/apple-xcode-8-beta-now-secured-with-digital-signatures-in-xip-format) |
| Xcode's licence forbids redistribution | An image with Xcode must never reach a public registry | [Xcode and Apple SDKs Agreement](https://www.apple.com/legal/sla/docs/xcode.pdf) |
| Xcode 27 needs macOS 26.6 or later on Apple silicon; it debugs devices on iOS 17 and later | `tahoe-research` (26.6.2) and `goldengate-research` (27) qualify | [Xcode 27 release notes summary](https://blakecrosley.com/blog/xcode-27-release) |
| Simulator runtimes can be exported to a `.dmg` and imported offline | `xcodebuild -downloadPlatform iOS -exportPath DIR`, then `xcodebuild -importPlatform FILE`, so the runtime can be pinned by hash | [Apple](https://developer.apple.com/documentation/xcode/downloading-and-installing-additional-xcode-components) |
| The Simulator runs in macOS VMs; CI images that use it turn on auto-login | Works in principle. Whether it boots without a desktop session is unverified, and our images forbid auto-login | [tart-xcode-runner](https://github.com/novotnyllc/tart-xcode-runner/blob/main/README.md) |
| Graphics glitches and crashes have been reported for some apps in the Simulator in VMs | Fidelity risk to record per engagement | [flutter#150169](https://github.com/flutter/flutter/issues/150169) |
| App Store and device builds don't run in the Simulator | Dynamic testing needs a Simulator build from the developer; an App Store `.ipa` can only be examined statically | |
| Apple Account sign-in in a VM needs macOS 15 or later on host and guest and a fresh VM; the Mac App Store doesn't work | Signing into Xcode for a test app should work; App Store and TestFlight installs don't | [MacRumors](https://www.macrumors.com/2024/06/20/macos-sequoia-adds-icloud-support-vms/) |
| macOS 27 added USB passthrough, but the framework refuses devices with isochronous endpoints, including iPhones | No USB route to a physical device, even after Tart supports passthrough | [Parallels KB 128867](https://kb.parallels.com/en/128867), [openai/tart#139](https://github.com/openai/tart/issues/139) |
| Xcode's first pairing with a device needs a USB cable; network debugging is enabled afterwards | The deciding question for physical devices in a clone | [Apple forum](https://developer.apple.com/forums/thread/89040) |
| Tart passes the Mac's microphone and a clipboard channel to guests by default | Fixed for every VM RhubarbTart starts (#170) | Tart source, `Sources/tart/VM.swift` |

## What the Simulator covers

Measured against the [OWASP MASVS](https://mas.owasp.org/MASVS/) areas a scoped test usually
covers:

| Area | Simulator | Notes |
|---|---|---|
| Network and API | Yes | Interception with ZAP, API and backend testing, TLS and App Transport Security settings, whether pinning is enforced. On most engagements this is where most findings come from |
| Data storage | Mostly | The app's files, preferences, databases, caches and logs are on disk to inspect. Keychain contents are visible, but neither the keychain nor file encryption is hardware-backed, so protection-class claims can't be confirmed |
| Authentication and session logic | Yes | Logins, tokens, sessions, authorization and business logic |
| Platform interaction | Mostly | URL schemes and deep links (`simctl openurl`), WebViews, the pasteboard, local push (`simctl push`). Universal links, Sign in with Apple and Apple Pay are limited |
| Code and build | Partly | The Simulator build differs from the release binary (compiler settings, debug flags, sometimes configuration). Static review also covers the real `.ipa` |
| Biometrics and Secure Enclave | No | Face ID can be simulated, but not its hardware binding |
| Resilience | No | Jailbreak detection, anti-debugging, anti-tampering and App Attest only mean anything on a device |
| Hardware features | No | Camera, Bluetooth, NFC, cellular, real sensors. Location can be simulated |

**Rule of thumb.** The Simulator is enough for a typical "app plus API" grey-box test with a
Simulator build, scoped to data handling, network security, authentication and logic. A device
is needed when the scope includes the production App Store build, resilience, biometrics or
the Secure Enclave, or hardware features. Those tests usually use a jailbroken device, which is
outside RhubarbTart. A report from a Simulator-only test states what it didn't cover.

So the Simulator is the main deliverable of this plan, and physical devices are an add-on.

## Design

### Xcode as a verified input

- A package `xcode` with `"resolver": "local"`: the operator places the `.xip` under
  `vendor/xcode/`, and resolve pins its sha256, size and version.
- A new verification step for `.xip` archives in `tools/rhubarb/macos.py`: `pkgutil
  --check-signature` must report Apple's own signing chain. Its exact output is captured during
  the spike and matched exactly, as the other signature checks do.
- In the guest, `install.sh` re-checks the hash and signature, expands the archive with
  `xip --expand`, moves it to `/Applications`, accepts the licence (`xcodebuild -license accept`)
  and runs `xcodebuild -runFirstLaunch`. `finalize.sh` asserts that Xcode is present at the
  pinned version and that `xcode-select -p` points at it.
- The Command Line Tools inside Xcode provide `otool`, `strings`, `lldb` and the rest for
  static analysis.
- No Apple Account is signed in during the build. Account-bound steps happen per clone.

### The iOS Simulator runtime

- A package `ios-simulator-runtime`, also `local`: the operator exports it once with
  `xcodebuild -downloadPlatform iOS -exportPath vendor/ios-simulator-runtime/`, and resolve pins
  the `.dmg` by hash. Whether the `.dmg` carries a verifiable Apple signature is checked in the
  spike; if it doesn't, the hash is trust on first use and the lock says so.
- The guest imports it with `xcodebuild -importPlatform`. The seal asserts that `xcrun simctl
  list runtimes` shows the pinned runtime.
- The smoke test creates and boots a simulator and waits for `simctl bootstatus`, if spike
  question S1 shows this works without a desktop session. If it needs one, the seal assertion is
  the only proof, and the gap is stated in the trust model.

### The `ios-research` profile

- Base `macos-26` (26.6 or later) or `macos-27`; packages `xcode`, `ios-simulator-runtime`, `zap`
  and `chrome`.
- Disk: Xcode is about 12 GB installed and a runtime about 8 GB, so the image grows from about
  27 GB to about 50 GB. The profile sets `disk_gb` with room for simulators and app data (120 GB
  proposed; the spike measures it).
- Memory: 8 GB per guest, the base default. The spike measures whether that is enough on an 8 GB
  host.
- The provenance records `redistributable: false` for any image containing a package whose licence
  forbids redistribution, and `scripts/publish.sh` refuses to push such an image to anything but
  the local registry.

### Testing workflow

- **App intake with chain of custody.** A command copies the app under test into an engagement
  clone: a Simulator `.app`, or an `.ipa` for static review. It records the file's sha256, source
  and time in the evidence journal before anything runs. Today the only path is `rhubarbtart ssh` with a
  redirect, which records nothing.
- **Traffic interception.** A helper sets the guest's proxy to ZAP and trusts ZAP's certificate in
  the booted Simulator (`xcrun simctl keychain booted add-root-cert`). ZAP's session or a HAR
  export goes to `~/evidence`.
- **Storage and runtime evidence.** Snapshots of the app's container, `simctl io` screenshots and
  video, and Simulator logs go to `~/evidence`, so `evidence collect` hashes them on the host.
- **Static analysis.** `codesign`, `plutil` and the Xcode tools against the delivered `.ipa`, with
  their output recorded through `rhubarbtart exec`.

### Instrumentation (decision needed)

Dynamic instrumentation (Frida, objection, LLDB attached to the app) is common in iOS testing.
Simulator apps are local processes, so attaching to them may work with SIP on, but that is
unverified. It may need `DevToolsSecurity -enable`, which lets members of the developer group
debug processes without a password prompt. That is a posture change. Options: leave
instrumentation out; allow it in `ios-research` only, with a seal assertion and a smoke-test
check that records exactly what changed; or allow it per clone at runtime and journal it. The
spike checks what is actually required before this is decided.

### Agents

Once the workflow exists, a herdr agent can drive the Simulator through `rbt-range` (`xcrun
simctl`, proxy setup, evidence capture), with tiered approvals for anything that reaches beyond
the clone. This depends on S1: an agent can't use a desktop session.

### Physical devices (#172)

| Route | Xcode in the guest | Verdict |
|---|---|---|
| macOS 27 USB passthrough | n/a | Blocked for iPhones by the framework; it would also need an entitlement in Tart |
| usbfluxd (relaying the host's usbmuxd) | iOS 16 and earlier only | Rejected: root daemons, an unauthenticated relay port, unmaintained |
| USB over IP (VirtualHere) | No | Rejected: iPhones unsupported with macOS clients |
| pymobiledevice3 tunnels | No | Possible for non-Xcode tooling, pinned like any input |
| Xcode wireless debugging | Yes | The only candidate. It needs the clone on the device's network (bridged) and a pairing, whose first step normally needs a cable |

If the spike shows a clone can pair with no cable, the design is an opt-in "device lab" run mode.
It means a bridged run on an isolated test network and pairing done per clone at runtime. What a
bridged clone exposes needs a recorded decision. If a cable is required, the choice is between leaving
physical devices out of scope and deciding whether a pairing record made on the host may be copied
into a clone. That record is a long-lived credential that lets the clone act as the host towards
the device, so copying it is not a default.

## The hardware spike (#172)

One throwaway clone of `tahoe-research`, with Xcode 27 installed by hand **for the spike only**.
The full checklist is on #172.

**Simulator questions (NAT network, over SSH):**

- **S1.** Does a simulator boot over SSH with nobody logged in at the VM window? Then with a
  desktop login.
- **S2.** Can ZAP's certificate be trusted in the Simulator, and does ZAP see an HTTPS request from
  it?
- **S3.** Guest and host memory and disk while the Simulator runs, on the M1 with 8 GB.
- Also recorded: the exact `pkgutil` output for the `.xip`, whether the runtime `.dmg` is signed,
  and what instrumentation needs.

**Device questions (bridged onto an isolated test network):**

- **Topology.** The Mac's Ethernet adapter goes to a separate test router; the iPhone joins that
  router's Wi-Fi; nothing else is on it. The Mac keeps its home Wi-Fi for internet, so the NAT
  network and `rhubarbtart ssh` are unchanged. Client isolation is off on the test router.
- **Q1.** Can the iPhone pair with Xcode in the clone with no cable? This decides the device route.
- **Q2.** If it pairs, can Xcode install and debug a test app over the network?
- **Q4.** What does the bridged clone expose on the test network (`nmap` from the Mac)?

**Prerequisites from the operator:**

- the Xcode 27 `.xip`, downloaded with an Apple Account;
- an iPhone on iOS 17 or later, and an Apple Account for signing a test app;
- the test router;
- a free Mac, with no validation builds running.

The S questions can run first, without the test router. They decide most of this plan.

## Phased roadmap

| Phase | Deliverable | Exit criteria |
|---|---|---|
| 0 · Ground truth (partly done) | Docs that match reality (#167, #174); the VM-limit fix (#166); no host microphone or clipboard (#170); the spike (#172) | The spike's S and Q questions answered with evidence on #172 |
| 1 · Verified toolchain | `xcode` and `ios-simulator-runtime` packages with `.xip` signature verification; the `ios-research` profile; seal assertions; the smoke test boots a simulator (or the gap is documented); `redistributable: false` enforced by `publish.sh` | An `ios-research` image builds, passes its smoke test on the Mac, and its provenance records Xcode and the runtime |
| 2 · Testing workflow | App intake with chain of custody; the interception helper; Simulator evidence capture; static analysis recorded through `exec` | A Simulator-only engagement against a test app produces a sealed vault with the app's hash, traffic, storage snapshots and screenshots |
| 3 · Extensions | The instrumentation decision; herdr agents driving the Simulator; the device lab mode if Q1 allows | Each decision recorded; each feature validated on hardware |

## Open questions and decisions needed

- **S1, the desktop session.** If the Simulator needs one, how is it proved at build time, and how
  can agents use it without auto-login?
- **The runtime's signature.** If the exported `.dmg` isn't signed, its hash is trust on first
  use, and the review checklist for locks needs a rule for it.
- **Instrumentation.** Which of the three options above, if any.
- **Physical devices.** Depends on Q1; the pairing-record question if a cable is required.
- **Xcode updates.** Xcode ships often. A new Xcode means a new image; the update-inputs review
  needs a checklist for it (signature, version, macOS requirement, runtime compatibility).
- **One profile or two.** Whether macOS 26 and 27 each get an `ios-research` variant, given the
  two-macOS-VM limit and the disk cost.
- **Delivery of client apps.** How a client hands over a Simulator build, and whether intake
  should also accept a source checkout and build it in the clone.
