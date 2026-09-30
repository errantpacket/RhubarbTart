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


def _raises(fn, exc) -> bool:
    try:
        fn()
        return False
    except exc:
        return True


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
                   RHUBARB_STATE_DIR=str(t / "state"), RHUBARB_SSH_WAIT="5", RHUBARB_SSH_DENIED_RETRY="0")

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
        r = rb("rm", "work-1", inp="")  # closed stdin (script/agent/CI): clean refusal, not EOFError (#47)
        check("rm: no answer on stdin -> refuses cleanly, non-zero, nothing removed",
              r.returncode != 0 and "--yes" in r.stderr and "Traceback" not in r.stderr
              and "work-1" in json.loads((t / "vms.json").read_text()))
        r = rb("rm", "work-1", "--yes")
        check("rm: deletes VM + record", r.returncode == 0 and "work-1" not in json.loads((t / "vms.json").read_text())
              and not (t / "state" / "clones" / "work-1.json").exists())
        check("event log records new/reset/rm", all(e in (t / "state" / "events.log").read_text()
                                                    for e in ("\tnew\t", "\treset\t", "\trm\t")))


def test_confirm_prompt() -> None:
    """The shared [y/N] prompt behind rm and engagement teardown (#47)."""
    import builtins
    from rhubarb import cli
    from rhubarb.common import VerifyError
    orig = builtins.input
    try:
        for ans, want in (("y\n", True), ("YES", True), ("n", False), ("", False)):
            builtins.input = lambda _p, a=ans: a
            check(f"_confirm({ans!r}) -> {want}", cli._confirm("? ") is want)

        def eof(_p):
            raise EOFError
        builtins.input = eof
        try:
            cli._confirm("? ")
            check("_confirm: EOF refuses with VerifyError", False)
        except VerifyError as e:
            check("_confirm: EOF refuses with VerifyError naming --yes", "--yes" in str(e))
    finally:
        builtins.input = orig


def test_pgp_ed25519() -> None:
    """Pure-Python OpenPGP Ed25519 verifier used to pin GnuPG's own tarballs (#54), against a
    real signed file (gnupg.org swdb.lst, signed by Werner Koch's dist key) and tamperings."""
    from rhubarb import pgp_ed25519 as p
    from rhubarb.common import KEYS, VerifyError
    werner, niibe = "6DAA6E64A76D2840571B4902528897B826403ADA", "AC8E115BF73E2D8D47FA9908E98E9B2D19C6C8BD"
    td = Path(__file__).resolve().parent / "testdata"
    data, sig = (td / "gnupg-swdb.lst").read_bytes(), (td / "gnupg-swdb.lst.sig").read_bytes()
    keys = (KEYS / "gnupg-release-signing.asc").read_text()

    def rejects(label, fn, needle):
        try:
            fn()
            check(label, False)
        except VerifyError as e:
            check(label, needle in str(e))

    check("key file: Ed25519 primaries parsed, fingerprints computed",
          {werner, niibe} <= set(p.ed25519_primary_keys(keys)))
    check("real swdb.lst signature verifies by the pinned key",
          p.verify_detached(data, sig, keys, {werner, niibe}) == {werner})
    tampered = bytearray(data)
    tampered[100] ^= 1
    rejects("tampered data rejected", lambda: p.verify_detached(bytes(tampered), sig, keys, {werner}), "NOT valid")
    bad_s = bytearray(sig)
    bad_s[-3] ^= 1  # inside the s MPI: digest/prefix still match, only the curve check can catch it
    rejects("corrupted signature value rejected by the Ed25519 check",
            lambda: p.verify_detached(data, bytes(bad_s), keys, {werner}), "NOT valid")
    rejects("signer not pinned -> no valid signature",
            lambda: p.verify_detached(data, sig, keys, {niibe}), "no valid signature")
    sha1 = bytearray(sig)
    sha1[sig.index(b"\x04\x00\x16") + 3] = 2  # hash algorithm byte -> SHA-1
    rejects("SHA-1 signature refused", lambda: p.verify_detached(data, bytes(sha1), keys, {werner}), "unsupported")
    rejects("partial-length packet refused",
            lambda: p.verify_detached(data, b"\xc2\xe0" + sig[2:], keys, {werner}), "partial")
    rejects("non-signature packet refused",
            lambda: p.verify_detached(data, b"\xcb\x01\x00", keys, {werner}), "unexpected packet")


def test_toolchain_gpg() -> None:
    """On macOS resolve uses only the bootstrap-built gpg, never one from PATH (#54)."""
    from rhubarb import gpg
    from rhubarb.common import VerifyError
    orig = (gpg.platform.system, gpg.TOOLCHAIN_GPG)
    try:
        gpg.platform.system = lambda: "Darwin"
        with tempfile.TemporaryDirectory() as d:
            fake = Path(d) / "gpg"
            gpg.TOOLCHAIN_GPG = fake
            try:
                gpg.gpg_binary()
                check("darwin without toolchain gpg -> refuses (no PATH fallback)", False)
            except VerifyError as e:
                check("darwin without toolchain gpg -> refuses (no PATH fallback)", "bootstrap" in str(e))
            fake.write_text("")
            check("darwin uses the toolchain gpg", gpg.gpg_binary() == str(fake))
    finally:
        gpg.platform.system, gpg.TOOLCHAIN_GPG = orig


def test_profile_usernames() -> None:
    """Kali refuses Debian-installer reserved usernames at profile load, not 14 min into an
    install; other families keep them (macOS/NixOS use `admin`). (#25)"""
    from rhubarb import profiles
    from rhubarb.common import VerifyError
    check("kali-research uses a non-reserved username",
          profiles.load_profile("kali-research")["username"] == "kaliresearcher")
    orig = profiles.PROFILES
    with tempfile.TemporaryDirectory() as d:
        profiles.PROFILES = Path(d)
        try:
            for pid, base in (("k-admin", "kali-rolling"), ("n-admin", "nixos-26.05")):
                (Path(d) / f"{pid}.json").write_text(json.dumps(
                    {"id": pid, "base": base, "username": "admin", "packages": []}))
            try:
                profiles.load_profile("k-admin")
                check("kali + reserved username rejected", False)
            except VerifyError as e:
                check("kali + reserved username rejected, names the installer", "reserved" in str(e))
            check("nixos may still use admin", profiles.load_profile("n-admin")["username"] == "admin")
        finally:
            profiles.PROFILES = orig
    check("reserved list covers the default username", "admin" in profiles.DI_RESERVED_USERS)
    check("reserved list only holds names USER_RE accepts",
          all(profiles.USER_RE.match(u) for u in profiles.DI_RESERVED_USERS))


def test_packages_tsv_readers() -> None:
    """Every guest install script's packages.tsv `read` line, run as-is by bash on a row with an
    extra (future) column: no column may leak into the package/app or signed fields. (#26: Kali
    read 5 of 6 columns and asked apt for 'zaproxy<TAB>1'.)"""
    import re
    root = Path(__file__).resolve().parent.parent
    row = "zap\tdistro\t-\t-\tzaproxy\t1\tFUTURE\n"
    lines = []
    for script in sorted((root / "guest").glob("*/install.sh")):
        lines += [(script, m) for m in re.findall(r"^\s*(while IFS=\$'\\t' read -r [^;]+);\s*do",
                                                   script.read_text(), re.M)]
    check("found the packages.tsv readers (kali + 2x macos)", len(lines) == 3)
    for script, loop in lines:
        read = loop.removeprefix("while ").strip()
        res = subprocess.run(["/bin/bash", "-c", f'{read}; printf "%s|%s" "${{pkg-}}${{app-}}" "${{signed-}}${{_signed-}}"'],
                             input=row, capture_output=True, text=True)
        check(f"{script.parent.name}/install.sh: package/app field is exactly 'zaproxy', signed '1'",
              res.stdout == "zaproxy|1")


def test_sshd_T_normalization() -> None:
    """The seal compares `sshd -T` lines exactly. OpenSSH >= 10.5 prints CamelCase keywords, so
    each finalize lowercases field 1 only; values must still compare exactly. Runs each script's
    actual awk filter. (#57)"""
    import re
    root = Path(__file__).resolve().parent.parent
    filters = []
    for script in ("guest/kali/finalize.sh", "guest/macos/finalize.sh"):
        m = re.search(r"sshd -T[^|\n]*\| awk '([^']*)'", (root / script).read_text())
        check(f"{script}: sshd -T output is keyword-normalized", m is not None)
        if m:
            filters.append((script, m.group(1)))
    for script, prog in filters:
        def norm(text, prog=prog):
            return subprocess.run(["awk", prog], input=text, capture_output=True, text=True).stdout.splitlines()
        new = norm("PasswordAuthentication no\nAllowUsers kaliresearcher\nAuthenticationMethods publickey\n")
        old = norm("passwordauthentication no\nallowusers kaliresearcher\n")
        check(f"{script}: 10.5 CamelCase and older lowercase both match",
              "passwordauthentication no" in new and "allowusers kaliresearcher" in new
              and "authenticationmethods publickey" in new and "passwordauthentication no" in old)
        check(f"{script}: values stay exact (yes != no, user case kept)",
              "passwordauthentication no" not in norm("PasswordAuthentication yes\n")
              and norm("AllowUsers KaliResearcher\n") == ["allowusers KaliResearcher"])


