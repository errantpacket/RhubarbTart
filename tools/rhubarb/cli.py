"""`rhubarb`: manage research clones of verified RhubarbTart images.

  rhubarb images                         built images, and which is current per profile
  rhubarb new NAME (--profile P | --image IMG) [--no-rotate]
  rhubarb list                           clones: lineage, state, staleness, password, enrollment
  rhubarb run NAME [--headless] [--detach]
  rhubarb stop NAME
  rhubarb ssh NAME [-- CMD...]
  rhubarb enroll NAME tailscale|warp|perimeter81 [--org TEAM]
  rhubarb reset NAME [--same-image] [--no-rotate]   back to a clean clone (drops enrollment)
  rhubarb rm NAME [--yes]

Only clones created by `rhubarb new` can be run, reset or removed through this tool; built
images (rbt-*) and other VMs are never modified. Records: see tools/rhubarb/clones.py.

This is a thin adapter: all orchestration, keychain, StrictModes and GUI-session logic lives
in the typed core (tools/rhubarb/api.py, over clones.py/hostops.py). Each command calls the
API and formats its structured result for the terminal — see docs/INTERFACE-PLAN.md, Stage A.
"""

import argparse
import os
import subprocess
import sys

from . import api, clones, hostops
from .common import VerifyError


def say(msg: str) -> None:
    print(f"[rhubarb] {msg}", file=sys.stderr)


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
        say("no clones yet (rhubarb new NAME --profile P)")
    for p in result.problems:
        say(f"IGNORED {p}")


def cmd_new(a) -> None:
    res = api.new(a.name, profile=a.profile, image=a.image, rotate=not a.no_rotate, progress=say)
    _say_clone(res)
    say(f"ready: rhubarb run {res.name}")


def cmd_run(a) -> None:
    res = api.run(a.name, headless=a.headless, detach=a.detach)
    if res.detached:
        say(f"started {res.name} in the background (log: {res.log_path})")
        return
    os.execvp(res.argv[0], res.argv)


def cmd_stop(a) -> None:
    rec = clones.load(a.name)
    hostops.stop_vm(rec["name"])
    say(f"stopped {rec['name']}")


def cmd_ssh(a) -> None:
    res = api.ssh_args(a.name)
    os.execvp(res.argv[0], [*res.argv, *a.remote])


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


def cmd_rm(a) -> None:
    if not a.yes:
        rec = clones.load(a.name)  # StrictModes-trusted read, for the confirmation prompt
        answer = input(f"Delete clone {rec['name']} (from {rec['image']}) and its keychain entry? [y/N] ")
        if answer.strip().lower() not in ("y", "yes"):
            say("aborted")
            return
    res = api.rm(a.name)
    say(f"removed {res.name}")


def main(argv: list[str] | None = None) -> None:
    ap = argparse.ArgumentParser(prog="rhubarb", description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("images").set_defaults(fn=cmd_images)
    sub.add_parser("list").set_defaults(fn=cmd_list)
    n = sub.add_parser("new")
    n.add_argument("name")
    n.add_argument("--profile")
    n.add_argument("--image")
    n.add_argument("--no-rotate", action="store_true", help="keep the image's password")
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
    a = ap.parse_args(argv)
    if a.cmd == "ssh" and a.remote[:1] == ["--"]:
        a.remote = a.remote[1:]
    try:
        a.fn(a)
    except (VerifyError, subprocess.CalledProcessError) as e:
        sys.exit(f"[rhubarb] FAILED: {e}")
    except FileNotFoundError as e:
        sys.exit(f"[rhubarb] FAILED: {e.filename} not found (on the Mac: ./tools/bootstrap.sh)")
