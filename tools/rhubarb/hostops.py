"""Host-side operations for the `rhubarbtart` CLI: tart, keychain, SSH, password rotation.

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


# tart enriches `list`/`get` with each VM's disk-image info and aborts the whole command if any
# one is unreadable — printing this. A *running* VM holds an exclusive lock on its disk.img, so
# this happens for the whole time it runs (not just transiently after a clone/stop). See #16/#21.
_DISK_BUSY = "Resource temporarily unavailable"
VMS_DIR = Path.home() / ".tart" / "vms"


def _tart_run_procs() -> list[tuple[str, str]]:
    """(pid, vm_name) for each live `tart run <name>` process — the VM name is the final arg."""
    procs = []
    found = subprocess.run(["pgrep", "-f", "tart run"], capture_output=True, text=True)
    for pid in found.stdout.split():
        cmd = subprocess.run(["ps", "-o", "command=", "-p", pid], capture_output=True, text=True).stdout
        toks = cmd.split()
        if toks and "run" in toks:
            procs.append((pid, toks[-1]))
    return procs


def _fs_vms() -> dict[str, dict]:
    """Disk-free VM enumeration: local VM directories + running state from the process table.

    The fallback when `tart list` can't read a running VM's locked disk (#21). Never reads
    disk.img, so it works while clones are up. Provides {Name, Source, Running, State} — all the
    CLI/TUI need (Disk size and other tart-only fields aren't used by images()/clones()).
    """
    running = {vm for _pid, vm in _tart_run_procs()}
    out = {}
    if VMS_DIR.is_dir():
        for d in sorted(VMS_DIR.iterdir()):
            if d.is_dir():
                up = d.name in running
                out[d.name] = {"Name": d.name, "Source": "local", "Running": up,
                               "State": "running" if up else "stopped"}
    return out


def local_vms() -> dict[str, dict]:
    res = tart("list", "--format", "json", capture=True, check=False)
    if res.returncode == 0:
        return {v["Name"]: v for v in json.loads(res.stdout or "[]") if v.get("Source") == "local"}
    # A running VM's locked disk (or a transient EAGAIN right after clone/stop) makes tart abort
    # the whole listing. Fall back to the disk-free enumeration so listing — and running state —
    # keep working even while clones are up (#16/#21).
    if _DISK_BUSY in (res.stderr or ""):
        return _fs_vms()
    raise VerifyError(f"tart list failed: {(res.stderr or '').strip() or 'unknown error'}")


def delete_vm(name: str) -> bool:
    """Delete a VM by name — no listing needed, so it works even when `list` is degraded.

    Returns whether the VM existed; tolerates it already being gone.
    """
    res = tart("delete", name, capture=True, check=False)
    if res.returncode == 0:
        return True
    low = (res.stderr or "").lower()
    if "does not exist" in low or "doesn't exist" in low or "not found" in low:
        return False
    raise VerifyError(f"{name}: could not delete: {(res.stderr or '').strip() or 'unknown error'}")


def is_running(name: str) -> bool:
    return bool(local_vms().get(name, {}).get("Running"))


def vm_ip(name: str, family: str, wait: int = 180) -> str:
    """DHCP-lease lookup first; ARP for Linux guests whose DHCP client-id isn't the MAC."""
    for extra in ([], ["--resolver", "arp"]) if family != "macos" else ([],):
        res = tart("ip", "--wait", str(wait), *extra, name, capture=True, check=False)
        if res.returncode == 0 and res.stdout.strip():
            return res.stdout.strip()
    raise VerifyError(f"{name}: no IP address (is it running?)")


# The Virtualization framework's view of Rosetta for Linux VMs: 0 unsupported, 1 not installed,
# 2 installed. Not `arch -x86_64`, which fails on macOS 27 even when Linux Rosetta works (#158).
_ROSETTA_JS = ('ObjC.import("Foundation"); '
               '$.NSBundle.bundleWithPath("/System/Library/Frameworks/Virtualization.framework").load; '
               '$.NSClassFromString("VZLinuxRosettaDirectoryShare").availability')


def rosetta_availability() -> str:
    """"installed", "not-installed", "unsupported", or "unknown" if it can't be checked."""
    try:
        res = subprocess.run(["osascript", "-l", "JavaScript", "-e", _ROSETTA_JS],
                             capture_output=True, text=True, timeout=20)
    except (OSError, subprocess.SubprocessError):
        return "unknown"
    return {"2": "installed", "1": "not-installed", "0": "unsupported"}.get(res.stdout.strip(),
                                                                         "unknown")


def require_rosetta(name: str) -> None:
    """Refuse to start a Rosetta clone on a Mac without Rosetta: `tart run --rosetta` would fail
    and the clone would never get an IP. Unknown is let through (the check itself may not work)."""
    state = rosetta_availability()
    if state == "not-installed":
        raise VerifyError(f"{name} uses Rosetta, which isn't installed on this Mac. Install it, then "
                          "try again: softwareupdate --install-rosetta --agree-to-license")
    if state == "unsupported":
        raise VerifyError(f"{name} uses Rosetta, which this Mac doesn't support")


def start_vm(name: str, rosetta: bool, headless: bool, log: Path | None = None) -> subprocess.Popen:
    if rosetta:
        require_rosetta(name)
    args = ["tart", "run", *(["--rosetta=rosetta"] if rosetta else []),
            *(["--no-graphics"] if headless else []), name]
    out = open(log, "ab") if log else subprocess.DEVNULL  # noqa: SIM115 (handed to the child)
    return subprocess.Popen(args, stdout=out, stderr=out, stdin=subprocess.DEVNULL,
                            start_new_session=True)


def sync_guest(name: str, username: str) -> bool:
    """Best-effort `sync` inside a running clone over its pinned SSH, before stopping it.

    `tart stop` ends a Linux guest about 2 s into its shutdown, before the filesystem is flushed,
    so recent writes (host keys, machine-id, logs) would be lost (#91). `sync` needs no
    privileges. Never blocks a stop: returns False on any failure (no IP, no SSH, timeout).
    """
    res = tart("ip", name, capture=True, check=False)
    ip = (res.stdout or "").strip()
    if res.returncode != 0 or not ip:
        return False
    try:
        return subprocess.run([*ssh_args(name, username, ip), "sync"], capture_output=True,
                              text=True, timeout=20).returncode == 0
    except subprocess.TimeoutExpired:
        return False


def stop_vm(name: str) -> None:
    if is_running(name):
        tart("stop", name, capture=True, check=False)
        for _ in range(60):
            if not is_running(name):
                return
            time.sleep(1)
        raise VerifyError(f"{name}: did not stop within 60s")


def reap_run(name: str) -> None:
    """Force-kill any lingering `tart run <name>` process (exact final-arg match). Best-effort.

    A detached `tart run` (start_new_session — from `run --detach`, or a rotation whose graceful
    stop timed out) can outlive us and keep the VM's disk image locked, wedging all listing
    (#16/#17/#18). The VM name is matched only as the final argument, so a different VM (e.g.
    `web-10` vs `web-1`) is never touched.
    """
    for pid, vm in _tart_run_procs():
        if vm == name:
            subprocess.run(["kill", pid], capture_output=True)


def shutdown(name: str, proc: subprocess.Popen | None = None, timeout: int = 60) -> None:
    """Stop the VM and GUARANTEE nothing is left running it (#17, #18).

    start_vm() detaches the `tart run` into its own session, so if it outlives us it keeps the
    VM's disk image locked and wedges all listing. This is belt-and-suspenders on any failure
    path (a finally): (1) a polite `tart stop`; (2) reap the Popen we hold (new's rotation boot) —
    wait, terminate, kill; (3) force-kill any detached `tart run <name>` we don't hold (a
    `run --detach`, or a macOS guest that ignored the graceful stop) so teardown never orphans.
    """
    try:
        stop_vm(name)
    except VerifyError:
        pass  # graceful stop may fail/timeout (headless macOS); the reap below is the guarantee
    if proc is not None and proc.poll() is None:
        try:
            proc.wait(timeout=timeout)
        except subprocess.TimeoutExpired:
            proc.terminate()
            try:
                proc.wait(timeout=10)
            except subprocess.TimeoutExpired:
                proc.kill()
                proc.wait(timeout=10)
    reap_run(name)


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
    """Host key pinned per VM *name* (DHCP reuses IPs), trust-on-first-use, no forwarding.

    ``-F /dev/null``: the operator's ~/.ssh/config (Host *, ProxyJump, IdentityFile, …) must not
    change how we reach a clone. Keys come from the agent and the default ~/.ssh/id_* files, or
    only from ``RHUBARB_SSH_IDENTITY`` when that is set.
    """
    ident = os.environ.get("RHUBARB_SSH_IDENTITY")
    return ["ssh", "-F", "/dev/null", "-o", f"HostKeyAlias={name}",
            "-o", f"UserKnownHostsFile={KNOWN_HOSTS}",
            "-o", "StrictHostKeyChecking=accept-new", "-o", "ForwardAgent=no", "-o", "ForwardX11=no",
            *(["-i", ident, "-o", "IdentitiesOnly=yes"] if ident else []),
            "-o", "ConnectTimeout=10", *(["-o", "BatchMode=yes"] if batch else []), f"{username}@{ip}"]


# ssh stderr while the guest is still booting (sshd not up yet, stealth firewall, stale lease).
# Anything else from a failed ssh is a client-side problem that waiting won't fix.
_SSH_TRANSIENT = ("Connection refused", "timed out", "No route to host", "Host is down",
                  "Network is unreachable", "Connection reset", "Connection closed",
                  "kex_exchange_identification", "banner exchange")


def forget_host_key(name: str) -> None:
    if KNOWN_HOSTS.exists():
        subprocess.run(["ssh-keygen", "-R", name, "-f", str(KNOWN_HOSTS)], capture_output=True)


DENIED_CONFIRMATIONS = 3      # consecutive key refusals before concluding the key is wrong (#88)


def wait_for_ssh(name: str, username: str, ip: str, timeout: int | None = None,
                 progress=None) -> str:
    """'ok' | 'denied' (reachable, but our key isn't accepted: final) | 'unreachable'.

    Raises ``VerifyError`` with ssh's own message on a client-side failure (bad identity or
    SK provider, host key mismatch, config error): retrying can't fix those, and reporting
    them as 'unreachable' after the full wait sends the operator after a healthy guest. (#46)

    A key refusal is only final after ``DENIED_CONFIRMATIONS`` in a row, a few seconds apart:
    a freshly booted guest can refuse once while it's still coming up (seen on Kali, #88),
    whereas a genuinely wrong key keeps being refused.

    ``progress`` (optional callable): emits a "waiting for SSH … Ns/Ms" line every ~20s so a
    long wait (a slow macOS first-boot clone) isn't a silent stall.
    """
    timeout = timeout or int(os.environ.get("RHUBARB_SSH_WAIT", "180"))
    denied = 0
    start = time.time()
    deadline = start + timeout
    ticked = 0
    retry = float(os.environ.get("RHUBARB_SSH_DENIED_RETRY", "5"))
    # Once a refusal has started, finish confirming it even past the deadline, so a wrong key is
    # reported as 'denied', never as 'unreachable'.
    while time.time() < deadline or 0 < denied < DENIED_CONFIRMATIONS:
        res = subprocess.run([*ssh_args(name, username, ip), "true"], capture_output=True, text=True)
        if res.returncode == 0:
            return "ok"
        if "Permission denied" in res.stderr:
            denied += 1
            if denied >= DENIED_CONFIRMATIONS:
                return "denied"
            time.sleep(retry)
            continue
        denied = 0
        if not any(t in res.stderr for t in _SSH_TRANSIENT):
            detail = res.stderr.strip().splitlines()[-1:] or [f"ssh exited {res.returncode}"]
            raise VerifyError(f"{name}: ssh failed on the host side, not waiting for it: {detail[0]}")
        elapsed = int(time.time() - start)
        if progress and elapsed - ticked >= 20:
            ticked = elapsed
            progress(f"{name}: waiting for SSH at {ip} … {elapsed}s/{timeout}s")
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
# `tart stop` cuts a Linux guest's shutdown short (no final flush, #91): make the new password
# (and anything else this first boot wrote, e.g. host keys) durable before rhubarb stops the VM.
sync
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
