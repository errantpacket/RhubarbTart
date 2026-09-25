"""Host-side operations for the `rhubarb` CLI: tart, keychain, SSH, password rotation.

Secrets (VM passwords) only ever travel: keychain -> this process -> subprocess stdin.
They are never placed in argv, environment variables, files, or log output.
"""

import base64
import json
import os
import secrets
import string
import subprocess
import time
from pathlib import Path

from .common import VerifyError

KEYCHAIN_SERVICE = "RhubarbTart"
KNOWN_HOSTS = Path.home() / ".ssh" / "known_hosts_rhubarbtart"


# ---- tart ---------------------------------------------------------------------------------

def tart(*args: str, capture: bool = False, check: bool = True) -> subprocess.CompletedProcess:
    return subprocess.run(["tart", *args], check=check, text=True,
                          stdout=subprocess.PIPE if capture else None,
                          stderr=subprocess.PIPE if capture else None)


def local_vms() -> dict[str, dict]:
    out = tart("list", "--format", "json", capture=True).stdout
    return {v["Name"]: v for v in json.loads(out or "[]") if v.get("Source") == "local"}


def is_running(name: str) -> bool:
    return bool(local_vms().get(name, {}).get("Running"))


def vm_ip(name: str, family: str, wait: int = 180) -> str:
    """DHCP-lease lookup first; ARP for Linux guests whose DHCP client-id isn't the MAC."""
    for extra in ([], ["--resolver", "arp"]) if family != "macos" else ([],):
        res = tart("ip", "--wait", str(wait), *extra, name, capture=True, check=False)
        if res.returncode == 0 and res.stdout.strip():
            return res.stdout.strip()
    raise VerifyError(f"{name}: no IP address (is it running?)")


def start_vm(name: str, rosetta: bool, headless: bool, log: Path | None = None) -> subprocess.Popen:
    args = ["tart", "run", *(["--rosetta=rosetta"] if rosetta else []),
            *(["--no-graphics"] if headless else []), name]
    out = open(log, "ab") if log else subprocess.DEVNULL  # noqa: SIM115 (handed to the child)
    return subprocess.Popen(args, stdout=out, stderr=out, stdin=subprocess.DEVNULL,
                            start_new_session=True)


def stop_vm(name: str) -> None:
    if is_running(name):
        tart("stop", name, capture=True, check=False)
        for _ in range(60):
            if not is_running(name):
                return
            time.sleep(1)
        raise VerifyError(f"{name}: did not stop within 60s")


# ---- keychain -----------------------------------------------------------------------------

def keychain_get(account: str) -> str | None:
    res = subprocess.run(["security", "find-generic-password", "-s", KEYCHAIN_SERVICE,
                          "-a", account, "-w"], capture_output=True, text=True)
    return res.stdout.rstrip("\n") if res.returncode == 0 else None


def keychain_put(account: str, password: str) -> None:
    # `security -i` reads commands from stdin, keeping the secret out of argv.
    if not password.isalnum():
        raise VerifyError("refusing non-alphanumeric password (it is embedded in a security(1) command)")
    cmd = f"add-generic-password -U -s {KEYCHAIN_SERVICE} -a {account} -w {password}\n"
    subprocess.run(["security", "-i"], input=cmd, text=True, check=True, capture_output=True)
    if keychain_get(account) != password:
        raise VerifyError(f"could not store the password for {account} in the keychain")


def keychain_delete(account: str) -> None:
    subprocess.run(["security", "delete-generic-password", "-s", KEYCHAIN_SERVICE, "-a", account],
                   capture_output=True)


def random_password(n: int = 32) -> str:
    alphabet = string.ascii_letters + string.digits
    return "".join(secrets.choice(alphabet) for _ in range(n))


# ---- ssh ------------------------------------------------------------------------------------

def ssh_args(name: str, username: str, ip: str, batch: bool = True) -> list[str]:
    """Host key pinned per VM *name* (DHCP reuses IPs), trust-on-first-use, no forwarding."""
    return ["ssh", "-o", f"HostKeyAlias={name}", "-o", f"UserKnownHostsFile={KNOWN_HOSTS}",
            "-o", "StrictHostKeyChecking=accept-new", "-o", "ForwardAgent=no", "-o", "ForwardX11=no",
            "-o", "ConnectTimeout=10", *(["-o", "BatchMode=yes"] if batch else []), f"{username}@{ip}"]


