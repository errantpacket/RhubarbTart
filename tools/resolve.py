# /// script
# requires-python = ">=3.12"
# dependencies = []
# ///
"""Resolve, verify and lock every input of a RhubarbTart guest profile.

  list                         profiles and their bases/packages
  plan <profile>               latest pins from upstream metadata (no downloads)
  resolve <profile>            plan + download + verify + write locks/<profile>.lock.json
          [--skip-large]       ...without downloading OS images (hashes still pinned)
  verify <profile>             re-check cached inputs vs the lock; build the guest stage dir
  provenance <profile> <vm>    write out/<vm>.provenance.json
  preflight                    repo-local toolchain matches config/toolchain.env
  toolchain-pin [--latest]     re-derive config/toolchain.env (two sources must agree)

Where each step can run:
  macOS profiles    resolve + verify need macOS (codesign/pkgutil/spctl).
  NixOS/Kali        resolve needs only gpg (any OS); verify on the build host is hash-only.
"""

import argparse
import datetime as dt
import json
import platform
import shutil
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from rhubarb import distsign, macos, nar  # noqa: E402
from rhubarb.bases import PLANNERS  # noqa: E402
from rhubarb.common import CACHE, ROOT, VerifyError, download, get_bytes, log, sha256_file  # noqa: E402
from rhubarb.packages import plan_package  # noqa: E402
from rhubarb.locks import artifacts, inputs_sha256, load_lock  # noqa: E402
from rhubarb.profiles import list_profiles, load_profile, lock_path  # noqa: E402
from rhubarb.toolchain import cmd_preflight, cmd_toolchain_pin, toolchain  # noqa: E402

MACOS_KINDS = {"pkg", "dmg"}


# ---- helpers ---------------------------------------------------------------------------

def fetch(entry: dict, dest: Path) -> None:
    if dest.exists():
        return
    if entry.get("local_path"):
        src = ROOT / entry["local_path"]
        if not src.exists():
            raise VerifyError(f"missing local file {entry['local_path']} (download it from the vendor portal)")
        shutil.copy2(src, dest)
    else:
        download(entry["url"], dest)


def plan(prof: dict) -> dict:
    return {
        "base": PLANNERS[prof["family"]](prof["base"]),
        "packages": {pid: plan_package(pid, v) for pid, v in prof["packages"].items()},
    }


# ---- commands --------------------------------------------------------------------------

def cmd_list(_args) -> None:
    for pid in list_profiles():
        try:
            p = load_profile(pid)
            print(f"{pid:24} {p['base_id']:14} {', '.join(p['packages']) or '-'}")
        except VerifyError as e:
            print(f"{pid:24} INVALID: {e}")


def cmd_plan(args) -> dict:
    prof = load_profile(args.profile)
    result = plan(prof)
    print(json.dumps(result, indent=2))
    return result


def cmd_resolve(args) -> None:
    prof = load_profile(args.profile)
    if prof["family"] == "macos" and platform.system() != "Darwin":
        raise VerifyError("macOS profiles must be resolved on macOS (signature checks need codesign/pkgutil)")
    p = plan(prof)
    cache = CACHE / "artifacts"
    cache.mkdir(parents=True, exist_ok=True)
    ts_keys = None

    for label, e in artifacts(p):
        path = cache / e["file"]
        if e.get("large") and args.skip_large:
            log(f"{label}: skipping download of {e['file']} (hash pinned: {e['sha256'][:16]}…)")
            continue
        if path.exists() and e.get("sha256") is None:
            path.unlink()  # TOFU / unversioned source: always re-fetch on resolve
        fetch(e, path)
        got = sha256_file(path)
        if e.get("sha256") and got != e["sha256"]:
            path.unlink()
            raise VerifyError(f"{label}: sha256 mismatch {got} != {e['sha256']}")
        e["sha256"], e["size"] = got, path.stat().st_size
        if label == "base:nixpkgs":
            e["nar_sha256"] = nar.tarball_nar_sha256(path)
            log(f"nixpkgs {e['rev'][:12]}: nar {e['nar_sha256']}")
        if e.get("distsign"):
            ts_keys = ts_keys or distsign.signing_keys(
                ["tailscale-distsign-root-crawshaw.pem", "tailscale-distsign-root-prod-1.pem"])
            distsign.verify_file(path, get_bytes(e["url"] + ".sig"), ts_keys)
            log(f"{label}: distsign signature ok")
        variant = prof["packages"].get(label, {})
        if variant.get("kind") in MACOS_KINDS:
            if variant.get("signed", True):
                e["signature"] = macos.check_signature(label, variant, path)
            elif not e.get("sha256"):
                # signed=false trades Apple's signature for an exact pinned hash; without one
                # there is no provenance at all, so refuse it.
                raise VerifyError(f"{label}: unsigned macOS artifact needs a pinned sha256")
            if variant.get("resolver") == "chrome-mac":
                e["version"] = macos.pkg_ref_version(path, variant["pkg_ref"])
        log(f"{label} ok: {e.get('version', '')} sha256={got[:16]}…")

    lock = {
        "schema": 2,
        "profile": prof["id"],
        "family": prof["family"],
        "base_id": prof["base_id"],
        "profile_sha256": prof["profile_sha256"],
        "resolved_at": dt.datetime.now(dt.UTC).isoformat(timespec="seconds"),
        "resolved_on": {"host_os": platform.platform()},
        **p,
    }
    path = lock_path(prof["id"])
    path.parent.mkdir(exist_ok=True)
    path.write_text(json.dumps(lock, indent=2) + "\n")
    log(f"wrote {path.relative_to(ROOT)} — review the diff and commit it")