def test_kali_nopasswd_allowlist() -> None:
    """The Kali seal allows exactly one NOPASSWD rule (#59: OpenVAS's _gvm) and nothing else.
    Runs finalize.sh's actual allowlist loop against planted sudoers trees."""
    import re
    root = Path(__file__).resolve().parent.parent
    src = (root / "guest/kali/finalize.sh").read_text()
    m = re.search(r"^GVM_SUDOERS=.*?\n(GVM_RULE=.*?\n)(while IFS= read -r hit; do\n.*?\ndone <<<[^\n]*\n)", src, re.S | re.M)
    check("kali finalize: NOPASSWD allowlist loop found", m is not None)
    if not m:
        return
    cases = {
        "exact allowed rule": ("ospd-openvas", "_gvm ALL = NOPASSWD: /usr/sbin/openvas\n", True),
        "allowed rule, tabs/extra spaces": ("ospd-openvas", "_gvm\tALL  =  NOPASSWD:\t/usr/sbin/openvas \n", True),
        "comment mentioning NOPASSWD": ("ospd-openvas", "# NOPASSWD is scary\n_gvm ALL = NOPASSWD: /usr/sbin/openvas\n", True),
        "kali-grant-root rule": ("kali-grant-root", "%kali-trusted   ALL=(ALL:ALL) NOPASSWD: ALL\n", False),
        "allowed rule widened": ("ospd-openvas", "_gvm ALL = NOPASSWD: /usr/sbin/openvas, /bin/sh\n", False),
        "allowed rule, other user": ("ospd-openvas", "kaliresearcher ALL = NOPASSWD: /usr/sbin/openvas\n", False),
        "allowed rule in another file": ("zz-sneaky", "_gvm ALL = NOPASSWD: /usr/sbin/openvas\n", False),
        "second rule appended": ("ospd-openvas", "_gvm ALL = NOPASSWD: /usr/sbin/openvas\nkaliresearcher ALL=(ALL) NOPASSWD: ALL\n", False),
    }
    for label, (name, content, allowed) in cases.items():
        with tempfile.TemporaryDirectory() as d:
            (Path(d) / "sudoers.d").mkdir()
            (Path(d) / "sudoers").write_text("root ALL=(ALL:ALL) ALL\n@includedir /etc/sudoers.d\n")
            (Path(d) / "sudoers.d" / name).write_text(content)
            loop = (m.group(2).replace("/etc/sudoers.d", f"{d}/sudoers.d").replace("/etc/sudoers ", f"{d}/sudoers "))
            script = (f'set -euo pipefail\ndie() {{ echo "DIE: $*"; exit 1; }}\n'
                      f'GVM_SUDOERS={d}/sudoers.d/ospd-openvas\n{m.group(1)}{loop}echo PASS\n')
            res = subprocess.run(["/bin/bash", "-c", script], capture_output=True, text=True)
            ok = res.stdout.strip().endswith("PASS")
            check(f"nopasswd allowlist: {label} -> {'allowed' if allowed else 'refused'}", ok is allowed)


def test_build_cleanup_trap() -> None:
    """build.sh's actual EXIT-trap cleanup, under macOS /bin/bash 3.2 with set -euo pipefail and
    an already-exited server PID: the script must still exit 0 and $WORK must be gone. (#61)"""
    import re
    root = Path(__file__).resolve().parent.parent
    m = re.search(r"^cleanup\(\) \{(?:[^\n]*\}\n|\n.*?^\}\n)", (root / "scripts/build.sh").read_text(), re.S | re.M)
    check("build.sh: cleanup() found", m is not None)
    if not m:
        return
    for label, pid in (("server already exited", "$(sh -c 'echo $$')"), ("no server (macOS)", "")):
        with tempfile.TemporaryDirectory() as d:
            work = Path(d) / "work"
            work.mkdir()
            (work / "password").write_text("x")
            script = (f'set -euo pipefail\nWORK={work}\nSERVER_PID="{pid}"\n{m.group(0)}'
                      f'trap cleanup EXIT\necho body-done\n')
            res = subprocess.run(["/bin/bash", "-c", script], capture_output=True, text=True)
            check(f"build.sh cleanup ({label}): exit 0 and $WORK removed",
                  res.returncode == 0 and "body-done" in res.stdout and not work.exists())


def test_content_addressed_cache() -> None:
    """Artifacts live at artifacts/<sha256>/<file>: two builds behind one file name (Chrome's
    unversioned URL) coexist; a legacy flat file is adopted only if its hash matches. (#67)"""
    import hashlib
    import resolve
    with tempfile.TemporaryDirectory() as d:
        cache = Path(d)
        old, new = b"chrome .58", b"chrome .93"
        e_old = {"file": "GoogleChrome.pkg", "sha256": hashlib.sha256(old).hexdigest()}
        e_new = {"file": "GoogleChrome.pkg", "sha256": hashlib.sha256(new).hexdigest()}
        check("same file name, different builds -> distinct cache paths",
              resolve.cached(cache, e_old) != resolve.cached(cache, e_new))
        (cache / "GoogleChrome.pkg").write_bytes(new)  # legacy flat file holding the .93 build
        p_old = resolve.cached(cache, e_old)
        check("legacy file with another hash is left alone (not adopted for .58)",
              not p_old.exists() and (cache / "GoogleChrome.pkg").exists())
        p_new = resolve.cached(cache, e_new)
        check("legacy file with the matching hash is adopted (moved, no re-download)",
              p_new.read_bytes() == new and not (cache / "GoogleChrome.pkg").exists()
              and p_new == cache / e_new["sha256"] / "GoogleChrome.pkg")
    # verify must not leave a wrong download under a hash it doesn't have (#67)
    src = (Path(__file__).resolve().parent / "resolve.py").read_text()
    body = src[src.index("def cmd_verify"):src.index("def ", src.index("def cmd_verify") + 5)]
    check("verify deletes a download whose hash doesn't match the lock",
          "path.unlink()" in body.split("differs from lock")[0].rsplit("fetch(e, path)", 1)[-1])


def test_chrome_update_policy() -> None:
    """macOS: install.sh's Keystone policy parses and sets UpdateDefault 2 (manual only) for both
    scopes, and the seal and smoke test assert that same file and value. (#29)"""
    import plistlib
    import re
    root = Path(__file__).resolve().parent.parent
    install = (root / "guest/macos/install.sh").read_text()
    m = re.search(r'cat > "\$KEYSTONE_POLICY" <<\'EOF\'\n(.*?)\nEOF\n', install, re.S)
    check("install.sh writes the Keystone policy", m is not None)
    if m:
        pol = plistlib.loads(m.group(1).encode())["updatePolicies"]
        check("policy: UpdateDefault 2 for global and com.google.Chrome",
              pol["global"]["UpdateDefault"] == 2 and pol["com.google.Chrome"]["UpdateDefault"] == 2)
    path = "/Library/Managed Preferences/com.google.Keystone.plist"
    fin = (root / "guest/macos/finalize.sh").read_text()
    smoke = (root / "scripts/smoke-test.sh").read_text()
    check("seal asserts the policy (path, both scopes, value 2)",
          path in fin and "for scope in global com.google.Chrome" in fin and '== 2 ]]' in fin)
    check("smoke test checks the policy from the clone",
          path in smoke and ":updatePolicies:com.google.Chrome:UpdateDefault" in smoke)
    check("finalize's residue cleanup doesn't delete the policy",
          not re.search(r"rm [^\n]*com\.google\.Keystone", fin))


def test_publish_offline_signing() -> None:
    """Publishing never involves public Sigstore services (#32): the signing config names none,
    every sign/attest is key-based with that config, and cosign's egress is pinned to the
    registry (it otherwise fetches Sigstore's public TUF root)."""
    import re
    root = Path(__file__).resolve().parent.parent
    cfg = json.loads((root / "config/cosign/signing-config-offline.json").read_text())
    check("offline signing config: no CA/OIDC/transparency-log/timestamp services",
          all(cfg.get(k) == [] for k in ("caUrls", "oidcUrls", "rekorTlogUrls", "tsaUrls")))
    pub = (root / "scripts/publish.sh").read_text()
    code = "\n".join(ln for ln in pub.splitlines() if not ln.lstrip().startswith("#"))
    signs = re.findall(r"cosign (?:sign|attest) [^\n]*", code)
    check("publish.sh signs and attests", len(signs) == 2)
    check("every sign/attest is key-based with the offline config",
          all('"${common[@]}"' in s for s in signs)
          and "--key env://COSIGN_PRIVATE_KEY" in code and '--signing-config "$SIGNING_CONFIG"' in code)
    check("never keyless, never the deprecated --tlog-upload", "--tlog-upload" not in code
          and "--identity-token" not in code and "--oidc" not in code)
    wrapper = re.search(r"^cosign\(\) \{\n(.*?)\n\}\n", code, re.S | re.M)
    body = wrapper.group(1) if wrapper else ""
    check("cosign is wrapped: egress pinned to the registry host",
          all(x in body for x in ("HTTPS_PROXY=http://127.0.0.1:9", 'NO_PROXY="$host"',
                                  'no_proxy="$host"', "command cosign")))
    check("private key reaches cosign via env only, never argv/file",
          "COSIGN_PRIVATE_KEY=" in code and not re.search(r"--key [^ ]*\.key", code))


