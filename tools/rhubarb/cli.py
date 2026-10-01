"""`rhubarbtart`: manage research clones of verified RhubarbTart images.

  rhubarbtart images                         built images, and which is current per profile
  rhubarbtart new NAME (--profile P | --image IMG) [--no-rotate]
  rhubarbtart list                           clones: lineage, state, staleness, password, enrollment
  rhubarbtart run NAME [--headless] [--detach]
  rhubarbtart stop NAME
  rhubarbtart ssh NAME [-- CMD...]
  rhubarbtart exec NAME -- CMD...                       run a command; journaled if the clone is in an engagement
  rhubarbtart enroll NAME tailscale|warp [--org TEAM]
  rhubarbtart reset NAME [--same-image] [--no-rotate]   back to a clean clone (drops enrollment)
  rhubarbtart rm NAME [--yes]
  rhubarbtart serve [--socket PATH]                     control-plane service on a 0600 Unix socket
  rhubarbtart herdr arm ID [--socket PATH]              launch the engagement's agents under herdr (charter model A)
  rhubarbtart herdr pending ID                          tiered commands awaiting approval
  rhubarbtart herdr approve ID REQUEST                  grant one single-use approval
  rhubarbtart engagement define FILE|ID                 validate + acknowledge a scope manifest
  rhubarbtart engagement list                           defined engagements and their clone counts
  rhubarbtart engagement provision ID                   stand up its ranges from verified images
  rhubarbtart engagement connect ID                     open its links until Ctrl-C (#30)
  rhubarbtart engagement teardown ID [--yes] [--no-collect]   collect evidence, then remove its clones
  rhubarbtart evidence collect|list|verify ID           pull ~/evidence from its clones; show; check the chain
  rhubarbtart vault seal ID [--out DIR]                 write a signed, sealed, portable evidence bundle
  rhubarbtart vault verify DIR [--pub KEY]              check a sealed vault's signature and every hash

Only clones created by `rhubarbtart new` can be run, reset or removed through this tool; built
images (rbt-*) and other VMs are never modified. Records: see tools/rhubarb/clones.py.

This is a thin adapter: all orchestration, keychain, StrictModes and GUI-session logic lives
in the typed core (tools/rhubarb/api.py, over clones.py/hostops.py). Each command calls the
API and formats its structured result for the terminal — see docs/INTERFACE-PLAN.md, Stage A.
"""

import argparse
import os
import signal
import subprocess
import sys
from pathlib import Path

from . import api, clones, hostops
from .common import VerifyError


def say(msg: str) -> None:
    print(f"[rhubarbtart] {msg}", file=sys.stderr)


def table(rows: list[list[str]], header: list[str]) -> None:
    widths = [max(len(str(r[i])) for r in [header, *rows]) for i in range(len(header))]
    for r in [header, *rows]:
        print("  ".join(str(c).ljust(w) for c, w in zip(r, widths, strict=True)).rstrip())


def _say_clone(res: api.NewResult) -> None:
    """Report the outcome of a fresh clone (new/reset).

    The step-by-step milestones (``cloned ... -> ...``, ``booting headless ...``, ``unique
    password set ...``) stream live during the operation via the ``progress`` callback the
    commands pass to ``api.new``/``api.reset`` (see #19); this only reports the closing
    ``note`` — why rotation was skipped or could not complete — which the core returns
    rather than streams. ``None`` note means a clean rotation, so nothing more to say.
    """
    if res.note:
        say(res.note)


# ---- commands ------------------------------------------------------------------------------

def cmd_images(_a) -> None:
    rows = [[i.name, i.profile, i.status, str(i.clones)] for i in api.images()]
    if rows:
        table(rows, ["IMAGE", "PROFILE", "STATUS", "CLONES"])
    else:
        say("no built images (./scripts/build.sh PROFILE)")


def cmd_list(_a) -> None:
    result = api.clones()
    rows = [[c.name, c.profile, c.state, c.freshness, c.password_mode,
             ",".join(c.enrollments) or "-"] for c in result.clones]
    if rows:
        table(rows, ["CLONE", "PROFILE", "STATE", "IMAGE", "PASSWORD", "ENROLLED"])
    else:
        say("no clones yet (rhubarbtart new NAME --profile P)")
    for p in result.problems:
        say(f"IGNORED {p}")


