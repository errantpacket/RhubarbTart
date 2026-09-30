"""Self-tests: the evidence journal, exec/collect and sealed vaults."""

import json
import os
import tempfile
import types

from tests.support import check


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
