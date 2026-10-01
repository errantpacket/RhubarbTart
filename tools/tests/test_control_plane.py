"""Self-tests: the control-plane service, the rbt-range client, herdr arm and tiered approvals."""

import os
import sys
import tempfile
import types

from tests.support import _raises, check


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
            from rhubarb import __version__
            check("GET /health -> 200 ok with the version", st == 200 and body["ok"]
                  and body["service"] == "rhubarbtart" and body["version"] == __version__)

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