def forget_host_key(name: str) -> None:
    if KNOWN_HOSTS.exists():
        subprocess.run(["ssh-keygen", "-R", name, "-f", str(KNOWN_HOSTS)], capture_output=True)


def wait_for_ssh(name: str, username: str, ip: str, timeout: int | None = None) -> str:
    """'ok' | 'denied' (reachable, but our key isn't accepted: final) | 'unreachable'."""
    timeout = timeout or int(os.environ.get("RHUBARB_SSH_WAIT", "180"))
    deadline = time.time() + timeout
    while time.time() < deadline:
        res = subprocess.run([*ssh_args(name, username, ip), "true"], capture_output=True, text=True)
        if res.returncode == 0:
            return "ok"
        if "Permission denied" in res.stderr:
            return "denied"
        time.sleep(3)
    return "unreachable"


# ---- per-clone password rotation -------------------------------------------------------------
# Runs as the guest's admin user. stdin: line 1 = current password, line 2 = new password.
# Written for macOS /bin/bash 3.2 as well as Linux bash. `sudo -S` reads the first stdin line
# as the password and passes the rest of stdin to the command.
ROTATE_SCRIPT = r'''set -eu
IFS= read -r OLD
IFS= read -r NEW
U="$(id -un)"
sudo -k
if [ "$(uname -s)" = Darwin ]; then
  # dscl -passwd with the old password keeps SecureToken in sync. The new password is in
  # dscl's argv inside the guest for an instant (macOS offers no stdin form).
  printf '%s\n' "$OLD" | sudo -S -p '' dscl . -passwd "/Users/$U" "$OLD" "$NEW"
elif [ -e /etc/NIXOS ]; then
  # NixOS (users.mutableUsers = false) rebuilds /etc/shadow from this hash file at every
  # activation: update the file AND apply now.
  H="$(printf '%s' "$NEW" | mkpasswd -m yescrypt -s)"
  { printf '%s\n' "$OLD"; printf '%s\n' "$H"; } | sudo -S -p '' sh -c '
    IFS= read -r H; umask 077
    printf "%s\n" "$H" > /var/lib/rhubarbtart/password.hash.new
    mv -f /var/lib/rhubarbtart/password.hash.new /var/lib/rhubarbtart/password.hash
    printf "%s:%s\n" "$1" "$H" | chpasswd -e' sh "$U"
else
  { printf '%s\n' "$OLD"; printf '%s:%s\n' "$U" "$NEW"; } | sudo -S -p '' chpasswd
fi
# Prove it through PAM: the old password must be rejected, the new one accepted.
sudo -k
if printf '%s\n' "$OLD" | sudo -S -p '' -v 2>/dev/null; then echo "old password still accepted" >&2; exit 1; fi
sudo -k
printf '%s\n' "$NEW" | sudo -S -p '' -v 2>/dev/null || { echo "new password rejected" >&2; exit 1; }
sudo -k
echo ROTATED
'''


def remote_script_cmd(script: str) -> str:
    """A remote command that runs `script` with bash, leaving stdin free for secrets."""
    b64 = base64.b64encode(script.encode()).decode()
    return f"bash -c \"$(printf '%s' {b64} | base64 --decode)\""


def rotate_password(name: str, username: str, ip: str, old: str, new: str) -> None:
    res = subprocess.run([*ssh_args(name, username, ip), remote_script_cmd(ROTATE_SCRIPT)],
                         input=f"{old}\n{new}\n", text=True, capture_output=True)
    if res.returncode != 0 or "ROTATED" not in res.stdout:
        err = (res.stderr or res.stdout).strip().splitlines()[-1:] or ["unknown error"]
        raise VerifyError(f"{name}: password rotation failed: {err[0]}")


def env_with(**extra: str) -> dict:
    env = dict(os.environ)
    env.update(extra)
    return env