def cmd_verify(args) -> None:
    prof = load_profile(args.profile)
    lock = load_lock(prof)
    cache = CACHE / "artifacts"
    cache.mkdir(parents=True, exist_ok=True)
    stage = CACHE / "stage" / prof["id"]
    if stage.exists():
        shutil.rmtree(stage)
    stage.mkdir(parents=True)

    sums, manifest = [], []
    for label, e in artifacts(lock):
        path = cache / e["file"]
        if label == "base:image" and args.skip_image:
            log("base:image: skipped (--skip-image; dev/CI only, the build needs it)")
            continue
        fetch(e, path)
        if sha256_file(path) != e["sha256"]:
            raise VerifyError(f"{label}: cached {e['file']} differs from lock. If upstream moved "
                              "(an unversioned URL such as Chrome's), re-resolve.")
        variant = prof["packages"].get(label, {})
        if variant.get("kind") in MACOS_KINDS and variant.get("signed", True):
            sig = macos.check_signature(label, variant, path)
            if sig["team_id"] != e["signature"]["team_id"]:
                raise VerifyError(f"{label}: Team ID changed since lock ({sig['team_id']})")
        if label != "base:image":  # the OS image is attached by Packer, everything else is staged
            shutil.copy2(path, stage / e["file"])
            sums.append(f"{e['sha256']}  {e['file']}")
        log(f"{label} ok")

    for pid, e in lock["packages"].items():
        v = prof["packages"][pid]
        # 6th column: "1" = Apple-signature verified in-guest, "0" = unsigned, integrity from
        # SHA256SUMS only (see guest/macos/install.sh). Default signed.
        signed = "1" if v.get("signed", True) else "0"
        manifest.append("\t".join([pid, v.get("kind", v["resolver"]), e.get("file", "-"),
                                   e.get("signature", {}).get("team_id", "-"),
                                   v.get("app", e.get("package", "-")), signed]))
    (stage / "SHA256SUMS").write_text("\n".join(sums) + "\n" if sums else "")
    (stage / "packages.tsv").write_text("\n".join(manifest) + "\n" if manifest else "")
    (stage / "profile.json").write_text(json.dumps({
        "id": prof["id"], "family": prof["family"], "base": prof["base_id"],
        "username": prof["username"], "options": prof["options"],
        "packages": {pid: {**prof["packages"][pid], **{k: e[k] for k in ("file", "version", "package")
                                                         if k in e}}
                     for pid, e in lock["packages"].items()},
        "nixpkgs": lock["base"].get("nixpkgs"),
    }, indent=2) + "\n")
    shutil.copy2(lock_path(prof["id"]), stage / "lock.json")

    base = lock["base"]
    print(json.dumps({
        "profile": prof["id"], "family": prof["family"], "base_id": prof["base_id"],
        "image_path": str(cache / base["image"]["file"]), "stage_dir": str(stage),
        "os_build": base.get("build") or base.get("version") or base.get("release"),
        "inputs_sha256": inputs_sha256(lock), "username": prof["username"],
        "cpu": prof["vm"].get("cpu", 4), "memory_gb": prof["vm"].get("memory_gb", 8),
        "disk_gb": prof["vm"].get("disk_gb", 80), "rosetta": bool(prof["options"].get("rosetta")),
    }))


