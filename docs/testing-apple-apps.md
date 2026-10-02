# Testing macOS and iOS apps

Testing Apple software has a few problems that are specific to the platform. This page lays them
out and shows how RhubarbTart addresses each. For macOS apps it covers the whole test machine.
For iOS apps it covers less today: the iOS toolchain (Xcode and the Simulator) is not part of
RhubarbTart yet, and [iOS apps today](#ios-apps-today) says what works and what Apple's platform
allows.

| Challenge | How RhubarbTart addresses it |
|---|---|
| A Mac is hard to return to a known-clean state | Work in a **disposable clone**; the sealed image is never booted, and `reset` gives a fresh copy of the image |
| Test results depend on what was installed | The image is built from **pinned, verified inputs** and named by their hash, so you can state exactly what the test machine contained |
| Repeating a test from an identical start | Re-clone the same image, or re-provision an engagement; every run starts from the same recorded baseline |
| Isolating untrusted apps and separating clients | Each test in its own clone with its **own credentials** (by default, for images with key SSH); clones can't reach each other; an engagement's links are the only path between its clones |
| Hardening must be realistic, not altered | SIP, Gatekeeper and the firewall stay **on**; the build toolchain is pinned and verified; the posture is proven from outside before the image is named |
| Several macOS versions to cover | A **profile per version** (macOS 26 and 27 today), each pinned and built the same way |
| iOS can't run in a VM | Not solved yet. No VM can run iOS. Xcode and the Simulator would run in a macOS guest, but they aren't in the package catalog yet ([iOS apps today](#ios-apps-today)) |
| Apps talk to backends you need to observe | Add intercepting proxies as packages; commands run through an engagement are journaled as evidence and can be sealed into a signed vault |

## The challenges, in detail

**A clean Mac is expensive to get and keep.** A physical or long-lived Mac accumulates state:
installed tools, caches, TCC permission grants, login items, leftover files from the last app you
looked at. Returning it to a known-good baseline means an erase and reinstall, which is slow, so in
practice tests run against a machine whose exact contents nobody can state. That undermines both
reproducibility and any security finding ("was that file dropped by the app, or already here?").

**Isolation cuts two ways.** You may be testing something you do not trust (a suspicious app, a
sample, an app under assessment), which you do not want touching your real machine. You may also be
testing for more than one client, and one client's data must never leak into another's test.

**Hardening has to be real.** macOS behaves differently with SIP, Gatekeeper, notarization and TCC
in force. A test environment that disables those to make life easier no longer reflects how the app
behaves on a customer's Mac, so the results do not transfer.

**iOS is the hard part.** There is no way to run iOS in a virtual machine: Apple's virtualization
supports macOS and Linux guests only, and iOS is not one of them. iOS testing therefore happens on
a **Mac**, through Xcode and the iOS **Simulator** (which runs on macOS, not a separate device), or
on real hardware. The part that can be made clean, reproducible and disposable is the **macOS
host and its toolchain**, not an iOS device.

## How RhubarbTart addresses them

### A pristine Mac, every time

You never boot the sealed image; `rhubarbtart new` makes a copy-on-write **clone** to work in, and
`rhubarbtart reset` swaps in a fresh one. Each test starts from the same clean state, and throwing the
clone away is instant. See [Key concepts](concepts.md) and [Using your VMs](using.md).

### A test machine you can describe exactly

The image is built only from pinned, verified vendor inputs, and its name carries a hash of those
inputs (see [Trust model](trust-model.md) and [How it works](how-it-works.md)). So a report can
state precisely what the test machine contained, and anyone with the lock can rebuild it from the
same inputs.
Anything you find that was not in the image came from the app under test, not from leftover tooling.

### Isolation per test and per client

Every clone gets its own random password, kept in the host keychain and never shared between
clones. That needs an image built with SSH keys; otherwise a clone keeps the image's password,
and `rhubarbtart list` shows it as `inherited` (see [Using your VMs](using.md)). Clones can't reach each other. An
[engagement](engagements.md) groups one client's clones, and its declared links are the only
path between them. One client's work is one engagement.

### Realistic, hardened macOS

The macOS guests keep SIP, Gatekeeper and the application firewall on, disable auto-login and
password-free sudo, and are built with a pinned, verified toolchain. That posture is asserted at seal and
**proven from outside by a smoke test** before the image is named, so the environment matches a
real, hardened Mac rather than a loosened test rig ([Trust model](trust-model.md)).

### Several macOS versions, built the same way

Each macOS version is its own profile (macOS 26 and 27 today, more as Apple ships them), pinned to a
specific build and produced by the same verified pipeline. Testing across versions is a matter of
picking the profile, not maintaining a rack of hand-configured Macs.

### Backends and traffic interception

Apple apps lean on backends, so testing usually means watching and shaping traffic. Add an
intercepting proxy to a profile's tool list (ZAP ships as a package today; others can be added, see
[Define your own guest](profiles.md#define-your-own-guest)). Run the test's commands in an engagement's clone with
`rhubarbtart exec`, and each one is journaled as tamper-evident [evidence](engagements.md) that
you can seal into a signed vault. Engagements do not filter a clone's outbound traffic yet; the
network control today is that clones can't reach each other.

## iOS apps today

**What works now.**

- **Backend and API testing.** An iOS app's server side can be tested from a macOS or Kali guest
  with ZAP, in an engagement that records the evidence. See
  [Backends and traffic interception](#backends-and-traffic-interception).
- **Looking at an app bundle.** A macOS clone has Apple's built-in `codesign`, `plutil` and
  `ditto`. They show an app's signature, entitlements and `Info.plist`, and unpack an `.ipa`. Tools
  such as `otool` and `strings` need the Xcode Command Line Tools, which the images don't include.

**What isn't built yet.** There is no Xcode, no iOS Simulator runtime and no mobile testing tool
(such as an iOS-aware proxy setup or instrumentation) in the package catalog. Installing Xcode by
hand in a clone works, but the clone then contains software its image doesn't record: the
provenance no longer describes the machine, and `reset` discards the install. For RhubarbTart to
offer the iOS toolchain, it has to be pinned, verified and proven like every other input.

**Limits that come from Apple's platform.**

| Limit | What it means here |
|---|---|
| A Mac runs at most **two macOS VMs** at once | Apple's licence sets the limit and the Virtualization framework enforces it. Linux VMs don't count. See [Requirements](reference.md#requirements) |
| Downloading Xcode needs an **Apple Account** | It can't be fetched automatically the way other inputs are. It would be supplied as a local installer, like other tools with no public download |
| Xcode's licence forbids **redistributing** it | An image that contains Xcode must never be published to a public registry ([Xcode and Apple SDKs Agreement](https://www.apple.com/legal/sla/docs/xcode.pdf)) |
| App Store and device builds **don't run in the Simulator** | Running an app in the Simulator needs a Simulator build of it, usually from the developer. An App Store `.ipa` can only be examined, not run |
| **Apple Account sign-in** inside a VM | Works only when the host and guest run macOS 15 or later and the VM was installed fresh; the Mac App Store still doesn't work in a VM. RhubarbTart images never contain an account |
| No **USB passthrough** of an iPhone or iPad yet | macOS 27 added USB passthrough to the Virtualization framework, for a macOS 27 host and guest. Tart doesn't support it yet ([tart#139](https://github.com/cirruslabs/tart/issues/139)), so a physical device can't be attached to a clone |

## Scope

RhubarbTart does not virtualize iOS, because Apple's virtualization cannot. It handles the part of
Apple app testing that is hard to do by hand: getting, keeping and proving a clean, hardened,
isolated Mac, and throwing it away to start again from the same image. For macOS apps that is the
whole job. For iOS apps it covers backend testing and inspecting app bundles today; the
reproducible host for Xcode and the Simulator is not built yet.