def cmd_new(a) -> None:
    res = api.new(a.name, profile=a.profile, image=a.image, rotate=not a.no_rotate, progress=say,
                  from_registry=a.from_registry)
    _say_clone(res)
    say(f"ready: rhubarbtart run {res.name}")


def cmd_run(a) -> None:
    res = api.run(a.name, headless=a.headless, detach=a.detach)
    if res.detached:
        say(f"started {res.name} in the background (log: {res.log_path})")
        return
    os.execvp(res.argv[0], res.argv)


def cmd_stop(a) -> None:
    rec = clones.load(a.name)
    if hostops.is_running(rec["name"]) and not hostops.sync_guest(rec["name"], rec["username"]):
        say(f"{rec['name']}: couldn't flush the guest's disk first (no SSH?); recent writes may be lost")
    hostops.stop_vm(rec["name"])
    say(f"stopped {rec['name']}")


def cmd_ssh(a) -> None:
    res = api.ssh_args(a.name)
    os.execvp(res.argv[0], [*res.argv, *a.remote])


def cmd_exec(a) -> None:
    if not a.remote:
        sys.exit("[rhubarbtart] usage: rhubarbtart exec NAME -- CMD...")
    res = api.exec(a.name, " ".join(a.remote))
    sys.stdout.buffer.write(res.stdout)
    sys.stdout.flush()
    sys.stderr.buffer.write(res.stderr)
    if res.evidence_seq is not None:
        say(f"recorded as evidence entry {res.evidence_seq}")
    if res.timed_out:
        say("timed out")
    sys.exit(res.exit_code if 0 <= res.exit_code < 256 else 1)


def cmd_enroll(a) -> None:
    res = api.enroll(a.name, a.service, org=a.org)
    if res.detail:
        print(res.detail)
    if res.recorded:
        say(f"enrolled {res.name} in {res.service} ({res.enrolled_at})")
    else:
        say(f"{res.name}: {res.service} enrollment is manual — follow the printed steps above")


def cmd_reset(a) -> None:
    res = api.reset(a.name, same_image=a.same_image, rotate=not a.no_rotate, progress=say)
    _say_clone(res)


def _confirm(prompt: str) -> bool:
    """[y/N] prompt. With no answer to read (closed stdin: scripts, agents, CI) refuse loudly
    instead of dying on EOFError; nothing destructive has happened yet at this point. (#47)"""
    try:
        answer = input(prompt)
    except EOFError:
        print(file=sys.stderr)
        raise VerifyError("no confirmation on stdin (not interactive); nothing removed — "
                          "pass --yes to confirm") from None
    return answer.strip().lower() in ("y", "yes")


def cmd_rm(a) -> None:
    if not a.yes:
        rec = clones.load(a.name)  # StrictModes-trusted read, for the confirmation prompt
        if not _confirm(f"Delete clone {rec['name']} (from {rec['image']}) and its keychain entry? [y/N] "):
            say("aborted")
            return
    res = api.rm(a.name)
    say(f"removed {res.name}")
    if res.base_released:
        say("released its registry base (no other clone was stacked on it)")


# ---- engagement commands -------------------------------------------------------------------
# Thin adapters over the core's engagement ops (api.provision/connect/teardown/engagements/
# engagement_clones, over engagements.py). No tart/keychain/record logic lives here.

def _engagement_id(arg: str) -> str:
    """Accept a manifest path (engagements/<id>.json) or a bare id, returning the id.

    Manifests are committed in engagements/ and loaded from there by id; a path is a
    convenience, so strip its directory and .json suffix to the stem.
    """
    return Path(arg).stem if arg.endswith(".json") or "/" in arg else arg


def cmd_engagement_define(a) -> None:
    eid = _engagement_id(a.engagement)
    eng = api._engagements.load_engagement(eid)   # strict validation ("no manifest, no run")
    total = sum(r.count for r in eng.ranges)
    say(f"engagement {eng.id!r} OK: {eng.label} (operator {eng.operator})")
    say(f"authorization: {eng.authorization}")
    rows = [[r.profile, str(r.count), r.prefix or f"{eng.id}-{r.profile}"] for r in eng.ranges]
    table(rows, ["PROFILE", "COUNT", "PREFIX"])
    say(f"{len(eng.ranges)} range(s), {total} clone(s); provision with: rhubarbtart engagement provision {eng.id}")


