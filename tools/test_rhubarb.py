# /// script
# requires-python = ">=3.12"
# dependencies = []
# ///
"""Offline self-tests for the security-relevant parts of tools/rhubarb (run by tools/check.sh).

Each expected value was produced by the reference implementation, not by this code:
  - Ed25519: RFC 8032 §7.1 test vectors 1 and 2
  - NAR: `nix hash path` (Nix 2.28.4) on the exact tree built by _nar_tree()
  - dpkg ordering: deb-version(7) semantics as implemented by dpkg --compare-versions
"""

import os
import sys
import tempfile
import types
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from rhubarb.apt import dpkg_cmp  # noqa: E402
from rhubarb.distsign import _file_msg, ed25519_verify  # noqa: E402
from rhubarb.nar import nar_sha256  # noqa: E402

import json  # noqa: E402
import subprocess  # noqa: E402

FAILS = []


def check(name: str, cond: bool) -> None:
    print(f"  {'ok  ' if cond else 'FAIL'}  {name}")
    if not cond:
        FAILS.append(name)


def _is_frozen(obj, attr: str, value) -> bool:
    """True iff setting attr on obj raises (a frozen dataclass forbids assignment)."""
    try:
        setattr(obj, attr, value)
        return False
    except Exception:
        return True


def test_ed25519() -> None:
    v1_pub = bytes.fromhex("d75a980182b10ab7d54bfed3c964073a0ee172f3daa62325af021a68f707511a")
    v1_sig = bytes.fromhex("e5564300c360ac729086e2cc806e828a84877f1eb8e5d974d873e065224901555fb8821590a33bacc61e39701cf9b46bd25bf5f0595bbe24655141438e7a100b")
    v2_pub = bytes.fromhex("3d4017c3e843895a92b70aa74d1b7ebc9c982ccf2ec4968cc0cd55f12af4660c")
    v2_msg = bytes.fromhex("72")
    v2_sig = bytes.fromhex("92a009a9f0d4cab8720e820b5f642540a2b27b5416503f8fb3762223ebdb69da085ac1e43e15996e458f3613d0f11d8c387b2eaeb4302aeeb00d291612bb0c00")
    check("ed25519 RFC8032 vector 1 verifies", ed25519_verify(v1_pub, b"", v1_sig))
    check("ed25519 RFC8032 vector 2 verifies", ed25519_verify(v2_pub, v2_msg, v2_sig))
    check("ed25519 rejects wrong message", not ed25519_verify(v2_pub, b"\x73", v2_sig))
    bad = bytearray(v2_sig)
    bad[10] ^= 1
    check("ed25519 rejects flipped signature bit", not ed25519_verify(v2_pub, v2_msg, bytes(bad)))
    check("ed25519 rejects wrong key", not ed25519_verify(v1_pub, v2_msg, v2_sig))
    check("ed25519 rejects S >= L (malleability)",
          not ed25519_verify(v1_pub, b"", v1_sig[:32] + (2**253).to_bytes(32, "little")))
    check("distsign message = blake2s256 || len_le64",
          _file_msg(b"abc")[-8:] == (3).to_bytes(8, "little") and len(_file_msg(b"abc")) == 40)


def _nar_tree(root: Path) -> None:
    (root / "sub" / "deeper").mkdir(parents=True)
    (root / "a.txt").write_bytes(b"hello")
    (root / "empty").write_bytes(b"")
    (root / "run.sh").write_bytes(b"#!/bin/sh\necho hi\n")
    os.chmod(root / "run.sh", 0o755)
    for f in ("a.txt", "empty"):
        os.chmod(root / f, 0o644)
    os.symlink("a.txt", root / "link")
    os.symlink("../missing", root / "sub" / "dangling")
    (root / "sub" / "eight").write_bytes(b"12345678")
    (root / "sub" / "deeper" / "big").write_bytes(b"x" * 1000)
    (root / "sub" / "Z-upper").write_bytes(b"")
    (root / "sub" / "a-lower").write_bytes(b"")


def test_nar() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp, "tree")
        root.mkdir()
        _nar_tree(root)
        check("NAR hash matches Nix 2.28.4 on reference tree",
              nar_sha256(root) == "sha256-aCxd7E/HSWSh4hWCo4ABpEtn3rZaP1ALGM2FAOBfR/Y=")
        os.chmod(root / "run.sh", 0o644)
        check("NAR hash changes when an executable bit changes",
              nar_sha256(root) != "sha256-aCxd7E/HSWSh4hWCo4ABpEtn3rZaP1ALGM2FAOBfR/Y=")


def test_dpkg() -> None:
    cases = [("1.0", "1.0", 0), ("1.0", "1.1", -1), ("1.0~rc1", "1.0", -1), ("1:0.1", "2.0", 1),
             ("1.0-1", "1.0-2", -1), ("154.0.8037.57-1", "154.0.8037.100-1", -1),
             ("2026.7.1377.0", "2026.7.1376.0", 1), ("1.0a", "1.0", 1), ("1.0+b1", "1.0", 1)]
    for a, b, want in cases:
        check(f"dpkg_cmp({a}, {b}) == {want}", dpkg_cmp(a, b) == want)


