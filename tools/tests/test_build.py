"""Self-tests: build-side rules and guest scripts (profile usernames, package lists, sshd settings, the Kali sudo allowlist, build cleanup, Chrome policy, offline signing, password rotation, guest sync)."""

import json
import os
import subprocess
import tempfile
from pathlib import Path

from tests.support import ROOT, _stub, check


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
    root = ROOT
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
    root = ROOT
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
    root = ROOT
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
    root = ROOT
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


def test_chrome_update_policy() -> None:
    """macOS: install.sh's Keystone policy parses and sets UpdateDefault 2 (manual only) for both
    scopes, and the seal and smoke test assert that same file and value. (#29)"""
    import plistlib
    import re
    root = ROOT
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
    root = ROOT
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