def cmd_engagement_list(_a) -> None:
    defined = api.engagements()
    if not defined:
        say("no engagements defined (add engagements/<id>.json)")
        return
    counts: dict[str, int] = {}
    for c in api.clones().clones:
        if c.engagement:
            counts[c.engagement] = counts.get(c.engagement, 0) + 1
    table([[eid, str(counts.get(eid, 0))] for eid in defined], ["ENGAGEMENT", "CLONES"])


def cmd_engagement_provision(a) -> None:
    res = api.provision(_engagement_id(a.engagement))
    for r in res.created:
        _say_clone(r)
        say(f"provisioned {r.name} (from {r.image}, password {r.password_mode})")
    for name in res.skipped:
        say(f"skipped {name}: already exists (not re-created)")
    say(f"engagement {res.engagement}: {len(res.created)} created, {len(res.skipped)} skipped")


def cmd_engagement_connect(a) -> None:
    eid = _engagement_id(a.engagement)
    say(f"engagement {eid}: opening links (Ctrl-C closes them)")

    def stop(_sig, _frame):
        raise KeyboardInterrupt

    # Close the tunnels however we're stopped: Ctrl-C (even if SIGINT was inherited as ignored,
    # as in a background job), `kill`, or the terminal going away.
    for sig in (signal.SIGINT, signal.SIGTERM, signal.SIGHUP):
        signal.signal(sig, stop)
    try:
        api.connect(eid, progress=say)
    except KeyboardInterrupt:
        say(f"engagement {eid}: links closed")


def cmd_engagement_teardown(a) -> None:
    eid = _engagement_id(a.engagement)
    if not a.yes:
        victims = [c.name for c in api.engagement_clones(eid)]
        if not victims:
            say(f"engagement {eid}: no clones to tear down")
            return
        if not _confirm(f"Tear down {len(victims)} clone(s) of engagement {eid} "
                        f"({', '.join(victims)}) and their keychain entries? [y/N] "):
            say("aborted")
            return
    removed = api.teardown(eid, collect_first=not a.no_collect, progress=say)
    if removed:
        say(f"engagement {eid}: removed {len(removed)} clone(s): {', '.join(removed)}")
    else:
        say(f"engagement {eid}: nothing to remove")


def cmd_evidence_collect(a) -> None:
    eid = _engagement_id(a.engagement)
    results = api.collect(eid, progress=say)
    for r in results:
        if r.note:
            say(f"{r.name}: {r.note}")
        for s in r.skipped:
            say(f"{r.name}: skipped {s}")
    if not results:
        say(f"engagement {eid}: no clones to collect from")


def _evidence_what(kind: str | None, d: dict) -> str:
    if kind == "exec":
        return f"$ {d.get('command')}  (exit {d.get('exit_code')})"
    if kind == "artifact":
        return f"{d.get('path')}  ({d.get('size')} bytes)"
    if kind == "ground_truth":
        return f"{d.get('package')} {d.get('status', d.get('error'))}"
    if kind == "approval":
        return f"{d.get('state')} {d.get('request_id')}" + (f"  {d['command']}" if d.get("command") else "")
    return str(d.get("event", ""))


def cmd_evidence_list(a) -> None:
    eid = _engagement_id(a.engagement)
    rows = []
    for e in api.evidence_entries(eid):
        rows.append([str(e.get("seq")), e.get("ts", ""), e.get("kind", ""), e.get("clone") or "-",
                     _evidence_what(e.get("kind"), e.get("data", {}))])
    if rows:
        table(rows, ["SEQ", "TIME", "KIND", "CLONE", "WHAT"])
    else:
        say(f"engagement {eid}: no evidence yet")


def cmd_evidence_verify(a) -> None:
    eid = _engagement_id(a.engagement)
    rep = api.verify_evidence(eid)
    for p in rep.problems:
        say(f"PROBLEM: {p}")
    if rep.problems:
        sys.exit(f"[rhubarbtart] FAILED: engagement {eid}: evidence does not verify "
                 f"({len(rep.problems)} problem(s))")
    say(f"engagement {eid}: {rep.entries} entries, {rep.items} items verified; head {rep.head[:16]}")


def cmd_vault_seal(a) -> None:
    vault = api.seal_vault(_engagement_id(a.engagement), out_dir=a.out, progress=say)
    say(f"vault sealed (read-only): {vault}")
    say(f"verify it anywhere with: ./rhubarbtart vault verify {vault}")