# ---- rhubarb CLI: clone records -------------------------------------------------------------

def _stub(bindir: Path, name: str, body: str) -> None:
    """A tiny stand-in program, run by *this* Python (portable to the Mac, no CLT python3)."""
    f = bindir / name
    f.write_text(f"#!{sys.executable}\nimport json, os, sys, time\nfrom pathlib import Path\n" + body)
    f.chmod(0o755)


def test_records() -> None:
    from rhubarb import clones
    from rhubarb.common import VerifyError
    with tempfile.TemporaryDirectory() as tmp:
        os.environ["RHUBARB_STATE_DIR"] = str(Path(tmp, "state"))
        prof = {"id": "kali-research", "family": "kali", "username": "admin", "options": {"rosetta": True}}
        rec = clones.new_record("work-1", prof, "rbt-kali-research-cc4479ae7492")
        clones.save(rec)
        d = clones.clones_dir()
        f = d / "work-1.json"
        check("state dir is 0700", (os.stat(d).st_mode & 0o777) == 0o700)
        check("record is 0600", (os.stat(f).st_mode & 0o777) == 0o600)
        check("record round-trips", clones.load("work-1") == rec)

        def refused(label, fn):
            try:
                fn()
                check(f"refuses {label}", False)
            except VerifyError:
                check(f"refuses {label}", True)
        refused("clone names starting with rbt-", lambda: clones.check_clone_name("rbt-x"))
        refused("uppercase / odd clone names", lambda: clones.check_clone_name("Work_1"))
        os.chmod(f, 0o644)
        refused("group/world-readable record", lambda: clones.load("work-1"))
        os.chmod(f, 0o600)
        (d / "evil.json").symlink_to(f)
        refused("symlinked record", lambda: clones.load("evil"))
        (d / "evil.json").unlink()
        planted = dict(rec, name="work-2")
        (d / "other.json").write_text(json.dumps(planted))
        os.chmod(d / "other.json", 0o600)
        refused("record whose name != filename", lambda: clones.load("other"))
        bad = dict(rec, extra="x")
        (d / "work-3.json").write_text(json.dumps(dict(bad, name="work-3")))
        os.chmod(d / "work-3.json", 0o600)
        refused("unknown record keys", lambda: clones.load("work-3"))
        bad_acct = dict(rec, name="work-4", password_account="someone-else")
        (d / "work-4.json").write_text(json.dumps(bad_acct))
        os.chmod(d / "work-4.json", 0o600)
        refused("password_account outside clone/image", lambda: clones.load("work-4"))
        good, problems = clones.all_records()
        check("all_records returns only valid records + reports the rest",
              [r["name"] for r in good] == ["work-1"] and len(problems) == 3)
        clones.log_event("test", "work-1", "detail")
        check("event log is 0600", (os.stat(clones.state_dir() / "events.log").st_mode & 0o777) == 0o600)

        # Stage 1B: engagement tagging on the clone record.
        eng_rec = clones.new_record("eng-1", prof, "rbt-kali-research-cc4479ae7492", engagement="acme-ctf")
        clones.save(eng_rec)
        check("record round-trips with engagement set",
              clones.load("eng-1") == eng_rec and eng_rec["engagement"] == "acme-ctf")
        # Back-compat: a record written BEFORE the engagement field (no "engagement" key) must
        # still load, treated as engagement=None — not rejected as a missing key.
        old = {k: v for k, v in rec.items() if k != "engagement"}
        (d / "old-1.json").write_text(json.dumps(dict(old, name="old-1")))
        os.chmod(d / "old-1.json", 0o600)
        check("pre-engagement record (no key) still loads as engagement=None",
              "engagement" not in old and clones.load("old-1")["engagement"] is None)
        # An invalid engagement value is rejected (must be null or an engagement id string).
        bad_eng = dict(rec, name="eng-bad", engagement="Bad_ID!")
        (d / "eng-bad.json").write_text(json.dumps(bad_eng))
        os.chmod(d / "eng-bad.json", 0o600)
        refused("invalid engagement value", lambda: clones.load("eng-bad"))
        del os.environ["RHUBARB_STATE_DIR"]