def test_stacked_clones() -> None:
    """#31: `rhubarb new --from-registry` (macOS only) verifies the published copy, stacks the
    clone on it, records its base blob, flags a missing base, and releases an unused base."""
    from rhubarb import api
    from rhubarb import clones as cl
    from rhubarb.common import VerifyError
    ref = "127.0.0.1:5780/rhubarbtart/tahoe-research@sha256:" + "a" * 64
    digest = "sha256:" + "b" * 64
    mac = {"id": "tahoe-research", "family": "macos", "username": "admin", "options": {}}
    kali = {"id": "kali-research", "family": "kali", "username": "kaliresearcher", "options": {}}
    img = "rbt-tahoe-research-edbb1008a6c0"

    def raises(label, fn, needle):
        try:
            fn()
            check(label, False)
        except VerifyError as e:
            check(label, needle in str(e))

    good = cl.new_record("st-1", mac, img, base={"ref": ref, "disk_digest": digest})
    check("record with a registry base validates", cl._validate(dict(good), "st-1")["base"]["ref"] == ref)
    old = dict(good)
    del old["base"]
    check("pre-#31 record (no base key) still loads, base=None", cl._validate(old, "st-1")["base"] is None)
    raises("record with a malformed base ref is rejected",
           lambda: cl._validate({**good, "base": {"ref": "evil.example/x", "disk_digest": digest}}, "st-1"),
           "invalid base")

    calls = []
    saved = (api.hostops.tart, api.hostops.delete_vm, api.hostops.VMS_DIR, api.TART_CONTENT,
             api._published_ref, api._verify_published, os.environ.get("RHUBARB_STATE_DIR"))
    with tempfile.TemporaryDirectory() as d:
        os.environ["RHUBARB_STATE_DIR"] = f"{d}/state"
        api.hostops.VMS_DIR = Path(d) / "vms"
        api.TART_CONTENT = Path(d) / "content"
        api.TART_CONTENT.mkdir()
        api.hostops.tart = lambda *a, **k: calls.append(list(a)) or subprocess.CompletedProcess(a, 0, "", "")
        api.hostops.delete_vm = lambda n: calls.append(["delete_vm", n]) or True
        try:
            raises("NixOS/Kali refused before touching tart",
                   lambda: api._clone("st-k", "rbt-kali-research-0ff4b8e78398", kali, False, from_registry=True),
                   "only for macOS")
            check("... and no tart call was made", calls == [])

            api._published_ref = lambda image: (_ for _ in ()).throw(VerifyError(f"{image} is not published"))
            raises("unpublished image refused", lambda: api._clone("st-1", img, mac, False, from_registry=True),
                   "not published")

            api._published_ref = lambda image: (ref, "127.0.0.1:5780")
            api._verify_published = lambda r, reg: (_ for _ in ()).throw(VerifyError("failed verification"))
            raises("verification failure refuses to clone",
                   lambda: api._clone("st-1", img, mac, False, from_registry=True), "failed verification")
            check("... and nothing was cloned", not any(c[:1] == ["clone"] for c in calls))

            api._verify_published = lambda r, reg: None
            raises("unreadable stacked manifest -> error",
                   lambda: api._clone("st-1", img, mac, False, from_registry=True), "manifest")
            check("... and the half-made clone is deleted", ["delete_vm", "st-1"] in calls)

            calls.clear()
            (api.hostops.VMS_DIR / "st-1").mkdir(parents=True)
            (api.hostops.VMS_DIR / "st-1" / "manifest.json").write_text(json.dumps({"layers": [
                {"annotations": {}}, {"annotations": {api._DISK_DIGEST_KEY: digest}},
                {"annotations": {api._DISK_DIGEST_KEY: digest}}]}))
            api._clone("st-1", img, mac, False, from_registry=True)
            check("stacked clone: tart clone --insecure --stacked <ref> <name>",
                  ["clone", "--insecure", "--stacked", ref, "st-1"] in calls)
            rec = cl.load("st-1")
            check("record carries the base ref + disk digest", rec["base"] == {"ref": ref, "disk_digest": digest})
            check("base blob absent -> base_present False", api.base_present(rec) is False)
            (api.TART_CONTENT / ("b" * 64)).write_text("x")
            check("base blob present -> base_present True", api.base_present(rec) is True)

            other = cl.new_record("st-2", mac, img, base={"ref": ref, "disk_digest": digest})
            cl.save(other)
            check("base kept while another clone is stacked on it", api._release_base(rec["base"], "st-1") is False
                  and (api.TART_CONTENT / ("b" * 64)).exists())
            cl.delete("st-2")
            calls.clear()
            check("last clone gone -> base released (OCI entry + blob)",
                  api._release_base(rec["base"], "st-1") is True and not (api.TART_CONTENT / ("b" * 64)).exists()
                  and ["delete", ref] in calls)
        finally:
            (api.hostops.tart, api.hostops.delete_vm, api.hostops.VMS_DIR, api.TART_CONTENT,
             api._published_ref, api._verify_published, prev) = saved
            if prev is None:
                os.environ.pop("RHUBARB_STATE_DIR", None)
            else:
                os.environ["RHUBARB_STATE_DIR"] = prev


def test_github_release_resolver() -> None:
    """github-release (#83): pins a release asset by GitHub's sha256 digest and refuses anything
    weaker or unexpected. GitHub's API is faked."""
    from rhubarb import packages
    from rhubarb.common import VerifyError
    v = {"repo": "juice-shop/juice-shop", "asset": "juice-shop-{version}_node22_linux_arm64.tgz"}
    name = "juice-shop-20.2.0_node22_linux_arm64.tgz"
    url = f"https://github.com/juice-shop/juice-shop/releases/download/v20.2.0/{name}"

    def release(**asset):
        return {"tag_name": "v20.2.0", "assets": [{"name": name, "browser_download_url": url,
                                                    "digest": "sha256:" + "a" * 64, "size": 1, **asset}]}
    orig = packages.get_json
    try:
        packages.get_json = lambda u: release()
        e = packages.github_release(v)
        check("github-release: version, file, sha256 from GitHub's digest",
              e["version"] == "20.2.0" and e["file"] == name and e["sha256"] == "a" * 64
              and e["hash_sources"] == ["github-release-digest"])
        for label, asset, needle in (
                ("asset without a digest refused", {"digest": None}, "no sha256 digest"),
                ("asset from an unexpected host refused", {"browser_download_url": "https://evil.example/x.tgz"},
                 "unexpected asset URL"),
                ("missing asset refused", {"name": "other.tgz"}, "has no asset")):
            packages.get_json = lambda u, a=asset: release(**a)
            try:
                packages.github_release(v)
                check(f"github-release: {label}", False)
            except VerifyError as err:
                check(f"github-release: {label}", needle in str(err))
        try:
            packages.github_release({**v, "repo": "not a repo"})
            check("github-release: malformed repo refused", False)
        except VerifyError:
            check("github-release: malformed repo refused", True)
    finally:
        packages.get_json = orig


def test_reset_keeps_engagement() -> None:
    """#89: `rhubarb reset` re-clones under the same name and must keep the clone's engagement
    tag, or the clone silently leaves its engagement and teardown orphans it."""
    from rhubarb import api
    from rhubarb import clones as cl
    from rhubarb.locks import image_name
    from rhubarb.profiles import load_profile
    prof = load_profile("kali-research")
    img = image_name(prof)
    seen = {}
    saved = (api._destroy, api._clone, api.hostops.local_vms, os.environ.get("RHUBARB_STATE_DIR"))
    with tempfile.TemporaryDirectory() as d:
        os.environ["RHUBARB_STATE_DIR"] = d
        try:
            cl.save(cl.new_record("jsl-attacker", prof, img, engagement="juiceshop-lab"))
            api._destroy = lambda rec: False
            api.hostops.local_vms = lambda: {img: {}}

            def fake_clone(name, image, p, rotate, progress=None, engagement=None, from_registry=False):
                seen.update(name=name, engagement=engagement)
                return False, None
            api._clone = fake_clone
            api.reset("jsl-attacker", same_image=True, rotate=False)
            check("reset re-clones with the clone's engagement tag", seen == {"name": "jsl-attacker",
                                                                          "engagement": "juiceshop-lab"})
        finally:
            api._destroy, api._clone, api.hostops.local_vms, prev = saved
            if prev is None:
                os.environ.pop("RHUBARB_STATE_DIR", None)
            else:
                os.environ["RHUBARB_STATE_DIR"] = prev


def test_guest_sync() -> None:
    """#91: `tart stop` cuts a Linux guest's shutdown short, so rhubarb flushes the guest first:
    the rotation script syncs before reporting success, and sync_guest runs `sync` over SSH,
    best-effort (never raises, never blocks a stop)."""
    from rhubarb import hostops
    body = hostops.ROTATE_SCRIPT
    check("rotation script syncs before reporting ROTATED",
          "\nsync\n" in body and body.index("\nsync\n") < body.index("echo ROTATED"))
    CP = subprocess.CompletedProcess
    calls = []
    orig = (hostops.tart, hostops.subprocess.run)
    try:
        hostops.tart = lambda *a, **k: CP(a, 1, "", "no IP")
        check("sync_guest: no IP -> False, no ssh attempted", hostops.sync_guest("c1", "admin") is False)
        hostops.tart = lambda *a, **k: CP(a, 0, "192.168.64.9\n", "")
        hostops.subprocess.run = lambda argv, **k: calls.append(argv) or CP(argv, 0, "", "")
        ok = hostops.sync_guest("c1", "admin")
        check("sync_guest: runs `sync` over the pinned SSH", ok is True and calls[-1][-1] == "sync"
              and "HostKeyAlias=c1" in calls[-1] and "admin@192.168.64.9" in calls[-1])

        def slow(argv, **k):
            raise subprocess.TimeoutExpired(argv, 20)
        hostops.subprocess.run = slow
        check("sync_guest: a hung guest -> False, doesn't raise", hostops.sync_guest("c1", "admin") is False)
    finally:
        hostops.tart, hostops.subprocess.run = orig


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
    # A throwaway profile that can never have a committed lock (a real profile gains one the
    # day it's resolved, which silently broke this check once).
    from rhubarb import profiles as _profiles
    orig_profiles = _profiles.PROFILES
    with tempfile.TemporaryDirectory() as d:
        (Path(d) / "nolock-test.json").write_text(json.dumps(
            {"id": "nolock-test", "base": "nixos-26.05", "packages": []}))
        _profiles.PROFILES = Path(d)
        try:
            check("_current_image: profile with no committed lock -> None",
                  api._current_image("nolock-test") is None)
        finally:
            _profiles.PROFILES = orig_profiles
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
    # A sibling <id>.herdr.json (herdr config, #108) is NOT an engagement.
    check("list_engagements ignores .herdr.json configs and non-id stems",
          not any(e.endswith(".herdr") or "." in e for e in engagements.list_engagements()))

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