def cmd_vault_verify(a) -> None:
    rep = api.verify_vault(a.vault, pub=a.pub)
    for p in rep.problems:
        say(f"PROBLEM: {p}")
    if rep.problems or not rep.signed:
        sys.exit(f"[rhubarbtart] FAILED: vault does NOT verify ({len(rep.problems)} problem(s))")
    say(f"vault OK: engagement {rep.engagement}, {rep.entries} entries, {rep.items} items, "
        f"signature verified; head {rep.chain_head[:16]}")


def cmd_herdr_arm(a) -> None:
    from . import herdr
    res = herdr.arm(_engagement_id(a.engagement), socket_path=a.socket, progress=say)
    for ag in res.agents:
        say(f"{ag.name} ({ag.kind}) -> clone {ag.clone}, pane {ag.pane}"
            + (f"  [{ag.note}]" if ag.note else ""))
    say(f"engagement {res.engagement}: armed {len(res.agents)} agent(s) in workspace {res.workspace}")


def cmd_herdr_pending(a) -> None:
    eid = _engagement_id(a.engagement)
    rows = [[p["request_id"], p["clone"] or "-", p["command"]] for p in api.pending_approvals(eid)]
    if rows:
        table(rows, ["REQUEST", "CLONE", "COMMAND"])
    else:
        say(f"engagement {eid}: no approvals pending")


def cmd_herdr_approve(a) -> None:
    eid = _engagement_id(a.engagement)
    api.approve(eid, a.request)
    say(f"engagement {eid}: approved {a.request} (single use)")


def cmd_serve(a) -> None:
    from . import service

    def stop(_sig, _frame):
        raise KeyboardInterrupt

    # Clean up the socket on Ctrl-C *and* `kill` (SIGTERM), so a supervised service leaves no
    # stale socket behind; serve()'s finally then unlinks it.
    for sig in (signal.SIGINT, signal.SIGTERM):
        signal.signal(sig, stop)
    say("control-plane service (read-only); Ctrl-C to stop")
    try:
        service.serve(socket_path=a.socket, on_ready=lambda p: say(f"listening on {p} (0600)"))
    except KeyboardInterrupt:
        say("stopped")