def test_cli_lifecycle() -> None:
    """new/list/images/reset/rm against stand-in tart + security + ssh."""
    root = Path(__file__).resolve().parent.parent
    with tempfile.TemporaryDirectory() as tmp:
        t = Path(tmp)
        bindir = t / "bin"
        bindir.mkdir()
        (t / "vms.json").write_text("{}")
        (t / "keychain.json").write_text("{}")
        _stub(bindir, "tart", """
db = Path(os.environ["STUB"]) / "vms.json"; vms = json.loads(db.read_text()); a = sys.argv[1:]
save = lambda: db.write_text(json.dumps(vms))
if a[0] == "list": print(json.dumps([{"Source": "local", "Name": n, "Running": v, "State": "running" if v else "stopped"} for n, v in vms.items()]))
elif a[0] == "clone":
    if a[1] not in vms or a[2] in vms: sys.exit(1)
    vms[a[2]] = False; save()
elif a[0] == "delete": vms.pop(a[1]); save()
elif a[0] == "stop": vms[a[1]] = False; save()
elif a[0] == "get": sys.exit(0 if a[1] in vms else 1)
elif a[0] == "ip": print("192.168.64.50") if vms.get(a[-1]) else sys.exit(1)
elif a[0] == "run":
    vms[a[-1]] = True; save()
    while json.loads(db.read_text()).get(a[-1]): time.sleep(0.2)
""")
        _stub(bindir, "security", """
db = Path(os.environ["STUB"]) / "keychain.json"; kc = json.loads(db.read_text()); a = sys.argv[1:]
if a[0] == "find-generic-password":
    acct = a[a.index("-a") + 1]
    print(kc[acct]) if acct in kc else sys.exit(44)
elif a[0] == "-i":
    w = sys.stdin.read().split(); kc[w[w.index("-a") + 1]] = w[w.index("-w") + 1]; db.write_text(json.dumps(kc))
elif a[0] == "delete-generic-password":
    kc.pop(a[a.index("-a") + 1], None); db.write_text(json.dumps(kc))
""")
        _stub(bindir, "ssh", 'print("admin@x: Permission denied (publickey).", file=sys.stderr); sys.exit(255)\n')
        _stub(bindir, "ssh-keygen", "sys.exit(0)\n")
        env = dict(os.environ, PATH=f"{bindir}:{os.environ['PATH']}", STUB=str(t),
                   RHUBARB_STATE_DIR=str(t / "state"), RHUBARB_SSH_WAIT="5")

        def rb(*args, inp=None):
            return subprocess.run([sys.executable, str(root / "tools" / "rhubarb_cli.py"), *args],
                                  env=env, input=inp, capture_output=True, text=True, timeout=120)

        sys.path.insert(0, str(root / "tools"))
        from rhubarb.locks import image_name
        from rhubarb.profiles import load_profile
        img = image_name(load_profile("kali-research"))
        vms = {img: False, img + "-unverified": False, "personal-vm": False}
        (t / "vms.json").write_text(json.dumps(vms))
        (t / "keychain.json").write_text(json.dumps({img: "ImagePassword123"}))

        r = rb("new", "work-1", "--profile", "kali-research", "--no-rotate")
        check("new: clones the current verified image", r.returncode == 0 and "work-1" in json.loads((t / "vms.json").read_text()))
        r = rb("new", "work-1", "--profile", "kali-research", "--no-rotate")
        check("new: refuses an existing VM name", r.returncode != 0 and "already exists" in r.stderr)
        r = rb("new", "work-2", "--image", img + "-unverified", "--no-rotate")
        check("new: refuses an -unverified image", r.returncode != 0)
        r = rb("new", "rbt-sneaky", "--profile", "kali-research")
        check("new: refuses rbt- clone names", r.returncode != 0 and "reserved" in r.stderr)
        r = rb("new", "work-2", "--profile", "kali-research")  # rotation attempted, ssh unavailable
        rec = json.loads((t / "state" / "clones" / "work-2.json").read_text())
        check("new: SSH key refused -> stops at once, keeps inherited password, says why",
              r.returncode == 0 and rec["password_account"] == img and "refused our key" in r.stderr)
        r = rb("list")
        check("list: shows both clones as current", r.stdout.count("current") == 2 and "work-2" in r.stdout)
        r = rb("images")
        check("images: marks the image current with 2 clones", "current" in r.stdout and " 2" in r.stdout)
        r = rb("rm", "personal-vm", "--yes")
        check("rm: refuses VMs it didn't create", r.returncode != 0 and "personal-vm" in json.loads((t / "vms.json").read_text()))
        r = rb("rm", img, "--yes")
        check("rm: refuses built images", r.returncode != 0 and img in json.loads((t / "vms.json").read_text()))
        r = rb("reset", "work-1", "--no-rotate")
        check("reset: re-clones and keeps the record", r.returncode == 0 and (t / "state" / "clones" / "work-1.json").exists())
        r = rb("rm", "work-1", inp="n\n")
        check("rm: asks for confirmation and aborts on no", "work-1" in json.loads((t / "vms.json").read_text()))
        r = rb("rm", "work-1", "--yes")
        check("rm: deletes VM + record", r.returncode == 0 and "work-1" not in json.loads((t / "vms.json").read_text())
              and not (t / "state" / "clones" / "work-1.json").exists())
        check("event log records new/reset/rm", all(e in (t / "state" / "events.log").read_text()
                                                    for e in ("\tnew\t", "\treset\t", "\trm\t")))


