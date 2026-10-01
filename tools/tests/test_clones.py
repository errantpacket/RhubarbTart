"""Self-tests: clone records, the rhubarb CLI, host operations, SSH, stacked clones and the typed core API."""

import json
import os
import subprocess
import sys
import tempfile
import types
from pathlib import Path

from tests.support import ROOT, _stub, check


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
    root = ROOT
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


def test_version() -> None:
    """#138: one version, SemVer, shown by `rhubarb --version`."""
    import re

    from rhubarb import __version__

    check("__version__ is MAJOR.MINOR.PATCH", re.fullmatch(r"\d+\.\d+\.\d+", __version__) is not None)
    res = subprocess.run([sys.executable, str(ROOT / "tools" / "rhubarb_cli.py"), "--version"],
                         capture_output=True, text=True, timeout=60)
    check("rhubarb --version prints 'rhubarb <version>' and exits 0",
          res.returncode == 0 and res.stdout.strip() == f"rhubarb {__version__}")
