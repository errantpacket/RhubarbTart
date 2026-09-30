"""Self-tests: engagement manifests, provision/teardown and links."""

import json
import subprocess
import tempfile
from pathlib import Path

from tests.support import _is_frozen, check


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