def test_rotation_script() -> None:
    """ROTATE_SCRIPT against a simulated guest (macOS, NixOS, Linux paths)."""
    from rhubarb.hostops import ROTATE_SCRIPT
    for os_name in ("Linux", "NixOS", "Darwin"):
        with tempfile.TemporaryDirectory() as tmp:
            t = Path(tmp)
            b = t / "bin"
            b.mkdir()
            (t / "pw").write_text("OldPass111")
            (t / "hash").mkdir()
            _stub(b, "id", 'print("admin")\n')
            _stub(b, "uname", f'print("{"Darwin" if os_name == "Darwin" else "Linux"}")\n')
            _stub(b, "sudo", """
pw = Path(os.environ["STUB"]) / "pw"; a = sys.argv[1:]
if a == ["-k"]: sys.exit(0)
# -S: read the password line byte by byte, like real sudo, leaving the rest of stdin
# for the command it runs (buffered readline() would swallow it)
buf = b""
while (c := os.read(0, 1)) not in (b"", b"\\n"): buf += c
line = buf.decode()
if line != pw.read_text(): sys.exit(1)
a = a[3:]                                        # drop -S -p ''
if a == ["-v"]: sys.exit(0)
os.execvp(a[0], a)
""")
            _stub(b, "chpasswd", """
pw = Path(os.environ["STUB"]) / "pw"; enc = "-e" in sys.argv
user, _, val = sys.stdin.readline().rstrip("\\n").partition(":")
assert user == "admin"
pw.write_text(val.removeprefix("$y$stub$") if enc else val)
""")
            _stub(b, "mkpasswd", 'print("$y$stub$" + sys.stdin.read())\n')
            _stub(b, "dscl", """
pw = Path(os.environ["STUB"]) / "pw"; a = sys.argv[1:]
if a[1] == "-passwd" and a[3] == pw.read_text(): pw.write_text(a[4])
else: sys.exit(1)
""")
            script = ROTATE_SCRIPT.replace("/var/lib/rhubarbtart", str(t / "hash"))
            script = script.replace("[ -e /etc/NIXOS ]", "[ -n \"$NIXOS\" ]")
            env = dict(os.environ, PATH=f"{b}:{os.environ['PATH']}", STUB=str(t),
                       NIXOS="1" if os_name == "NixOS" else "")
            res = subprocess.run(["bash", "-c", script], input="OldPass111\nNewPass222\n",
                                 env=env, capture_output=True, text=True, timeout=60)
            ok = res.returncode == 0 and "ROTATED" in res.stdout and (t / "pw").read_text() == "NewPass222"
            if os_name == "NixOS":
                h = t / "hash" / "password.hash"
                ok = ok and h.read_text().strip() == "$y$stub$NewPass222" and (os.stat(h).st_mode & 0o777) == 0o600
            check(f"rotation ({os_name}): new password set, old rejected, proven via sudo -v", ok)
            (t / "pw").write_text("OldPass111")
            res = subprocess.run(["bash", "-c", script], input="WrongPass9\nNewPass222\n",
                                 env=env, capture_output=True, text=True, timeout=60)
            check(f"rotation ({os_name}): wrong current password fails closed",
                  res.returncode != 0 and (t / "pw").read_text() == "OldPass111")


# ---- api.py: pure logic (no tart / keychain / Mac) -----------------------------------------
# _profile_of / _current_image derive the image identity and current/outdated status; the
# expected values below are reasoned from the naming contract and the committed locks/, not
# read back from what this code returned. Locks present: kali/nixos/goldengate; tahoe has none.