def test_engagement_links() -> None:
    """#30: manifest links are range-relative, name declared ports only, and become one
    `ssh -R 127.0.0.1:P:<target>:P` tunnel per source clone (tart/keychain/ssh mocked)."""
    from rhubarb import api, engagements
    from rhubarb.common import VerifyError

    eng = engagements.load_engagement("juiceshop-lab")
    check("juiceshop-lab links attacker -> target on 3000",
          eng.links == (engagements.Link(src="jsl-attacker", dst="jsl-target", ports=(3000,)),))
    check("demo (no links key) loads with no links", engagements.load_engagement("demo").links == ())

    base = json.loads((engagements.ENGAGEMENTS / "juiceshop-lab.json").read_text())
    saved = engagements.ENGAGEMENTS
    try:
        with tempfile.TemporaryDirectory() as tmp:
            engagements.ENGAGEMENTS = Path(tmp)

            def refused(label, eid, mutate):
                man = json.loads(json.dumps(base))
                man["id"] = eid
                mutate(man)
                (Path(tmp) / f"{eid}.json").write_text(json.dumps(man))
                try:
                    engagements.load_engagement(eid)
                    check(f"links: rejects {label}", False)
                except VerifyError:
                    check(f"links: rejects {label}", True)

            link = lambda m: m["links"][0]   # noqa: E731
            refused("an unknown link key", "l-key", lambda m: link(m).update(proto="udp"))
            refused("a link to no range", "l-dst", lambda m: link(m).update(to="nowhere"))
            refused("a link from a range to itself", "l-self", lambda m: link(m).update(to="jsl-attacker"))
            refused("a port the target profile doesn't declare", "l-undecl",
                    lambda m: link(m).update(ports=[22]))
            refused("an empty port list", "l-empty", lambda m: link(m).update(ports=[]))
            refused("a non-integer port", "l-str", lambda m: link(m).update(ports=["3000"]))
            refused("a duplicate port", "l-dup", lambda m: link(m).update(ports=[3000, 3000]))
            refused("a target range of count > 1", "l-count",
                    lambda m: m["ranges"][1].update(count=2))
            refused("the same port linked twice onto one source", "l-twice",
                    lambda m: m["links"].append(dict(link(m))))
            refused("two ranges with the same clone-name stem", "l-stem",
                    lambda m: m["ranges"][1].update(prefix="jsl-attacker"))
    finally:
        engagements.ENGAGEMENTS = saved

    # connect_plan: needs both clones provisioned (tagged) and running (an IP).
    def clone(name, profile, family, eng="juiceshop-lab"):
        return api.Clone(name=name, profile=profile, family=family, image="rbt-x", state="running",
                         freshness="current", password_mode="unique", password_account=name,
                         enrollments=[], created_at="2026-01-02T00:00:00Z", engagement=eng)
    recs = {"jsl-attacker": {"name": "jsl-attacker", "family": "kali", "username": "kaliresearcher"},
            "jsl-target": {"name": "jsl-target", "family": "nixos", "username": "admin"}}
    ips = {"jsl-attacker": "192.168.64.5", "jsl-target": "192.168.64.6"}
    orig = (api.clones, api._clones.load, api.hostops.vm_ip)
    try:
        api._clones.load = lambda name: recs[name]
        api.hostops.vm_ip = lambda name, family, wait=180: ips[name]
        api.clones = lambda: api.CloneList(clones=[clone("jsl-attacker", "kali-research", "kali"),
                                                   clone("jsl-target", "juiceshop-target", "nixos")],
                                           problems=[])
        plan = api.connect_plan("juiceshop-lab")
        argv = plan[0].argv if plan else []
        check("connect_plan: one tunnel into the attacker, to the target's address",
              [(t.src, t.dst, t.dst_ip, t.ports) for t in plan]
              == [("jsl-attacker", "jsl-target", "192.168.64.6", (3000,))])
        check("connect_plan: remote-forwards to the attacker's loopback only",
              "127.0.0.1:3000:192.168.64.6:3000" in argv
              and argv[argv.index("127.0.0.1:3000:192.168.64.6:3000") - 1] == "-R")
        check("connect_plan: no remote shell, fails if the port can't be bound, pinned host key",
              "-N" in argv and "ExitOnForwardFailure=yes" in argv and "HostKeyAlias=jsl-attacker" in argv
              and argv[-1] == "kaliresearcher@192.168.64.5")

        api.clones = lambda: api.CloneList(clones=[clone("jsl-attacker", "kali-research", "kali")],
                                           problems=[])
        try:
            api.connect_plan("juiceshop-lab")
            check("connect_plan refuses an unprovisioned target", False)
        except VerifyError:
            check("connect_plan refuses an unprovisioned target", True)
        try:
            api.connect_plan("demo")
            check("connect_plan refuses an engagement without links", False)
        except VerifyError:
            check("connect_plan refuses an engagement without links", True)
    finally:
        api.clones, api._clones.load, api.hostops.vm_ip = orig

    # connect: when one tunnel drops, it reports it and closes the others (no orphaned ssh).
    orig_plan = api.connect_plan
    procs = []
    real_popen = subprocess.Popen
    try:
        api.connect_plan = lambda e: [
            api.Tunnel(src="a", dst="t", dst_ip="x", ports=(1,), argv=["sleep", "30"]),
            api.Tunnel(src="b", dst="t", dst_ip="x", ports=(1,), argv=["false"])]
        api.subprocess.Popen = lambda *a, **k: procs.append(real_popen(*a, **k)) or procs[-1]
        try:
            api.connect("juiceshop-lab")
            check("connect reports a dropped tunnel", False)
        except VerifyError as e:
            check("connect reports a dropped tunnel", "b -> t closed" in str(e))
        check("connect closes the remaining tunnels on the way out",
              len(procs) == 2 and all(p.poll() is not None for p in procs))
    finally:
        api.connect_plan, api.subprocess.Popen = orig_plan, real_popen


def test_cli_progress_stream() -> None:
    """#19: cli.py still streams the core's live milestones for `rhubarb new`/`reset` (it
    hands api a progress callback that prints each line), and api's default (no callback)
    stays a pure no-op so existing callers/tests are unaffected."""
    import io
    from contextlib import redirect_stderr

    from rhubarb import api, cli

    # cli.cmd_new must pass api.new a live progress callback and stream what it emits.
    seen = {}

    def fake_new(name, profile=None, image=None, rotate=True, progress=None, from_registry=False):
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
                                     image=None, no_rotate=False, from_registry=False)
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


def test_ssh_client() -> None:
    """ssh_args ignores ~/.ssh/config; wait_for_ssh keeps waiting only on boot-time errors and
    fails fast (with ssh's message) on client-side ones instead of reporting 'unreachable'. (#46)"""
    from rhubarb import hostops
    from rhubarb.common import VerifyError
    CP = subprocess.CompletedProcess

    argv = hostops.ssh_args("vm1", "admin", "192.168.64.9")
    check("ssh_args ignores the operator's ssh config", argv[1:3] == ["-F", "/dev/null"])
    check("ssh_args leaves identity to agent/defaults by default", "IdentitiesOnly=yes" not in argv)
    os.environ["RHUBARB_SSH_IDENTITY"] = "/k/id_ed25519"
    try:
        argv = hostops.ssh_args("vm1", "admin", "192.168.64.9")
    finally:
        del os.environ["RHUBARB_SSH_IDENTITY"]
    check("RHUBARB_SSH_IDENTITY pins the key", "/k/id_ed25519" in argv and "IdentitiesOnly=yes" in argv)

    def run_with(stderrs):
        seq = iter(stderrs)
        calls = []

        def fake_run(a, **k):
            calls.append(a)
            err = next(seq)
            return CP(a, 0 if err is None else 255, "", err or "")
        orig_run, orig_sleep = hostops.subprocess.run, hostops.time.sleep
        hostops.subprocess.run, hostops.time.sleep = fake_run, lambda s: None
        try:
            return hostops.wait_for_ssh("vm1", "admin", "192.168.64.9", timeout=60), len(calls)
        finally:
            hostops.subprocess.run, hostops.time.sleep = orig_run, orig_sleep

    refused = "ssh: connect to host 192.168.64.9 port 22: Connection refused"
    timeout = "ssh: connect to host 192.168.64.9 port 22: Operation timed out"
    check("boot-time errors keep waiting until ok", run_with([refused, timeout, None]) == ("ok", 3))
    deny = "admin@x: Permission denied (publickey)."
    check("a key refusal is final only after 3 in a row (#88)", run_with([refused, deny, deny, deny]) == ("denied", 4))
    check("a transient early-boot refusal recovers (#88)", run_with([refused, deny, None]) == ("ok", 3))
    check("a refusal streak resets on a boot-time error", run_with([deny, refused, deny, deny, deny])[0] == "denied")
    try:
        run_with([refused, "Load key \"/k\": No such file\n$SSH_SK_PROVIDER did not resolve; disabling"])
        check("client-side ssh error fails fast", False)
    except VerifyError as e:
        check("client-side ssh error fails fast with ssh's message", "SSH_SK_PROVIDER" in str(e))