def ssh_record(keys_file: str | None, ssh_from: str) -> dict:
    """What SSH access the image was sealed with. Without it a key-less build (SSH disabled,
    smoke test asserting port 22 *closed*) is indistinguishable from a working one (#46)."""
    lines = []
    if keys_file and Path(keys_file).is_file():
        lines = [ln for ln in Path(keys_file).read_text().splitlines()
                 if ln.strip() and not ln.lstrip().startswith("#")]
    fps = []
    for ln in lines:
        res = subprocess.run(["ssh-keygen", "-lf", "/dev/stdin"], input=ln + "\n",
                             capture_output=True, text=True)
        if res.returncode != 0:
            raise VerifyError(f"cannot fingerprint authorized key: {ln[:40]}…")
        fps.append(res.stdout.split()[1])  # "256 SHA256:... comment (ED25519)"
    return {"enabled": bool(fps), "key_fingerprints": fps, "from": ssh_from if fps else ""}


def cmd_provenance(args) -> None:
    prof = load_profile(args.profile)
    lock = load_lock(prof)
    tracked = sorted(p for d in ("config", "profiles", "packer", "guest", "nix", "kali", "scripts", "tools")
                     if (ROOT / d).exists() for p in (ROOT / d).rglob("*")
                     if p.is_file() and "__pycache__" not in p.parts)
    vm_info = subprocess.run(["tart", "get", args.vm, "--format", "json"], capture_output=True, text=True)
    git = subprocess.run(["git", "-C", str(ROOT), "rev-parse", "HEAD"], capture_output=True, text=True)
    dirty = subprocess.run(["git", "-C", str(ROOT), "status", "--porcelain"], capture_output=True, text=True)
    record = {
        "vm": args.vm, "profile": prof["id"],
        "built_at": dt.datetime.now(dt.UTC).isoformat(timespec="seconds"),
        "git_commit": git.stdout.strip() or None, "git_dirty": bool(dirty.stdout.strip()),
        "toolchain": toolchain(),
        "tart_vm": json.loads(vm_info.stdout) if vm_info.returncode == 0 else None,
        "inputs_sha256": inputs_sha256(lock),
        "lock_sha256": sha256_file(lock_path(prof["id"])),
        "lock": lock,
        "build_files": {str(p.relative_to(ROOT)): sha256_file(p) for p in tracked},
        "ssh": ssh_record(args.ssh_keys, args.ssh_from),
    }
    out = ROOT / "out" / f"{args.vm}.provenance.json"
    out.parent.mkdir(exist_ok=True)
    out.write_text(json.dumps(record, indent=2) + "\n")
    print(out)


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("list").set_defaults(fn=cmd_list)
    p = sub.add_parser("plan")
    p.add_argument("profile")
    p.set_defaults(fn=cmd_plan)
    v = sub.add_parser("verify")
    v.add_argument("profile")
    v.add_argument("--skip-image", action="store_true", help="don't fetch/check the OS image (dev/CI)")
    v.set_defaults(fn=cmd_verify)
    r = sub.add_parser("resolve")
    r.add_argument("profile")
    r.add_argument("--skip-large", action="store_true", help="don't download OS images now")
    r.set_defaults(fn=cmd_resolve)
    pv = sub.add_parser("provenance")
    pv.add_argument("profile")
    pv.add_argument("vm")
    pv.add_argument("--ssh-keys", metavar="FILE",
                    help="the authorized_keys baked into the image (empty/absent => SSH disabled)")
    pv.add_argument("--ssh-from", default="", help="the authorized_keys from= pattern used")
    pv.set_defaults(fn=cmd_provenance)
    sub.add_parser("preflight").set_defaults(fn=cmd_preflight)
    tp = sub.add_parser("toolchain-pin")
    tp.add_argument("--latest", action="store_true", help="bump to the newest upstream releases")
    tp.set_defaults(fn=cmd_toolchain_pin)
    args = ap.parse_args()
    try:
        args.fn(args)
    except (VerifyError, subprocess.CalledProcessError) as e:
        sys.exit(f"[resolve] FAILED: {e}")


if __name__ == "__main__":
    main()