def main(argv: list[str] | None = None) -> None:
    from . import __version__

    ap = argparse.ArgumentParser(prog="rhubarbtart", description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--version", action="version", version=f"rhubarbtart {__version__}")
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("images").set_defaults(fn=cmd_images)
    sub.add_parser("list").set_defaults(fn=cmd_list)
    n = sub.add_parser("new")
    n.add_argument("name")
    n.add_argument("--profile")
    n.add_argument("--image")
    n.add_argument("--no-rotate", action="store_true", help="keep the image's password")
    n.add_argument("--from-registry", action="store_true",
                   help="macOS: stack on the image's verified registry copy (scripts/publish.sh)")
    n.set_defaults(fn=cmd_new)
    r = sub.add_parser("run")
    r.add_argument("name")
    r.add_argument("--headless", action="store_true")
    r.add_argument("--detach", action="store_true")
    r.set_defaults(fn=cmd_run)
    st = sub.add_parser("stop")
    st.add_argument("name")
    st.set_defaults(fn=cmd_stop)
    sh = sub.add_parser("ssh")
    sh.add_argument("name")
    sh.add_argument("remote", nargs=argparse.REMAINDER, help="command to run (after --)")
    sh.set_defaults(fn=cmd_ssh)
    ex = sub.add_parser("exec", help="run a command in a clone (journaled as evidence in an engagement)")
    ex.add_argument("name")
    ex.add_argument("remote", nargs=argparse.REMAINDER, help="command to run (after --)")
    ex.set_defaults(fn=cmd_exec)
    e = sub.add_parser("enroll")
    e.add_argument("name")
    e.add_argument("service", choices=sorted(clones.SERVICES))
    e.add_argument("--org")
    e.set_defaults(fn=cmd_enroll)
    rs = sub.add_parser("reset")
    rs.add_argument("name")
    rs.add_argument("--same-image", action="store_true", help="re-clone from the same (possibly outdated) image")
    rs.add_argument("--no-rotate", action="store_true")
    rs.set_defaults(fn=cmd_reset)
    rm = sub.add_parser("rm")
    rm.add_argument("name")
    rm.add_argument("--yes", action="store_true")
    rm.set_defaults(fn=cmd_rm)
    eng = sub.add_parser("engagement", help="define / list / provision / connect / teardown a scoped engagement")
    esub = eng.add_subparsers(dest="engagement_cmd", required=True)
    ed = esub.add_parser("define", help="validate and acknowledge a manifest (engagements/<id>.json)")
    ed.add_argument("engagement", metavar="FILE|ID")
    ed.set_defaults(fn=cmd_engagement_define)
    esub.add_parser("list", help="defined engagements and their clone counts").set_defaults(fn=cmd_engagement_list)
    ep = esub.add_parser("provision", help="stand up the engagement's clone set from verified images")
    ep.add_argument("engagement", metavar="ID")
    ep.set_defaults(fn=cmd_engagement_provision)
    ec = esub.add_parser("connect", help="open the manifest's links (target ports on each source's loopback)")
    ec.add_argument("engagement", metavar="ID")
    ec.set_defaults(fn=cmd_engagement_connect)
    et = esub.add_parser("teardown", help="remove every clone tagged to the engagement")
    et.add_argument("engagement", metavar="ID")
    et.add_argument("--yes", action="store_true")
    et.add_argument("--no-collect", action="store_true", help="don't pull evidence first")
    et.set_defaults(fn=cmd_engagement_teardown)
    srv = sub.add_parser("serve", help="run the control-plane service on a Unix socket")
    srv.add_argument("--socket", metavar="PATH", help="socket path (default: <state>/service.sock)")
    srv.set_defaults(fn=cmd_serve)
    hd = sub.add_parser("herdr", help="agent-driven engagements via herdr (charter model A)")
    hdsub = hd.add_subparsers(dest="herdr_cmd", required=True)
    ha = hdsub.add_parser("arm", help="launch the engagement's configured agents under herdr")
    ha.add_argument("engagement", metavar="ID")
    ha.add_argument("--socket", metavar="PATH", help="control-plane socket (default: <state>/service.sock)")
    ha.set_defaults(fn=cmd_herdr_arm)
    hp = hdsub.add_parser("pending", help="tiered commands awaiting operator approval")
    hp.add_argument("engagement", metavar="ID")
    hp.set_defaults(fn=cmd_herdr_pending)
    hap = hdsub.add_parser("approve", help="grant one single-use approval for a pending request")
    hap.add_argument("engagement", metavar="ID")
    hap.add_argument("request", metavar="REQUEST")
    hap.set_defaults(fn=cmd_herdr_approve)
    vt = sub.add_parser("vault", help="seal / verify a signed evidence bundle")
    vtsub = vt.add_subparsers(dest="vault_cmd", required=True)
    vs = vtsub.add_parser("seal", help="write a signed, sealed, portable evidence bundle")
    vs.add_argument("engagement", metavar="ID")
    vs.add_argument("--out", metavar="DIR", help="where to write the vault (default: ./vaults)")
    vs.set_defaults(fn=cmd_vault_seal)
    vv = vtsub.add_parser("verify", help="check a sealed vault's signature and every hash")
    vv.add_argument("vault", metavar="DIR")
    vv.add_argument("--pub", metavar="KEY", help="public key (default: the vault's cosign.pub)")
    vv.set_defaults(fn=cmd_vault_verify)
    ev = sub.add_parser("evidence", help="collect / list / verify an engagement's evidence")
    evsub = ev.add_subparsers(dest="evidence_cmd", required=True)
    for cmd, fn, hlp in (("collect", cmd_evidence_collect, "pull each clone's ~/evidence (and ground truth)"),
                         ("list", cmd_evidence_list, "the evidence journal"),
                         ("verify", cmd_evidence_verify, "recompute the hash chain and every item")):
        p = evsub.add_parser(cmd, help=hlp)
        p.add_argument("engagement", metavar="ID")
        p.set_defaults(fn=fn)
    a = ap.parse_args(argv)
    if a.cmd in ("ssh", "exec") and a.remote[:1] == ["--"]:
        a.remote = a.remote[1:]
    try:
        a.fn(a)
    except (VerifyError, subprocess.CalledProcessError) as e:
        sys.exit(f"[rhubarbtart] FAILED: {e}")
    except FileNotFoundError as e:
        sys.exit(f"[rhubarbtart] FAILED: {e.filename} not found (on the Mac: ./tools/bootstrap.sh)")