def test_ssh_provenance() -> None:
    """Provenance records the image's SSH mode; rotation refuses an SSH-disabled image up front
    instead of booting it and waiting out two SSH timeouts. (#46)"""
    import resolve
    from rhubarb import api
    pub = "ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIOMqqnkVzrm0SdG6UOoqKLsabgH5C9okWi0dh2l9GKJl test"
    with tempfile.TemporaryDirectory() as d:
        keys = Path(d) / "authorized_keys"
        keys.write_text(f"# comment\n{pub}\n")
        on = resolve.ssh_record(str(keys), "192.168.64.1")
        keys.write_text("")
        off = resolve.ssh_record(str(keys), "192.168.64.1")
    check("ssh_record: keys => enabled with fingerprint",
          on["enabled"] and len(on["key_fingerprints"]) == 1 and on["key_fingerprints"][0].startswith("SHA256:")
          and on["from"] == "192.168.64.1")
    check("ssh_record: no keys => disabled", off == {"enabled": False, "key_fingerprints": [], "from": ""})
    check("ssh_record: no file => disabled", resolve.ssh_record(None, "")["enabled"] is False)

    booted = []
    orig = (api.provenance, api.hostops.start_vm)
    api.hostops.start_vm = lambda *a, **k: booted.append(a)
    try:
        api.provenance = lambda vm: types.SimpleNamespace(ssh={"enabled": False})
        rotated, note = api._rotate({"name": "c1", "image": "rbt-p-000000000000"})
        check("ssh-disabled image: no boot, clear note",
              rotated is False and not booted and "SSH disabled" in note)
        api.provenance = lambda vm: types.SimpleNamespace(ssh=None)
        check("pre-#46 record => unknown", api._image_ssh_enabled("rbt-p-000000000000") is None)

        def missing(vm):
            raise FileNotFoundError(vm)
        api.provenance = missing
        check("no record => unknown", api._image_ssh_enabled("rbt-p-000000000000") is None)
    finally:
        api.provenance, api.hostops.start_vm = orig


def test_evidence_store() -> None:
    """#85: the host evidence journal is hash-chained over content-addressed items, and
    verify() reports an altered entry, a removed entry and an altered item."""
    from rhubarb import evidence

    e1 = evidence.append("lab-x", "lifecycle", {"event": "provision"})
    e2 = evidence.append("lab-x", "exec", {"command": "id", "exit_code": 0}, clone="c1",
                         blobs={"stdout": b"uid=1000\n", "stderr": b""})
    check("journal chains: seq 1, 2 and prev links", (e1["seq"], e2["seq"]) == (1, 2)
          and e1["prev"] == evidence.GENESIS and e2["prev"] == e1["hash"])
    check("output stored content-addressed",
          evidence.item_path("lab-x", e2["items"]["stdout"]).read_bytes() == b"uid=1000\n")
    rep = evidence.verify("lab-x")
    check("clean journal verifies", rep.problems == [] and rep.entries == 2 and rep.head == e2["hash"])
    d = evidence.store_dir("lab-x")
    check("store is 0700, journal 0600", (d.stat().st_mode & 0o777) == 0o700
          and ((d / "journal.jsonl").stat().st_mode & 0o777) == 0o600)

    journal = d / "journal.jsonl"
    original = journal.read_bytes()
    journal.write_bytes(original.replace(b'"command":"id"', b'"command":"ls"'))
    check("verify catches an altered entry",
          any("altered" in p for p in evidence.verify("lab-x").problems))
    journal.write_bytes(original.split(b"\n", 1)[1])
    check("verify catches a removed entry",
          any("seq" in p or "chain" in p for p in evidence.verify("lab-x").problems))
    journal.write_bytes(original)
    item = evidence.item_path("lab-x", e2["items"]["stdout"])
    item.write_bytes(b"uid=0\n")
    check("verify catches an altered item",
          any("content altered" in p for p in evidence.verify("lab-x").problems))
    try:
        evidence.store_dir("../escape")
        check("rejects an engagement id that is a path", False)
    except Exception:
        check("rejects an engagement id that is a path", True)


def test_evidence_exec_collect() -> None:
    """#85: api.exec journals engagement commands (not ad-hoc ones); api.collect parses the
    guest's tar stream without extracting, refuses unsafe members, and dedupes on re-collect."""
    import io
    import tarfile

    from rhubarb import api, evidence

    recs = {"jsl-attacker": {"name": "jsl-attacker", "family": "kali", "username": "kr",
                             "profile": "kali-research", "engagement": "juiceshop-lab"},
            "adhoc": {"name": "adhoc", "family": "kali", "username": "kr",
                      "profile": "kali-research", "engagement": None}}

    def tar_bytes():
        buf = io.BytesIO()
        with tarfile.open(fileobj=buf, mode="w") as tf:
            def add(name, data=b"", kind=tarfile.REGTYPE, link=""):
                ti = tarfile.TarInfo(name)
                ti.type, ti.linkname, ti.size = kind, link, len(data)
                tf.addfile(ti, io.BytesIO(data) if data else None)
            add(".", kind=tarfile.DIRTYPE)
            add("./scans", kind=tarfile.DIRTYPE)
            add("./scans/nmap.txt", b"3000/tcp open")
            add("./findings.md", b"# SQLi in login")
            add("./link", kind=tarfile.SYMTYPE, link="/etc/passwd")
            add("../../etc/evil", b"x")
            add("/abs", b"x")
        return buf.getvalue()

    stream = tar_bytes()

    class FakeProc:
        def __init__(self, argv, **_k):
            self.stdout = io.BufferedReader(io.BytesIO(stream))
            self.stderr = io.BytesIO(b"")
        def wait(self):
            return 0

    ran = []
    orig = (api._clones.load, api.hostops.vm_ip, api.subprocess.run, api.subprocess.Popen,
            api.clones, api._ground_truth)
    try:
        api._clones.load = lambda n: recs[n]
        api.hostops.vm_ip = lambda n, f, wait=180: "192.168.64.5"
        api.subprocess.run = lambda argv, **k: (ran.append(argv) or
                                                types.SimpleNamespace(stdout=b"uid=1000(kr)\n", stderr=b"",
                                                                      returncode=0))
        res = api.exec("jsl-attacker", "id")
        journal = evidence.entries("juiceshop-lab")
        check("exec runs the command over the pinned ssh", ran and ran[-1][-1] == "id"
              and "HostKeyAlias=jsl-attacker" in ran[-1])
        check("exec in an engagement is journaled with its output",
              res.evidence_seq == 1 and journal[-1]["kind"] == "exec"
              and journal[-1]["data"]["command"] == "id"
              and evidence.item_path("juiceshop-lab", journal[-1]["items"]["stdout"]).read_bytes()
              == b"uid=1000(kr)\n")
        check("exec on an ad-hoc clone is not journaled", api.exec("adhoc", "id").evidence_seq is None)

        api.subprocess.Popen = FakeProc
        api._ground_truth = lambda e, rec, ip: []
        running = api.Clone(name="jsl-attacker", profile="kali-research", family="kali", image="rbt-x",
                            state="running", freshness="current", password_mode="unique",
                            password_account="jsl-attacker", enrollments=[],
                            created_at="2026-01-02T00:00:00Z", engagement="juiceshop-lab")
        stopped = api.Clone(**{**running.__dict__, "name": "jsl-target", "state": "stopped"})
        api.clones = lambda: api.CloneList(clones=[running, stopped], problems=[])
        first = {r.name: r for r in api.collect("juiceshop-lab")}
        a = first["jsl-attacker"]
        check("collect records regular files by relative path",
              sorted(a.new) == ["findings.md", "scans/nmap.txt"])
        check("collect refuses symlinks, .. and absolute paths",
              len(a.skipped) == 3 and any("not a regular file" in s for s in a.skipped)
              and sum("unsafe path" in s for s in a.skipped) == 2)
        check("collect reports a stopped clone instead of starting it",
              first["jsl-target"].note and "stopped" in first["jsl-target"].note)
        arts = [e for e in evidence.entries("juiceshop-lab") if e["kind"] == "artifact"]
        check("artifact content is stored and hashed on the host",
              any(evidence.item_path("juiceshop-lab", e["items"]["content"]).read_bytes()
                  == b"# SQLi in login" for e in arts))
        again = {r.name: r for r in api.collect("juiceshop-lab")}["jsl-attacker"]
        check("re-collect journals nothing new for unchanged files",
              again.new == [] and sorted(again.unchanged) == ["findings.md", "scans/nmap.txt"])
        check("the whole run verifies", evidence.verify("juiceshop-lab").problems == [])
    finally:
        (api._clones.load, api.hostops.vm_ip, api.subprocess.run, api.subprocess.Popen,
         api.clones, api._ground_truth) = orig


