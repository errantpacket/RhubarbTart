"""Profiles: a guest = one base (OS + installer) + packages + VM/desktop settings.

config/bases/<id>.json     OS family, installer source, default VM size
config/packages/<id>.json  one tool, with a variant per OS family (macos / nixos / kali)
profiles/<id>.json         what to build: base + packages + overrides
locks/<id>.lock.json       resolved, reviewed, committed pins for that profile
"""

import hashlib
import json
import re

from .common import ROOT, VerifyError, load_json

BASES = ROOT / "config" / "bases"
PACKAGES = ROOT / "config" / "packages"
PROFILES = ROOT / "profiles"
LOCKS = ROOT / "locks"
FAMILIES = {"macos", "nixos", "kali"}
ID_RE = re.compile(r"^[a-z0-9][a-z0-9-]{1,40}$")
USER_RE = re.compile(r"^[a-z][a-z0-9]{2,15}$")
# Debian-installer's user-setup refuses these as the first user and stops on an error screen,
# which a preseeded install can't get past (it idles until Packer's 120 min timeout). Source:
# /usr/lib/user-setup/reserved-usernames in user-setup-udeb 1.109 (Kali 2026.2 installer ISO),
# filtered to names USER_RE could accept. Note `admin`, the default username. (#25)
DI_RESERVED_USERS = frozenset("""
adm admin alias asterisk audio backup bin bind cdrom ceph crontab cupsys daemon dcc dhcp
dialout dictd dip disk dnsmasq dovecot fax fetchmail firebird floppy ftn ftp fuse games gdm
gnats haclient hacluster haldaemon hplilp identd input irc jwhois klog kmem kvm list
lpadmin mail man messagebus mysql mythtv netdev netplan news nobody nogroup opensrf
operator plugdev powerdev proxy qmail qmaild qmaill qmailp qmailq qmailr qmails radvd
render root saned sasl sbuild scanner shadow slocate slurm src ssh sshd sslwrap staff statd
sudo sync sys syslog tape telnetd tftpd tty users utmp uucp vchkpw video voice vpopmail
""".split())


PROFILE_KEYS = {"id", "description", "base", "packages", "username", "vm", "options"}
VM_LIMITS = {"cpu": (2, 64), "memory_gb": (4, 256), "disk_gb": (40, 2048)}  # Tart Linux needs >= 4 GB
PKG_NAME_RE = re.compile(r"^[a-z0-9][a-z0-9+.-]*$")
# option -> (families it applies to, validator, human description)
OPTIONS = {
    "desktop": ({"nixos", "kali"}, lambda v: v in ("none", "xfce"), '"none" or "xfce"'),
    "rosetta": ({"nixos", "kali"}, lambda v: isinstance(v, bool), "true or false"),
    "kali_metapackages": ({"kali"},
                          lambda v: isinstance(v, list) and all(isinstance(x, str) and PKG_NAME_RE.match(x) for x in v),
                          "a list of Kali package names"),
}


def _validate(pid: str, prof: dict, family: str) -> None:
    """Reject anything the build would otherwise silently ignore."""
    where = f"profiles/{pid}.json"
    unknown = set(prof) - PROFILE_KEYS
    if unknown:
        raise VerifyError(f"{where}: unknown key(s) {sorted(unknown)}; allowed: {sorted(PROFILE_KEYS)}")
    pkgs = prof.get("packages", [])
    if len(pkgs) != len(set(pkgs)):
        raise VerifyError(f"{where}: duplicate packages")
    for key, value in prof.get("vm", {}).items():
        if key not in VM_LIMITS:
            raise VerifyError(f"{where}: unknown vm key {key!r}; allowed: {sorted(VM_LIMITS)}")
        lo, hi = VM_LIMITS[key]
        if not isinstance(value, int) or isinstance(value, bool) or not lo <= value <= hi:
            raise VerifyError(f"{where}: vm.{key} must be an integer in {lo}..{hi}")
    for key, value in prof.get("options", {}).items():
        if key not in OPTIONS:
            raise VerifyError(f"{where}: unknown option {key!r}; allowed: {sorted(OPTIONS)}")
        families, ok, desc = OPTIONS[key]
        if family not in families:
            raise VerifyError(f"{where}: option {key!r} applies to {sorted(families)}, not {family}")
        if not ok(value):
            raise VerifyError(f"{where}: option {key!r} must be {desc}")


def list_profiles() -> list[str]:
    return sorted(p.stem for p in PROFILES.glob("*.json"))


def load_profile(pid: str) -> dict:
    """Profile merged with its base and the per-family variant of each package."""
    if not ID_RE.match(pid):
        raise VerifyError(f"invalid profile id {pid!r}")
    prof = load_json(PROFILES / f"{pid}.json")
    if prof.get("id") != pid:
        raise VerifyError(f"profiles/{pid}.json: id must be {pid!r}")
    base = load_json(BASES / f"{prof['base']}.json")
    family = base.get("family")
    if family not in FAMILIES:
        raise VerifyError(f"config/bases/{prof['base']}.json: unknown family {family!r}")

    _validate(pid, prof, family)
    packages = {}
    for pkg_id in prof.get("packages", []):
        pkg = load_json(PACKAGES / f"{pkg_id}.json")
        variant = pkg.get("variants", {}).get(family)
        if variant is None:
            raise VerifyError(f"package {pkg_id!r} has no {family} variant "
                              f"(config/packages/{pkg_id}.json); remove it from profiles/{pid}.json")
        packages[pkg_id] = variant

    vm = {**base.get("vm", {}), **prof.get("vm", {})}
    username = prof.get("username", "admin")
    if not USER_RE.match(username):
        raise VerifyError(f"profiles/{pid}.json: username must match {USER_RE.pattern}")
    if family == "kali" and username in DI_RESERVED_USERS:
        raise VerifyError(f"profiles/{pid}.json: username {username!r} is reserved by the Debian "
                          f"installer (Kali would stop at an error screen); set \"username\"")
    return {
        "id": pid,
        "description": prof.get("description", ""),
        "family": family,
        "base_id": prof["base"],
        "base": base,
        "packages": packages,
        "vm": vm,
        "username": username,
        "options": prof.get("options", {}),
        # hash of what the profile asks for (sizes, desktop, options) – part of the VM identity
        "profile_sha256": hashlib.sha256(
            json.dumps(prof, sort_keys=True, separators=(",", ":")).encode()).hexdigest(),
    }


def lock_path(pid: str):
    return LOCKS / f"{pid}.lock.json"