def test_api_pure() -> None:
    from rhubarb import api
    from rhubarb.clones import IMAGE_RE
    from rhubarb.common import VerifyError

    def raises(label, exc, fn):
        try:
            fn()
            check(label, False)
        except exc:
            check(label, True)

    # _profile_of: rbt-PROFILE-SHA(12 hex) -> profile id; None for anything else.
    check("_profile_of maps a well-formed kali image to its profile",
          api._profile_of("rbt-kali-research-0123456789ab") == "kali-research")
    check("_profile_of maps a well-formed nixos image to its profile",
          api._profile_of("rbt-nixos-research-fedcba987654") == "nixos-research")
    check("_profile_of: matches IMAGE_RE but no such profile -> None",
          api._profile_of("rbt-nosuchprofile-0123456789ab") is None)
    check("_profile_of: not an rbt-PROFILE-SHA name -> None",
          api._profile_of("personal-vm") is None)
    check("_profile_of: uppercase (non-hex) sha is not an image name -> None",
          api._profile_of("rbt-kali-research-ABCDEF012345") is None)
    check("_profile_of: sha of wrong length -> None",
          api._profile_of("rbt-kali-research-0123456789") is None)

    # _current_image: the rbt- name the profile's committed lock produces; None if no lock.
    cur = api._current_image("kali-research")
    check("_current_image returns a well-formed rbt- image for a locked profile",
          isinstance(cur, str) and bool(IMAGE_RE.match(cur)) and cur.startswith("rbt-kali-research-"))
    check("_current_image is consistent with _profile_of (round-trip)",
          api._profile_of(cur) == "kali-research")
    check("_current_image: profile with no committed lock (tahoe-research) -> None",
          api._current_image("tahoe-research") is None)
    check("_current_image: unknown / invalid profile id -> None",
          api._current_image("no-such-profile") is None)

    # current/outdated status derivation (rhubarb images / list): an image is 'current' iff it
    # equals what its profile's lock now produces, else 'outdated'.
    def status(image):
        return "current" if api._current_image(api._profile_of(image)) == image else "outdated"
    fake = "rbt-kali-research-000000000000"
    check("derivation precondition: fabricated image differs from the current one", fake != cur)
    check("status derivation: the current image reads 'current'", status(cur) == "current")
    check("status derivation: same profile, stale sha reads 'outdated'", status(fake) == "outdated")

    # provenance(vm): read-only parse of out/<vm>.provenance.json, typed errors, path guard.
    saved_root = api.ROOT
    try:
        with tempfile.TemporaryDirectory() as tmp:
            api.ROOT = Path(tmp)
            out = Path(tmp, "out")
            out.mkdir()
            ref = {
                "vm": "work-1", "profile": "kali-research", "built_at": "2026-01-02T03:04:05Z",
                "git_commit": "deadbeefcafe", "git_dirty": True,
                "toolchain": {"tart": "2.0.0", "packer": "1.14.2"},
                "tart_vm": {"Name": "work-1", "Running": False},
                "inputs_sha256": "a" * 64, "lock_sha256": "b" * 64,
                "lock": {"schema": 2, "profile": "kali-research"},
                "build_files": {"packer/kali/kali.pkr.hcl": "c" * 64},
            }
            (out / "work-1.provenance.json").write_text(json.dumps(ref))
            p = api.provenance("work-1")
            check("provenance maps every field of a valid record verbatim",
                  (p.vm, p.profile, p.built_at, p.git_commit, p.git_dirty) ==
                  ("work-1", "kali-research", "2026-01-02T03:04:05Z", "deadbeefcafe", True)
                  and p.toolchain == {"tart": "2.0.0", "packer": "1.14.2"}
                  and p.tart_vm == {"Name": "work-1", "Running": False}
                  and p.inputs_sha256 == "a" * 64 and p.lock_sha256 == "b" * 64
                  and p.lock == {"schema": 2, "profile": "kali-research"}
                  and p.build_files == {"packer/kali/kali.pkr.hcl": "c" * 64})

            nullable = dict(ref, vm="nogit", git_commit=None, git_dirty=False, tart_vm=None)
            (out / "nogit.provenance.json").write_text(json.dumps(nullable))
            p2 = api.provenance("nogit")
            check("provenance passes through null git_commit / tart_vm",
                  p2.git_commit is None and p2.tart_vm is None and p2.git_dirty is False)

            (out / "bad.provenance.json").write_text("{ not valid json")
            raises("provenance: malformed JSON -> VerifyError", VerifyError,
                   lambda: api.provenance("bad"))
            (out / "partial.provenance.json").write_text(json.dumps({"vm": "partial"}))
            raises("provenance: record missing required keys -> VerifyError", VerifyError,
                   lambda: api.provenance("partial"))
            (out / "list.provenance.json").write_text("[1, 2, 3]")
            raises("provenance: non-object record -> VerifyError", VerifyError,
                   lambda: api.provenance("list"))
            raises("provenance: no record for the vm -> FileNotFoundError", FileNotFoundError,
                   lambda: api.provenance("ghost"))
            for bad_vm in ("a/b", "..", ".", "", "a\\b"):
                raises(f"provenance: rejects unsafe vm name {bad_vm!r} before any file access",
                       VerifyError, lambda v=bad_vm: api.provenance(v))
    finally:
        api.ROOT = saved_root