def test_vault_seal_verify() -> None:
    """#86: seal writes a signed, sealed, portable bundle over the evidence store; verify checks
    the signature and every hash and catches tampering. cosign is faked (pure-logic test)."""
    from pathlib import Path

    from rhubarb import evidence, vault
    from rhubarb.common import VerifyError

    # A tiny fake "signature": sha256 of root.json's bytes. Proves the wiring and that verify
    # re-runs the signer over root.json; the real cosign round-trip is checked on hardware.
    def fake_sign(root: Path, bundle: Path) -> None:
        bundle.write_text(_h(root.read_bytes()))

    def fake_verify(root: Path, bundle: Path, pub) -> None:
        if bundle.read_text() != _h(root.read_bytes()):
            raise VerifyError("bad signature")

    import hashlib
    def _h(b): return hashlib.sha256(b).hexdigest()

    # Build a small evidence store, then seal it.
    evidence.append("juiceshop-lab", "lifecycle", {"event": "provision",
                    "created": {"a": "rbt-kali-research-000000000000"}})
    evidence.append("juiceshop-lab", "exec", {"command": "id", "exit_code": 0}, clone="a",
                    blobs={"stdout": b"uid=1000\n", "stderr": b""})

    with tempfile.TemporaryDirectory() as out:
        out = Path(out)
        v = vault.seal("juiceshop-lab", out, fake_sign, "2026-09-30T02:00:00+00:00", cosign_version="v3.1.3")
        check("seal writes root.json, a signature bundle and the public-key placeholder is optional",
              (v / "root.json").is_file() and (v / "root.bundle.json").is_file()
              and (v / "journal.jsonl").is_file())
        root = json.loads((v / "root.json").read_text())
        check("root.json commits to chain head, entries and every item",
              root["entries"] == 2 and root["chain_head"] == evidence.verify("juiceshop-lab").head
              and len(root["items"]) == 2 and root["engagement"] == "juiceshop-lab")
        check("seal copies every referenced item", all((v / "items" / d).is_file() for d in root["items"]))
        check("sealed tree is read-only", (v.stat().st_mode & 0o200) == 0
              and ((v / "root.json").stat().st_mode & 0o222) == 0)
        rep = vault.verify(v, fake_verify)
        check("a fresh vault verifies (signature + hashes)", rep.signed and rep.problems == []
              and rep.entries == 2 and rep.items == 2)

        # Tamper: flip an item's content -> hash mismatch AND signature (root unchanged) still ok,
        # so the hash check is what catches an item swap.
        os.chmod(v / "items", 0o700)
        item = next(iter(root["items"]))
        os.chmod(v / "items" / item, 0o600)
        (v / "items" / item).write_bytes(b"tampered\n")
        bad = vault.verify(v, fake_verify)
        check("verify catches an altered item", any("altered" in p for p in bad.problems))

        # Tamper: edit root.json -> signature fails.
        os.chmod(v, 0o700)
        os.chmod(v / "root.json", 0o600)
        (v / "root.json").write_text(json.dumps({**root, "entries": 999}))
        bad2 = vault.verify(v, fake_verify)
        check("verify catches an edited root.json via the signature",
              not bad2.signed and any("signature" in p for p in bad2.problems))

    # Refusals: no evidence, and a broken chain.
    with tempfile.TemporaryDirectory() as out:
        try:
            vault.seal("demo", Path(out), fake_sign, "2026-09-30T02:00:00+00:00")
            check("seal refuses an engagement with no evidence", False)
        except VerifyError:
            check("seal refuses an engagement with no evidence", True)
        j = evidence.store_dir("juiceshop-lab") / "journal.jsonl"
        orig = j.read_bytes()
        j.write_bytes(orig.replace(b'"command":"id"', b'"command":"XX"'))
        try:
            vault.seal("juiceshop-lab", Path(out), fake_sign, "2026-09-30T02:01:00+00:00")
            check("seal refuses when the chain does not verify", False)
        except VerifyError:
            check("seal refuses when the chain does not verify", True)
        j.write_bytes(orig)


def test_control_plane_service() -> None:
    """#104: the read-only control-plane service routes to the typed core over a 0600 Unix
    socket, serializes dataclasses to JSON, and maps core errors to HTTP status (core mocked;
    no tart/keychain)."""
    import threading
    from pathlib import Path

    from rhubarb import api, service
    from rhubarb.common import VerifyError

    img = api.Image(name="rbt-kali-research-000000000000", profile="kali-research", kind="image",
                    status="current", clones=1)
    clones = api.CloneList(clones=[api.Clone(
        name="jsl-attacker", profile="kali-research", family="kali", image="rbt-x", state="running",
        freshness="current", password_mode="unique", password_account="jsl-attacker",
        enrollments=[], created_at="2026-01-02T00:00:00Z", engagement="juiceshop-lab")], problems=[])

    def boom(vm):
        raise FileNotFoundError(f"no provenance for {vm}")

    saved = (api.images, api.clones, api.engagements, api.provenance,
             api.provision, api.teardown, api.exec, api.evidence_entries)
    with tempfile.TemporaryDirectory() as tmp:
        sock = Path(tmp) / "service.sock"
        api.images = lambda: [img]
        api.clones = lambda: clones
        api.engagements = lambda: ["juiceshop-lab", "demo"]
        api.provenance = boom
        srv = service.make_server(sock)
        t = threading.Thread(target=srv.serve_forever, daemon=True)
        t.start()
        try:
            check("socket is created 0600", (sock.stat().st_mode & 0o777) == 0o600)

            st, body = service.request(sock, "GET", "/health")
            check("GET /health -> 200 ok", st == 200 and body["ok"] and body["service"] == "rhubarb")

            st, body = service.request(sock, "GET", "/images")
            check("GET /images serializes the dataclass list",
                  st == 200 and body[0]["name"] == "rbt-kali-research-000000000000"
                  and body[0]["status"] == "current" and body[0]["clones"] == 1)

            st, body = service.request(sock, "GET", "/clones")
            check("GET /clones returns the CloneList shape",
                  st == 200 and body["clones"][0]["engagement"] == "juiceshop-lab"
                  and body["problems"] == [])

            st, body = service.request(sock, "GET", "/engagements")
            check("GET /engagements returns the ids", st == 200 and "demo" in body)

            st, body = service.request(sock, "GET", "/provenance/whatever")
            check("a core FileNotFoundError maps to 404", st == 404 and "no provenance" in body["error"])

            st, body = service.request(sock, "GET", "/nope")
            check("an unknown route is 404", st == 404 and "no such route" in body["error"])

            st, body = service.request(sock, "POST", "/clones")
            check("POST to a GET-only collection route is 405", st == 405)

            check("get_json raises on a non-200",
                  _raises(lambda: service.get_json(sock, "/nope"), VerifyError))

            # -- guarded actions (POST), core mocked --
            import base64
            calls = []
            api.provision = lambda eid: (calls.append(("provision", eid))
                                         or api.ProvisionResult(engagement=eid, created=[], skipped=["x"]))
            api.teardown = lambda eid, collect_first=True, progress=None: (
                calls.append(("teardown", eid, collect_first)) or ["a", "b"])
            api.exec = lambda name, command, timeout=None: (
                calls.append(("exec", name, command, timeout))
                or api.ExecResult(name=name, command=command, exit_code=0, stdout=b"\x00\xffOUT",
                                  stderr=b"", timed_out=False, evidence_seq=7))

            st, body = service.request(sock, "POST", "/engagements/demo/provision")
            check("POST provision runs the core call and returns its result",
                  st == 200 and body["skipped"] == ["x"] and ("provision", "demo") in calls)

            st, body = service.request(sock, "POST", "/engagements/demo/teardown",
                                       body={"collect_first": False})
            check("POST teardown passes a validated flag through",
                  st == 200 and body["removed"] == ["a", "b"] and ("teardown", "demo", False) in calls)

            st, body = service.request(sock, "POST", "/clones/jsl-attacker/exec",
                                       body={"command": "id"})
            check("POST exec returns the result with stdout base64-encoded (lossless)",
                  st == 200 and body["evidence_seq"] == 7
                  and base64.b64decode(body["stdout"]) == b"\x00\xffOUT")

            st, body = service.request(sock, "POST", "/clones/jsl-attacker/exec", body={})
            check("POST exec without a command is 400", st == 400 and "command" in body["error"])

            st, body = service.request(sock, "POST", "/clones/jsl-attacker/exec",
                                       body={"command": "id", "timeout": -1})
            check("POST exec with a bad timeout is 400", st == 400 and "timeout" in body["error"])

            st, _ = service.request(sock, "POST", "/images")
            check("POST to a GET-only route is 405", st == 405)

            st, _ = service.request(sock, "GET", "/engagements/demo/provision")
            check("GET on a POST-only route is 405", st == 405)

            # -- event stream (NDJSON tail of the evidence journal) --
            import threading
            import time as _time
            journal = [{"seq": 1, "kind": "lifecycle", "data": {"event": "provision"}},
                       {"seq": 2, "kind": "exec", "data": {"command": "id"}}]
            api.evidence_entries = lambda eid: list(journal)

            evs = list(service.stream_events(sock, "demo", follow=False))
            check("stream replays the whole journal when follow=false",
                  [e["seq"] for e in evs] == [1, 2])
            evs = list(service.stream_events(sock, "demo", from_seq=1, follow=False))
            check("stream 'from' replays only entries after that seq",
                  [e["seq"] for e in evs] == [2])
            check("a negative 'from' is rejected (400 -> VerifyError)",
                  _raises(lambda: list(service.stream_events(sock, "demo", from_seq=-1, follow=False)),
                          VerifyError))

            api.evidence_entries = lambda eid: (_ for _ in ()).throw(VerifyError("bad id"))
            check("stream on a bad engagement is 404 -> VerifyError",
                  _raises(lambda: list(service.stream_events(sock, "demo", follow=False)), VerifyError))
            api.evidence_entries = lambda eid: list(journal)

            got = []
            gen = service.stream_events(sock, "demo", from_seq=2, follow=True, timeout=10)

            def reader():
                for e in gen:
                    got.append(e)
                    break

            t = threading.Thread(target=reader, daemon=True)
            t.start()
            _time.sleep(0.6)
            journal.append({"seq": 3, "kind": "exec", "data": {"command": "whoami"}})
            t.join(timeout=5)
            check("follow=true streams a newly appended entry", [e["seq"] for e in got] == [3])
            gen.close()

        finally:
            srv.shutdown()
            srv.server_close()
            t.join(timeout=5)

    # A non-socket file at the path is refused, never clobbered.
    with tempfile.TemporaryDirectory() as tmp:
        p2 = Path(tmp) / "service.sock"
        p2.write_text("i am not a socket")
        check("refuses to replace a non-socket file at the path",
              _raises(lambda: service.make_server(p2), VerifyError) and p2.read_text() == "i am not a socket")
    (api.images, api.clones, api.engagements, api.provenance,
     api.provision, api.teardown, api.exec, api.evidence_entries) = saved


