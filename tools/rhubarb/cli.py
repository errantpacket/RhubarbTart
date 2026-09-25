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
"""

import argparse
import os
import subprocess
import sys

from . import clones, hostops
from .common import ROOT, VerifyError
from .locks import image_name
from .profiles import list_profiles, load_profile


def say(msg: str) -> None:
    print(f"[rhubarb] {msg}", file=sys.stderr)


def current_image(pid: str) -> str | None:
    """The verified image the profile's committed lock produces (None if no valid lock)."""
    try:
        return image_name(load_profile(pid))
    except VerifyError:
        return None


def profile_of(image: str) -> str | None:
    for pid in sorted(list_profiles(), key=len, reverse=True):
        if image.startswith(f"rbt-{pid}-") and clones.IMAGE_RE.match(image):
            return pid
    return None


def table(rows: list[list[str]], header: list[str]) -> None:
    widths = [max(len(str(r[i])) for r in [header, *rows]) for i in range(len(header))]
    for r in [header, *rows]:
        print("  ".join(str(c).ljust(w) for c, w in zip(r, widths, strict=True)).rstrip())


# ---- commands ------------------------------------------------------------------------------

def cmd_images(_a) -> None:
    vms = hostops.local_vms()
    recs, _ = clones.all_records()
    rows = []
    for name in sorted(vms):
        kind = ("unverified" if name.endswith("-unverified") else
                "vanilla" if name.endswith("-vanilla") else
                "image" if clones.IMAGE_RE.match(name) else None)
        if kind is None:
            continue
        pid = profile_of(name.removesuffix("-unverified")) or "-"
        status = kind
        if kind == "image":
            status = "current" if current_image(pid) == name else "outdated"
        rows.append([name, pid, status, str(sum(r["image"] == name for r in recs))])
    if rows:
        table(rows, ["IMAGE", "PROFILE", "STATUS", "CLONES"])
    else:
        say("no built images (./scripts/build.sh PROFILE)")


def cmd_list(_a) -> None:
    vms = hostops.local_vms()
    recs, problems = clones.all_records()
    rows = []
    for r in recs:
        vm = vms.get(r["name"])
        state = vm.get("State", "?") if vm else "MISSING"
        cur = current_image(r["profile"])
        fresh = ("image-deleted" if r["image"] not in vms else
                 "current" if cur == r["image"] else "outdated")
        pw = "unique" if r["password_account"] == r["name"] else "inherited"
        rows.append([r["name"], r["profile"], state, fresh, pw, ",".join(sorted(r["enrollments"])) or "-"])
    if rows:
        table(rows, ["CLONE", "PROFILE", "STATE", "IMAGE", "PASSWORD", "ENROLLED"])
    else:
        say("no clones yet (rhubarb new NAME --profile P)")
    for p in problems:
        say(f"IGNORED {p}")


def _source_image(a) -> tuple[str, dict]:
    if bool(a.profile) == bool(a.image):
        raise VerifyError("give exactly one of --profile or --image")
    if a.profile:
        prof = load_profile(a.profile)
        image = image_name(prof)
    else:
        image = a.image
        if not clones.IMAGE_RE.match(image):
            raise VerifyError(f"{image!r} is not a verified RhubarbTart image name (rbt-PROFILE-SHA)")
        pid = profile_of(image)
        if not pid:
            raise VerifyError(f"{image}: no matching profile in profiles/")
        prof = load_profile(pid)
    if image not in hostops.local_vms():
        raise VerifyError(f"{image} is not built on this Mac (./scripts/build.sh {prof['id']})")
    if hostops.keychain_get(image) is None:
        raise VerifyError(f"no keychain password for {image}; was it built on this Mac?")
    return image, prof


def _clone(name: str, image: str, prof: dict, rotate: bool) -> dict:
    hostops.tart("clone", image, name)
    rec = clones.new_record(name, prof, image)
    clones.save(rec)
    clones.log_event("new", name, image)
    say(f"cloned {image} -> {name}")
    if rotate:
        _rotate(rec)
    else:
        say("password: inherited from the image (use without --no-rotate for a per-clone password)")
    return rec