def test_engagements() -> None:
    """Stage 1A: engagements.py loads + strictly validates a scope manifest into a frozen
    Engagement. The committed engagements/demo.json is the reference; the rejection cases are
    variants of it written to a temp engagements/ dir (no tart / keychain / network)."""
    from rhubarb import engagements
    from rhubarb.common import VerifyError

    # The committed demo manifest parses to the expected frozen dataclass.
    eng = engagements.load_engagement("demo")
    check("demo: id/label/operator/authorization map verbatim",
          (eng.id, eng.label, eng.operator, eng.authorization)
          == ("demo", "Demo lab engagement", "errantpacket",
              "RoE-2026-DEMO-001 (internal lab authorization, non-production)"))
    check("demo: 2 ranges reference existing profiles with their counts/prefix",
          [(r.profile, r.count, r.prefix) for r in eng.ranges]
          == [("kali-research", 2, "demo-offense"), ("goldengate-research", 1, None)])
    check("demo: targets stored by shape (hosts/cidrs/domains/urls)",
          eng.targets.hosts == ("10.20.0.10",) and eng.targets.cidrs == ("10.20.0.0/24",)
          and eng.targets.domains == ("lab.internal",) and eng.targets.urls == ("https://lab.internal/ctf",))
    check("demo: agent_budget stored (agents/wall-clock/spend/kill-time)",
          eng.agent_budget.agents == ("recon", "exploit")
          and eng.agent_budget.wall_clock_minutes == 240
          and eng.agent_budget.max_spend_usd == 25.0
          and eng.agent_budget.kill_time == "2026-09-27T18:00:00Z")
    check("demo: evidence policy stored (vault + retention)",
          eng.evidence.vault == "vault://demo" and eng.evidence.retention_days == 90)
    check("Engagement is frozen (immutable)", isinstance(eng, engagements.Engagement)
          and _is_frozen(eng, "label", "tampered"))
    check("demo id is in list_engagements()", "demo" in engagements.list_engagements())

    # Rejection cases: write a manifest into a temp engagements/ dir and load it by stem.
    base = json.loads((engagements.ENGAGEMENTS / "demo.json").read_text())
    saved = engagements.ENGAGEMENTS
    try:
        with tempfile.TemporaryDirectory() as tmp:
            engagements.ENGAGEMENTS = Path(tmp)

            def refused(label, eid, mutate):
                man = json.loads(json.dumps(base))   # deep copy
                man["id"] = eid
                mutate(man)
                (Path(tmp) / f"{eid}.json").write_text(json.dumps(man))
                try:
                    engagements.load_engagement(eid)
                    check(f"rejects {label}", False)
                except VerifyError:
                    check(f"rejects {label}", True)

            refused("an unknown top-level key", "unknown-top",
                    lambda m: m.update(scope="everything"))
            refused("an unknown range profile", "bad-profile",
                    lambda m: m["ranges"].__setitem__(0, {"profile": "no-such-profile", "count": 1}))
            refused("a range count of 0", "bad-count",
                    lambda m: m["ranges"][0].__setitem__("count", 0))
            refused("a missing authorization", "no-auth",
                    lambda m: m.pop("authorization"))
            refused("an empty authorization", "empty-auth",
                    lambda m: m.__setitem__("authorization", "   "))
            refused("an empty ranges list", "no-ranges",
                    lambda m: m.__setitem__("ranges", []))

            # id != filename: valid body, wrong stem.
            (Path(tmp) / "mismatch.json").write_text(json.dumps(base))  # base["id"] == "demo"
            try:
                engagements.load_engagement("mismatch")
                check("rejects id != filename", False)
            except VerifyError:
                check("rejects id != filename", True)
    finally:
        engagements.ENGAGEMENTS = saved


def test_engagement_ops() -> None:
    """Stage 1B: api.provision/teardown group a range set by engagement tag, over a MOCKED
    core (api.new / api.rm / clones() / hostops.local_vms) — no tart / keychain / network.
    The committed engagements/demo.json is the reference (kali x2 'demo-offense', goldengate x1)."""
    from rhubarb import api
    from rhubarb.common import VerifyError

    # -- provision: resolve each range's verified image (mocked), clone count copies via
    #    api.new, tagged with the engagement id, named from prefix / <eid>-<profile>. --
    calls = []

    def fake_new(name, profile=None, image=None, rotate=True, progress=None, engagement=None):
        calls.append((name, image, engagement))
        return api.NewResult(name=name, image=image, profile="p", rotated=True,
                             password_mode="unique", note=None)

    orig = (api._source_image, api.new, api.hostops.local_vms, api._clones.all_records)
    try:
        api._source_image = lambda profile, image: (f"rbt-{profile}-000000000000", {"id": profile})
        api.new = fake_new
        api.hostops.local_vms = lambda: {}
        api._clones.all_records = lambda: ([], [])

        res = api.provision("demo")
        check("provision names ranges from prefix / <eid>-<profile>, suffixed only when count>1",
              [c.name for c in res.created]
              == ["demo-offense-1", "demo-offense-2", "demo-goldengate-research"])
        check("provision tags every clone with the engagement id",
              all(eng == "demo" for _n, _img, eng in calls) and len(calls) == 3)
        check("provision clones each range's resolved verified image",
              [img for _n, img, _e in calls]
              == ["rbt-kali-research-000000000000", "rbt-kali-research-000000000000",
                  "rbt-goldengate-research-000000000000"])
        check("provision reports nothing skipped when no names collide", res.skipped == [])

        # A name already taken (a VM or a record) is skipped, never re-created.
        calls.clear()
        api.hostops.local_vms = lambda: {"demo-offense-1": {}}
        res2 = api.provision("demo")
        check("provision skips an existing name and still creates the rest",
              res2.skipped == ["demo-offense-1"]
              and [c.name for c in res2.created] == ["demo-offense-2", "demo-goldengate-research"])

        # An unbuilt/unverified range image refuses the whole provision (nothing created).
        calls.clear()
        api.hostops.local_vms = lambda: {}
        def refuse(profile, image):
            raise VerifyError(f"{profile} not built")
        api._source_image = refuse
        try:
            api.provision("demo")
            check("provision refuses when a range image is not built/verified", False)
        except VerifyError:
            check("provision refuses when a range image is not built/verified", len(calls) == 0)
    finally:
        api._source_image, api.new, api.hostops.local_vms, api._clones.all_records = orig

    # -- teardown: remove exactly the clones tagged to the engagement, via api.rm. --
    removed = []
    tagged = api.CloneList(clones=[
        api.Clone(name="demo-offense-1", profile="kali-research", family="kali",
                  image="rbt-kali-research-000000000000", state="stopped", freshness="current",
                  password_mode="unique", password_account="demo-offense-1", enrollments=[],
                  created_at="2026-01-02T00:00:00Z", engagement="demo"),
        api.Clone(name="other", profile="kali-research", family="kali",
                  image="rbt-kali-research-000000000000", state="stopped", freshness="current",
                  password_mode="unique", password_account="other", enrollments=[],
                  created_at="2026-01-02T00:00:00Z", engagement="acme"),
        api.Clone(name="adhoc", profile="kali-research", family="kali",
                  image="rbt-kali-research-000000000000", state="stopped", freshness="current",
                  password_mode="unique", password_account="adhoc", enrollments=[],
                  created_at="2026-01-02T00:00:00Z", engagement=None),
    ], problems=[])
    orig2 = (api.clones, api.rm)
    try:
        api.clones = lambda: tagged
        api.rm = lambda name: (removed.append(name)
                               or api.RemoveResult(name=name, image="rbt-x", keychain_deleted=True))
        out = api.teardown("demo")
        check("teardown removes exactly the engagement's clones (not other/ad-hoc)",
              out == ["demo-offense-1"] and removed == ["demo-offense-1"])
        check("teardown is idempotent (an engagement with no tagged clones removes nothing)",
              api.teardown("no-such") == [])
        check("engagement_clones returns only the tag's clones",
              [c.name for c in api.engagement_clones("acme")] == ["other"])
    finally:
        api.clones, api.rm = orig2