def test_scoped_range_client() -> None:
    """#108 slice 1: the agent's range client runs a command in its one assigned clone via the
    service, decodes output, and pins the clone from the environment (not the command)."""
    import base64
    import io

    from rhubarb import agent, service
    from rhubarb.common import VerifyError

    seen = {}

    def fake_post(sock, path, body=None, timeout=300):
        seen["sock"], seen["path"], seen["body"] = sock, path, body
        return {"exit_code": 0, "stdout": base64.b64encode(b"uid=1000\n").decode(),
                "stderr": base64.b64encode(b"").decode(), "evidence_seq": 5}

    saved = service.post_json
    try:
        service.post_json = fake_post
        r = agent.range_exec("/s.sock", "jsl-attacker", "id")
        check("range_exec runs in the named clone via the service exec endpoint",
              seen["path"] == "/clones/jsl-attacker/exec" and seen["body"]["command"] == "id")
        check("range_exec decodes the base64 output and returns the exit code",
              r["exit_code"] == 0 and r["stdout"] == b"uid=1000\n" and r["stderr"] == b""
              and r["approval_required"] is False)

        def reject(sock, path, body=None, timeout=300):
            raise VerifyError("jsl-attacker: no IP (is it running?)")
        service.post_json = reject
        check("range_exec surfaces a service rejection",
              _raises(lambda: agent.range_exec("/s.sock", "jsl-attacker", "id"), VerifyError))

        # main(): the clone is fixed by the environment; the command cannot change it.
        service.post_json = fake_post
        os.environ["RBT_SERVICE_SOCKET"] = "/s.sock"
        os.environ["RBT_RANGE_CLONE"] = "jsl-attacker"
        try:
            buf = io.BytesIO()
            real = sys.stdout
            class _B:  # capture sys.stdout.buffer.write
                buffer = buf
                def flush(self): pass
            sys.stdout = _B()
            try:
                agent.main(["--", "curl", "http://other-clone/"])
                rc = 0
            except SystemExit as e:
                rc = e.code
            finally:
                sys.stdout = real
            check("main targets only the env-assigned clone, whatever the command says",
                  seen["path"] == "/clones/jsl-attacker/exec"
                  and seen["body"]["command"] == "curl http://other-clone/")
            check("main exits with the remote code and writes stdout", rc == 0 and buf.getvalue() == b"uid=1000\n")
        finally:
            os.environ.pop("RBT_SERVICE_SOCKET", None)
            os.environ.pop("RBT_RANGE_CLONE", None)

        check("main without the armed environment refuses",
              _raises(lambda: agent.main(["--", "id"]), SystemExit))
    finally:
        service.post_json = saved


def test_herdr_arm() -> None:
    """#108 slice 2: herdr.arm validates config, drives herdr to make a pane per agent pinned to
    its clone, and journals an 'arm' evidence entry. herdr + core mocked (no herdr/tart)."""
    import json as _json
    from pathlib import Path

    from rhubarb import api, evidence, herdr
    from rhubarb.common import VerifyError

    # committed config loads
    cfg = herdr.load_config("juiceshop-lab")
    check("load_config reads the committed engagement herdr config",
          cfg == [{"name": "recon", "kind": "claude", "clone": "jsl-attacker",
                   "model": "claude-opus-4-8"}])

    # validation rejections via a temp config path
    saved_cfgpath = herdr.config_path
    with tempfile.TemporaryDirectory() as tmp:
        def write(obj):
            p = Path(tmp) / "x.herdr.json"
            p.write_text(_json.dumps(obj))
            herdr.config_path = lambda eid: p
        for label, obj in [
            ("an unknown top key", {"agents": [{"name": "a", "kind": "claude", "clone": "c"}], "x": 1}),
            ("an empty agents list", {"agents": []}),
            ("an unknown agent kind", {"agents": [{"name": "a", "kind": "nope", "clone": "c"}]}),
            ("a bad agent name", {"agents": [{"name": "A B", "kind": "claude", "clone": "c"}]}),
            ("a duplicate name", {"agents": [{"name": "a", "kind": "claude", "clone": "c"},
                                             {"name": "a", "kind": "codex", "clone": "d"}]}),
            ("a missing key", {"agents": [{"name": "a", "kind": "claude"}]}),
        ]:
            write(obj)
            check(f"load_config rejects {label}", _raises(lambda: herdr.load_config("x"), VerifyError))
    herdr.config_path = saved_cfgpath

    # arm orchestration, herdr + core mocked
    calls = []

    def fake_herdr(*args, check=True):
        calls.append(args)
        if args[:2] == ("workspace", "create"):
            return {"workspace": {"workspace_id": "w1"}, "root_pane": {"pane_id": "w1:p1"}}
        if args[:2] == ("pane", "split"):
            return {"pane": {"pane_id": "w1:p2"}}
        if args[:2] == ("agent", "start"):
            return {"_error": "agent_not_ready"} if "slow" in args else {}
        return {}

    def clone(name, eng="juiceshop-lab"):
        return api.Clone(name=name, profile="kali-research", family="kali", image="rbt-x",
                         state="running", freshness="current", password_mode="unique",
                         password_account=name, enrollments=[], created_at="2026-01-02T00:00:00Z",
                         engagement=eng)

    with tempfile.TemporaryDirectory() as tmp:
        sock = Path(tmp) / "service.sock"
        sock.write_text("")   # arm only checks existence
        cfgp = Path(tmp) / "two.herdr.json"
        cfgp.write_text(_json.dumps({"agents": [
            {"name": "recon", "kind": "claude", "clone": "jsl-attacker", "model": "claude-opus-4-8"},
            {"name": "slow", "kind": "codex", "clone": "jsl-target"}]}))
        saved = (herdr._herdr, herdr.herdr_bin, herdr.config_path, api.engagement_clones)
        try:
            herdr._herdr = fake_herdr
            herdr.herdr_bin = lambda: "herdr"
            herdr.config_path = lambda eid: cfgp
            api.engagement_clones = lambda eid: [clone("jsl-attacker"), clone("jsl-target")]

            res = herdr.arm("juiceshop-lab", socket_path=str(sock))
            check("arm makes a workspace then splits for the 2nd agent",
                  any(c[:2] == ("workspace", "create") for c in calls)
                  and any(c[:2] == ("pane", "split") for c in calls))
            ws_call = next(c for c in calls if c[:2] == ("workspace", "create"))
            check("arm pins the socket and clone into the first pane's env",
                  f"RBT_SERVICE_SOCKET={sock}" in ws_call and "RBT_RANGE_CLONE=jsl-attacker" in ws_call)
            split_call = next(c for c in calls if c[:2] == ("pane", "split"))
            check("arm pins the 2nd agent's clone in its split pane",
                  "RBT_RANGE_CLONE=jsl-target" in split_call)
            check("arm puts the repo on PATH in each pane",
                  sum(1 for c in calls if c[:2] == ("pane", "run") and "export" in c[3]) == 2)
            start_call = next(c for c in calls if c[:2] == ("agent", "start") and c[2] == "recon")
            check("arm starts each agent with its kind, pane, and configured model",
                  start_call[:7] == ("agent", "start", "recon", "--kind", "claude", "--pane", "w1:p1")
                  and "--model" in start_call and "claude-opus-4-8" in start_call)
            check("arm records a slow agent's note instead of failing",
                  res.agents[1].name == "slow" and res.agents[1].note == "agent_not_ready")

            j = evidence.entries("juiceshop-lab")
            arm_entry = j[-1]
            check("arm journals an evidence entry with the config hash and agents",
                  arm_entry["kind"] == "lifecycle" and arm_entry["data"]["event"] == "arm"
                  and len(arm_entry["data"]["config_sha256"]) == 64
                  and [x["clone"] for x in arm_entry["data"]["agents"]] == ["jsl-attacker", "jsl-target"])

            # refusals
            api.engagement_clones = lambda eid: []
            check("arm refuses an unprovisioned engagement",
                  _raises(lambda: herdr.arm("juiceshop-lab", socket_path=str(sock)), VerifyError))
            api.engagement_clones = lambda eid: [clone("jsl-attacker")]
            check("arm refuses when a config clone is not in the engagement",
                  _raises(lambda: herdr.arm("juiceshop-lab", socket_path=str(sock)), VerifyError))
            api.engagement_clones = lambda eid: [clone("jsl-attacker"), clone("jsl-target")]
            check("arm refuses when the control-plane service is not running",
                  _raises(lambda: herdr.arm("juiceshop-lab", socket_path=str(Path(tmp) / "no.sock")),
                          VerifyError))
        finally:
            (herdr._herdr, herdr.herdr_bin, herdr.config_path, api.engagement_clones) = saved