def _rotate(rec: dict) -> None:
    """Give the clone its own password (keychain account = clone name), proven via PAM."""
    name = rec["name"]
    old = hostops.keychain_get(rec["password_account"])
    new = hostops.random_password()
    say(f"{name}: booting headless to rotate its password")
    proc = hostops.start_vm(name, rec["rosetta"], headless=True)
    try:
        ip = hostops.vm_ip(name, rec["family"])
        reach = hostops.wait_for_ssh(name, rec["username"], ip)
        if reach != "ok":
            why = ("SSH refused our key: load it with ssh-add, or the image was built for other keys"
                   if reach == "denied" else "SSH not reachable (image built without SSH keys?)")
            say(f"{name}: {why}; keeping the image's password. "
                f"Retry later with: rhubarb reset {name} --same-image")
            return
        hostops.keychain_put(name, new)
        try:
            hostops.rotate_password(name, rec["username"], ip, old, new)
        except VerifyError:
            hostops.keychain_delete(name)
            raise
        rec["password_account"] = name
        clones.save(rec)
        clones.log_event("rotate", name)
        say(f"{name}: unique password set (keychain account {name}); old password rejected")
    finally:
        hostops.stop_vm(name)
        if proc.poll() is None:
            proc.wait(timeout=60)


def cmd_new(a) -> None:
    name = clones.check_clone_name(a.name)
    if name in hostops.local_vms():
        raise VerifyError(f"a VM named {name} already exists")
    image, prof = _source_image(a)
    _clone(name, image, prof, rotate=not a.no_rotate)
    say(f"ready: rhubarb run {name}")


def cmd_run(a) -> None:
    rec = clones.load(a.name)
    if hostops.is_running(rec["name"]):
        raise VerifyError(f"{rec['name']} is already running")
    if a.detach:
        logs = clones._secure_dir(clones.state_dir() / "logs")
        hostops.start_vm(rec["name"], rec["rosetta"], a.headless, logs / f"{rec['name']}.log")
        say(f"started {rec['name']} in the background (log: {logs / (rec['name'] + '.log')})")
        return
    args = ["tart", "run", *(["--rosetta=rosetta"] if rec["rosetta"] else []),
            *(["--no-graphics"] if a.headless else []), rec["name"]]
    os.execvp("tart", args)


def cmd_stop(a) -> None:
    rec = clones.load(a.name)
    hostops.stop_vm(rec["name"])
    say(f"stopped {rec['name']}")


def cmd_ssh(a) -> None:
    rec = clones.load(a.name)
    ip = hostops.vm_ip(rec["name"], rec["family"], wait=60)
    args = hostops.ssh_args(rec["name"], rec["username"], ip, batch=False)
    os.execvp("ssh", [*args, *a.remote])


def cmd_enroll(a) -> None:
    rec = clones.load(a.name)
    cmd = [str(ROOT / "scripts" / "enroll.sh"), rec["name"], a.service, "--image", rec["password_account"]]
    if a.org:
        cmd += ["--org", a.org]
    res = subprocess.run(cmd, env=hostops.env_with(RHUBARB_USER=rec["username"]))
    if res.returncode != 0:
        raise VerifyError(f"enrollment failed ({a.service})")
    if a.service != "perimeter81":  # P81 is manual; the script only prints instructions
        rec["enrollments"][a.service] = clones.now()
        clones.save(rec)
        clones.log_event("enroll", rec["name"], a.service)


def _destroy(rec: dict) -> None:
    hostops.stop_vm(rec["name"])
    if rec["name"] in hostops.local_vms():
        hostops.tart("delete", rec["name"])
    hostops.forget_host_key(rec["name"])
    if rec["password_account"] == rec["name"]:
        hostops.keychain_delete(rec["name"])


def cmd_reset(a) -> None:
    rec = clones.load(a.name)
    prof = load_profile(rec["profile"])
    image = rec["image"] if a.same_image else image_name(prof)
    if image not in hostops.local_vms():
        raise VerifyError(f"{image} is not built on this Mac")
    _destroy(rec)
    clones.delete(rec["name"])
    clones.log_event("reset", rec["name"], image)
    say(f"{rec['name']}: destroyed (enrollment and identity are gone); re-cloning from {image}")
    _clone(rec["name"], image, prof, rotate=not a.no_rotate)


def cmd_rm(a) -> None:
    rec = clones.load(a.name)
    if not a.yes:
        answer = input(f"Delete clone {rec['name']} (from {rec['image']}) and its keychain entry? [y/N] ")
        if answer.strip().lower() not in ("y", "yes"):
            say("aborted")
            return
    _destroy(rec)
    clones.delete(rec["name"])
    clones.log_event("rm", rec["name"])
    say(f"removed {rec['name']}")


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