def test_cli_progress_stream() -> None:
    """#19: cli.py still streams the core's live milestones for `rhubarb new`/`reset` (it
    hands api a progress callback that prints each line), and api's default (no callback)
    stays a pure no-op so existing callers/tests are unaffected."""
    import io
    from contextlib import redirect_stderr

    from rhubarb import api, cli

    # cli.cmd_new must pass api.new a live progress callback and stream what it emits.
    seen = {}

    def fake_new(name, profile=None, image=None, rotate=True, progress=None):
        seen["callable"] = callable(progress)
        if progress:
            progress(f"cloned rbt-x -> {name}")
            progress(f"{name}: unique password set (rotated)")
        return api.NewResult(name=name, image="rbt-x", profile=profile or "kali-research",
                             rotated=rotate, password_mode="unique" if rotate else "inherited",
                             note=None)

    orig_new = api.new
    api.new = fake_new
    try:
        args = types.SimpleNamespace(name="work-1", profile="kali-research",
                                     image=None, no_rotate=False)
        err = io.StringIO()
        with redirect_stderr(err):
            cli.cmd_new(args)
        streamed = err.getvalue()
    finally:
        api.new = orig_new

    check("cli.cmd_new hands api.new a live progress callback (#19)", seen.get("callable") is True)
    check("cli.cmd_new streams the core's milestones live to stderr as they arrive",
          "cloned rbt-x -> work-1" in streamed and "unique password set" in streamed)

    # api's default is silent: _emit with no callback is a pure no-op (why existing tests
    # and callers are unaffected); with a callback it delivers each line unchanged.
    delivered = []
    api._emit(None, "dropped when no frontend supplied a sink")
    api._emit(delivered.append, "delivered verbatim")
    check("api._emit is a no-op without a callback (default behavior unchanged)",
          delivered == ["delivered verbatim"])


def test_hostops_resilience() -> None:
    """#16/#21: local_vms() falls back to a disk-free enumeration when tart list hits the
    disk-lock (a running VM); other failures still raise; delete_vm() works by name. tart +
    _fs_vms are stubbed in-process (no real VMs)."""
    from rhubarb import hostops
    from rhubarb.common import VerifyError
    CP = subprocess.CompletedProcess
    BUSY = ("Error: Failed to retrieve info for disk image: The operation couldn't be "
            "completed. Resource temporarily unavailable")
    orig_tart, orig_fs = hostops.tart, hostops._fs_vms
    try:
        # happy path: tart list works -> its JSON is used, no fallback
        good = json.dumps([{"Source": "local", "Name": "vm1", "Running": False}])
        hostops.tart = lambda *a, **k: CP(a, 0, good, "")
        hostops._fs_vms = lambda: {"SHOULD_NOT": {}}
        check("local_vms uses tart list when it works", hostops.local_vms().get("vm1") is not None)

        # disk-busy (a running VM) -> fall back to the disk-free enumeration, don't raise
        hostops.tart = lambda *a, **k: CP(a, 1, "", BUSY)
        hostops._fs_vms = lambda: {"fsvm": {"Name": "fsvm", "Source": "local",
                                            "Running": True, "State": "running"}}
        vms = hostops.local_vms()
        check("local_vms falls back to disk-free enumeration on a running VM", vms.get("fsvm", {}).get("Running") is True)

        # a different failure still raises (not masked by the fallback)
        hostops.tart = lambda *a, **k: CP(a, 1, "", "Error: some other failure")
        try:
            hostops.local_vms()
            other_ok = False
        except VerifyError:
            other_ok = True
        check("local_vms raises on a non-disk-lock failure", other_ok)

        # delete_vm by name: present / already-gone / real error
        hostops.tart = lambda *a, **k: CP(a, 0, "", "")
        d_ok = hostops.delete_vm("x") is True
        hostops.tart = lambda *a, **k: CP(a, 1, "", 'Error: VM "x" does not exist')
        d_absent = hostops.delete_vm("x") is False
        hostops.tart = lambda *a, **k: CP(a, 1, "", "Error: still running")
        try:
            hostops.delete_vm("x")
            d_err = False
        except VerifyError:
            d_err = True
        check("delete_vm handles present / absent / error by name", d_ok and d_absent and d_err)
    finally:
        hostops.tart, hostops._fs_vms = orig_tart, orig_fs