def test_tiered_approvals() -> None:
    """#108 slice 3: tiered commands are held for approval, granted single-use, and the whole
    exchange is evidence. approvals ledger + api.exec gating + api.approve/pending (core mocked)."""
    import json as _json
    from pathlib import Path

    from rhubarb import api, approvals, evidence
    from rhubarb.common import VerifyError

    saved_cfg = approvals._config_path
    with tempfile.TemporaryDirectory() as tmp:
        cfg = Path(tmp) / "e.herdr.json"
        cfg.write_text(_json.dumps({"agents": [{"name": "a", "kind": "claude", "clone": "c1"}],
                                    "tiered": ["curl ", "^rm "]}))
        approvals._config_path = lambda eid: cfg

        check("needs_approval matches a tiered pattern",
              approvals.needs_approval("eng", "curl http://x") and not approvals.needs_approval("eng", "id"))
        rid = approvals.request_id("c1", "curl http://x")
        check("request_id is stable per (clone, command)",
              rid == approvals.request_id("c1", "curl http://x")
              and rid != approvals.request_id("c2", "curl http://x"))

        # request -> pending; not approved yet
        approvals.record_request("eng", "c1", "curl http://x")
        approvals.record_request("eng", "c1", "curl http://x")   # idempotent while pending
        pend = approvals.pending("eng")
        check("a held command shows once in pending",
              [p["request_id"] for p in pend] == [rid]
              and sum(1 for x in evidence.entries("eng")
                      if x["kind"] == "approval" and x["data"]["state"] == "requested") == 1)
        check("not approved before a grant",
              not approvals.is_approved_and_consume("eng", "c1", "curl http://x"))

        # grant -> single-use consume
        approvals.grant("eng", rid)
        check("granted request is consumable exactly once",
              approvals.is_approved_and_consume("eng", "c1", "curl http://x")
              and not approvals.is_approved_and_consume("eng", "c1", "curl http://x"))
        check("after consume it is no longer pending", approvals.pending("eng") == [])

    approvals._config_path = saved_cfg

    # api.exec gating (mock the clone record + ssh; use the temp tiered config)
    with tempfile.TemporaryDirectory() as tmp:
        cfg = Path(tmp) / "e2.herdr.json"
        cfg.write_text(_json.dumps({"agents": [{"name": "a", "kind": "claude", "clone": "c1"}],
                                    "tiered": ["curl "]}))
        approvals._config_path = lambda eid: cfg
        ran = []
        orig = (api._clones.load, api.hostops.vm_ip, api.subprocess.run)
        try:
            api._clones.load = lambda n: {"name": "c1", "family": "kali", "username": "kr",
                                          "profile": "kali-research", "engagement": "juiceshop-lab"}
            api.hostops.vm_ip = lambda n, f, wait=180: "10.0.0.2"
            api.subprocess.run = lambda argv, **k: (ran.append(argv) or
                types.SimpleNamespace(stdout=b"ok\n", stderr=b"", returncode=0))

            r = api.exec("c1", "curl http://x")
            check("exec holds a tiered command instead of running it",
                  r.approval_required and r.exit_code == 126 and not ran and r.request_id)
            check("holding a tiered command records a request",
                  api.pending_approvals("juiceshop-lab") and api.pending_approvals("juiceshop-lab")[0]["command"] == "curl http://x")

            check("approve rejects an unknown request",
                  _raises(lambda: api.approve("juiceshop-lab", "deadbeef"), VerifyError))
            api.approve("juiceshop-lab", r.request_id)
            r2 = api.exec("c1", "curl http://x")
            check("after approval the command runs (grant consumed)",
                  not r2.approval_required and r2.exit_code == 0 and len(ran) == 1)
            r3 = api.exec("c1", "curl http://x")
            check("a second run needs a fresh approval (single-use)",
                  r3.approval_required and len(ran) == 1)

            ran.clear()
            r4 = api.exec("c1", "id")
            check("a non-tiered command runs without approval", not r4.approval_required and len(ran) == 1)
        finally:
            (api._clones.load, api.hostops.vm_ip, api.subprocess.run) = orig
    approvals._config_path = saved_cfg


def test_range_client_waits_for_approval() -> None:
    """#108 slice 3: rbt-range holds a tiered command until it is approved, then runs it."""
    import base64

    from rhubarb import agent, service

    os.environ["RBT_SERVICE_SOCKET"] = "/s.sock"
    os.environ["RBT_RANGE_CLONE"] = "c1"
    os.environ["RBT_APPROVAL_POLL"] = "0"
    calls = {"n": 0}

    def fake_post(sock, path, body=None, timeout=300):
        calls["n"] += 1
        if calls["n"] < 3:   # held twice, then approved
            return {"exit_code": 126, "approval_required": True, "request_id": "r1",
                    "stdout": base64.b64encode(b"").decode(), "stderr": base64.b64encode(b"").decode()}
        return {"exit_code": 0, "approval_required": False,
                "stdout": base64.b64encode(b"done\n").decode(), "stderr": base64.b64encode(b"").decode()}

    saved = service.post_json
    real_out = sys.stdout
    buf = __import__("io").BytesIO()
    class _B:
        buffer = buf
        def flush(self): pass
    try:
        service.post_json = fake_post
        sys.stdout = _B()
        try:
            agent.main(["--", "curl", "http://x"])
            rc = 0
        except SystemExit as e:
            rc = e.code
        finally:
            sys.stdout = real_out
        check("rbt-range polls until approved, then runs and exits 0",
              rc == 0 and calls["n"] == 3 and buf.getvalue() == b"done\n")
    finally:
        service.post_json = saved
        for k in ("RBT_SERVICE_SOCKET", "RBT_RANGE_CLONE", "RBT_APPROVAL_POLL"):
            os.environ.pop(k, None)


def test_logs_api() -> None:
    """#120: list_logs classifies clone/build/events logs (skipping symlinks), read_log tails
    safely and refuses unsafe ids, new_build_log makes a private file, and build output shown in
    the TUI/log has the build VM's VNC password redacted."""
    from pathlib import Path

    from rhubarb import api
    from rhubarb.common import VerifyError
    from rhubarb.tui.actions import build as build_action

    state = Path(os.environ["RHUBARB_STATE_DIR"])   # the runner's per-test throwaway dir
    logs = state / "logs"
    logs.mkdir(parents=True, exist_ok=True)
    os.chmod(state, 0o700)
    os.chmod(logs, 0o700)
    (logs / "live-1.log").write_text("".join(f"line {i}\n" for i in range(5000)))
    (logs / "gone-1.log").write_text("old run\n")
    (logs / "build-kali-research-20260930T120000Z.log").write_text("installing kali\n")
    (state / "events.log").write_text("2026-09-30\tnew\tlive-1\t\n")
    (logs / "evil.log").symlink_to("/etc/passwd")
    (logs / "big.log").write_bytes(b"x" * 600_000 + b"\nlast-but-one\nlast\n")

    saved = api._clones.all_records
    try:
        api._clones.all_records = lambda: ([{"name": "live-1"}], [])
        refs = {r.id: r for r in api.list_logs()}
    finally:
        api._clones.all_records = saved
    check("list_logs finds clone, build and events logs",
          {"logs/live-1.log", "logs/gone-1.log", "logs/build-kali-research-20260930T120000Z.log",
           "events.log"} <= set(refs))
    check("list_logs skips a symlink", "logs/evil.log" not in refs)
    check("list_logs labels live, removed, build and events logs",
          refs["logs/live-1.log"].label == "live-1"
          and refs["logs/gone-1.log"].label == "gone-1 (removed)"
          and refs["logs/build-kali-research-20260930T120000Z.log"].kind == "build"
          and refs["events.log"].kind == "events")

    check("read_log returns the tail", api.read_log("logs/live-1.log", max_lines=3)
          == "line 4997\nline 4998\nline 4999")
    check("read_log tails a log larger than the read window",
          api.read_log("logs/big.log", max_lines=2) == "last-but-one\nlast")
    for bad in ("../x.log", "logs/../x.log", "logs/a/b.log", "/etc/passwd", "logs/x.txt", "logs/"):
        check(f"read_log refuses {bad!r}", _raises(lambda b=bad: api.read_log(b), VerifyError))
    check("read_log refuses to follow a symlink", _raises(lambda: api.read_log("logs/evil.log"), OSError))

    p = api.new_build_log("kali-research")
    check("new_build_log makes a private build-<profile>-<time>.log",
          p.parent == logs and p.name.startswith("build-kali-research-") and p.suffix == ".log"
          and (p.stat().st_mode & 0o777) == 0o600)
    check("new_build_log refuses a path-like profile",
          _raises(lambda: api.new_build_log("../x"), VerifyError))

    line = 'connect via VNC with the password "chuckle-deny-lonely-legal" to'
    check("build output redacts the VNC password",
          "chuckle" not in build_action.redact(line) and '"***"' in build_action.redact(line))


if __name__ == "__main__":
    for t in (test_ed25519, test_nar, test_dpkg, test_records, test_cli_lifecycle,
              test_rotation_script, test_api_pure, test_engagements, test_engagement_ops, test_engagement_links,
              test_cli_progress_stream, test_hostops_resilience, test_shutdown_reaps_boot_process,
              test_reap_run, test_fs_vms, test_ssh_client, test_ssh_provenance, test_confirm_prompt,
              test_pgp_ed25519, test_toolchain_gpg, test_profile_usernames, test_packages_tsv_readers,
              test_sshd_T_normalization, test_kali_nopasswd_allowlist,
              test_build_cleanup_trap, test_content_addressed_cache, test_chrome_update_policy, test_publish_offline_signing,
              test_stacked_clones, test_github_release_resolver, test_reset_keeps_engagement, test_guest_sync,
              test_evidence_store, test_evidence_exec_collect, test_vault_seal_verify,
              test_control_plane_service, test_scoped_range_client, test_herdr_arm,
              test_tiered_approvals, test_range_client_waits_for_approval, test_logs_api):
        print(t.__name__)
        # Each test gets a throwaway state dir, so nothing (records, evidence) can reach the
        # operator's real one; tests that manage RHUBARB_STATE_DIR themselves still may.
        with tempfile.TemporaryDirectory() as state:
            os.environ["RHUBARB_STATE_DIR"] = state
            try:
                t()
            finally:
                os.environ.pop("RHUBARB_STATE_DIR", None)
    if FAILS:
        sys.exit(f"{len(FAILS)} test(s) failed")
    print("all rhubarb self-tests passed")
