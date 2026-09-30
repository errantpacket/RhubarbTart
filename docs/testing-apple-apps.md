# Testing macOS and iOS apps

Testing Apple software has a few problems that are specific to the platform. This page lays them
out and shows how RhubarbTart addresses each, and it is honest about the one it cannot solve
(virtualizing iOS).

| Challenge | How RhubarbTart addresses it |
|---|---|
| A Mac is hard to return to a known-clean state | Work in a **disposable clone**; the sealed image is never booted, and `reset` gives a pristine copy in seconds |
| Test results depend on what was installed | The image is built from **pinned, verified inputs** and named by their hash, so you can state exactly what the test machine contained |
| Repeating a test from an identical start | Re-clone the same image, or re-provision an engagement; every run starts from the same recorded baseline |
| Isolating untrusted apps and separating clients | Each test in its own clone with its **own credentials**; clones can't reach each other; engagements scope the network |
| Hardening must be realistic, not altered | SIP, Gatekeeper and the firewall stay **on**; the build tools are notarized; the posture is proven from outside before the image is named |
| Several macOS versions to cover | A **profile per version** (macOS 26 and 27 today), each pinned and built the same way |
| iOS can't run in a VM | RhubarbTart gives a clean, reproducible **macOS** host for the iOS toolchain (Xcode, the Simulator, proxies); it does not, and cannot, virtualize iOS |
| Apps talk to backends you need to observe | Add intercepting proxies as packages; engagements scope egress and record the session as evidence |

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
on real hardware. The part you can make clean, reproducible and disposable is the **macOS host and
its toolchain**, not an iOS device.

## How RhubarbTart addresses them

### A pristine Mac, every time

You never boot the sealed image; `rhubarb new` makes a copy-on-write **clone** to work in, and
`rhubarb reset` swaps in a fresh one. Each test starts from the same clean state, and throwing the
clone away is instant. See [Key concepts](concepts.md) and [Using your VMs](using.md).

### A test machine you can describe exactly

The image is built only from pinned, verified vendor inputs, and its name is the hash of those
inputs (see [Trust model](trust-model.md) and [How it works](how-it-works.md)). So a report can
state precisely what the test machine contained, and anyone can rebuild the identical machine.
Anything you find that was not in the image came from the app under test, not from leftover tooling.

### Isolation per test and per client

Every clone gets its own random password, kept in the host keychain, never shared between clones
(see [macOS guests and containers](macos-and-containers.md) for the credential model). Clones can't
reach each other; an [engagement](using.md#engagements) groups a scope, scopes its network, and can
give a target no route out. One client's work is one engagement, and nothing crosses that line.

### Realistic, hardened macOS

The macOS guests keep SIP, Gatekeeper and the application firewall on, disable auto-login and
password-free sudo, and are built with a notarized toolchain. That posture is asserted at seal and
**proven from outside by a smoke test** before the image is named, so the environment matches a
real, hardened Mac rather than a loosened test rig ([Trust model](trust-model.md)).

### Several macOS versions, built the same way

Each macOS version is its own profile (macOS 26 and 27 today, more as Apple ships them), pinned to a
specific build and produced by the same verified pipeline. Testing across versions is a matter of
picking the profile, not maintaining a rack of hand-configured Macs.

### A workstation for the iOS toolchain

For iOS work, the macOS guest is where Xcode, the iOS Simulator, intercepting proxies and analysis
tools live. RhubarbTart makes that host clean, pinned and disposable. It is explicit about the
boundary: **it is not an iOS VM**, and real-device testing still needs a device. What it gives you
is a reproducible, throwaway Mac to run the Simulator and the surrounding tooling on.

### Backends and traffic interception

Apple apps lean on backends, so testing usually means watching and shaping traffic. Add an
intercepting proxy to a profile's tool list (ZAP ships as a package today; others can be added, see
[Define your own guest](profiles.md)), and use an engagement to scope egress and record the session
as tamper-evident [evidence](using.md#engagements). You get a controlled network and a signed record
of what the test did.

## In short

RhubarbTart does not virtualize iOS, because nothing can. What it does is remove the part of Apple
app testing that is actually painful: getting, keeping and proving a clean, hardened, isolated Mac,
and being able to throw it away and start again identically. For macOS apps that is the whole job;
for iOS apps it is the reproducible host the Simulator and your backend tooling run on.