def test_fs_vms() -> None:
    """#21: _fs_vms enumerates local VM dirs with running state from the process table, no disk read."""
    from rhubarb import hostops
    orig_dir, orig_procs = hostops.VMS_DIR, hostops._tart_run_procs
    with tempfile.TemporaryDirectory() as tmp:
        vms = Path(tmp)
        for n in ("gate-c", "rbt-goldengate-research-abc123", "web-1"):
            (vms / n).mkdir()
        (vms / "not-a-dir.txt").write_text("x")
        hostops.VMS_DIR = vms
        hostops._tart_run_procs = lambda: [("111", "gate-c")]  # only gate-c is running
        try:
            out = hostops._fs_vms()
            ok = (set(out) == {"gate-c", "rbt-goldengate-research-abc123", "web-1"}
                  and out["gate-c"]["Running"] is True and out["gate-c"]["State"] == "running"
                  and out["web-1"]["Running"] is False and out["web-1"]["State"] == "stopped"
                  and all(v["Source"] == "local" for v in out.values()))
            check("_fs_vms lists VM dirs with running state, no disk read", ok)
        finally:
            hostops.VMS_DIR, hostops._tart_run_procs = orig_dir, orig_procs


def test_shutdown_reaps_boot_process() -> None:
    """#17: shutdown() must terminate the detached boot process even when stop_vm() fails."""
    from rhubarb import hostops
    from rhubarb.common import VerifyError

    class FakeProc:
        def __init__(self):
            self.alive = True
            self.terminated = self.killed = False
        def poll(self):
            return None if self.alive else 0
        def wait(self, timeout=None):
            if self.alive:
                raise subprocess.TimeoutExpired("tart", timeout)
            return 0
        def terminate(self):
            self.terminated = True
            self.alive = False  # dies on SIGTERM
        def kill(self):
            self.killed = True
            self.alive = False

    orig_stop, orig_reap = hostops.stop_vm, hostops.reap_run
    hostops.stop_vm = lambda *_a, **_k: (_ for _ in ()).throw(VerifyError("stop failed / listing degraded"))
    reaped = []
    hostops.reap_run = lambda n: reaped.append(n)
    try:
        p = FakeProc()
        hostops.shutdown("vm1", p, timeout=0)  # stop raises, wait times out -> terminate
        check("shutdown terminates the boot process when stop_vm fails", p.terminated and not p.alive)

        # a process that has already exited is left alone (no terminate/kill)
        gone = FakeProc()
        gone.alive = False
        hostops.shutdown("vm1", gone, timeout=0)
        check("shutdown no-ops on an already-exited process", not gone.terminated and not gone.killed)

        # shutdown always sweeps for a stray detached `tart run <name>` (#18), even proc=None
        hostops.shutdown("vm1")
        check("shutdown always reaps stray tart run by name", reaped == ["vm1", "vm1", "vm1"])
    finally:
        hostops.stop_vm, hostops.reap_run = orig_stop, orig_reap


def test_reap_run() -> None:
    """#18: reap_run kills only `tart run <name>` whose final arg is the exact name."""
    from rhubarb import hostops
    CP = subprocess.CompletedProcess
    killed = []
    cmds = {"100": "/x/tart run --no-graphics web-1",
            "101": "/x/tart run web-10",                       # substring — must NOT match web-1
            "102": "/x/tart run --rosetta=rosetta web-1",
            "103": "/x/some-other-process web-1"}              # not a tart run — must NOT match

    def fake_run(argv, **k):
        if argv[:2] == ["pgrep", "-f"]:
            return CP(argv, 0, "100\n101\n102\n103\n", "")
        if argv[0] == "ps":
            return CP(argv, 0, cmds.get(argv[argv.index("-p") + 1], "") + "\n", "")
        if argv[0] == "kill":
            killed.append(argv[1])
            return CP(argv, 0, "", "")
        return CP(argv, 0, "", "")

    orig = hostops.subprocess.run
    hostops.subprocess.run = fake_run
    try:
        hostops.reap_run("web-1")
        check("reap_run kills exact-name matches only (web-1, not web-10)", sorted(killed) == ["100", "102"])
    finally:
        hostops.subprocess.run = orig


if __name__ == "__main__":
    for t in (test_ed25519, test_nar, test_dpkg, test_records, test_cli_lifecycle,
              test_rotation_script, test_api_pure, test_engagements, test_engagement_ops,
              test_cli_progress_stream, test_hostops_resilience, test_shutdown_reaps_boot_process,
              test_reap_run, test_fs_vms):
        print(t.__name__)
        t()
    if FAILS:
        sys.exit(f"{len(FAILS)} test(s) failed")
    print("all rhubarb self-tests passed")
