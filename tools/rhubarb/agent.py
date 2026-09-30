"""Scoped range client (#108, charter model A): the *only* tool an agent gets to reach its range.

An agent runs under herdr in a host PTY. Its single way to act in a range VM is this client,
which runs a command in **one assigned clone** through the control-plane socket
(``POST /clones/<clone>/exec``) — so every command is journaled as evidence (#85) and the clone
is fixed, not chosen per call. The agent never holds the clone's password or a raw route.

The assignment comes from the environment set by ``arm`` (slice 2):

    RBT_SERVICE_SOCKET   the control-plane Unix socket (service.py)
    RBT_RANGE_CLONE      the one clone this agent may drive

**Honest boundary.** On the host (model A) the agent runs as the operator, so this client is the
*sanctioned* path and the evidence path — not a kernel sandbox: a shell-escaping agent on the
same host could reach the socket directly. Hard isolation of the agent from other clones is the
per-engagement driver VM (model C); scope to targets is enforced below the guest by the network
(#30). This client makes the intended path convenient and fully recorded.

Thin client of ``service.py``; no tart, no keychain. Run via ``./rbt-range`` (tools/rbt_range.py).
"""

import os
import sys
import time

from . import service
from .common import VerifyError

CLONE_ENV = "RBT_RANGE_CLONE"
SOCKET_ENV = "RBT_SERVICE_SOCKET"


def _decode(b64: str | None) -> bytes:
    import base64
    return base64.b64decode(b64) if b64 else b""


def range_exec(socket_path: str, clone: str, command: str,
               timeout: float | None = None) -> dict:
    """Run ``command`` in ``clone`` via the control-plane service. Returns the ExecResult dict
    (exit_code, decoded stdout/stderr, approval_required, request_id).

    Raises ``VerifyError`` if the service rejects it (unknown/stopped clone, bad input) — the
    scope (which clone) is decided by the caller/environment, never by ``command``.
    """
    out = service.post_json(socket_path, f"/clones/{clone}/exec",
                            {"command": command, **({"timeout": timeout} if timeout else {})})
    return {"exit_code": out.get("exit_code", 1),
            "stdout": _decode(out.get("stdout")), "stderr": _decode(out.get("stderr")),
            "approval_required": bool(out.get("approval_required")),
            "request_id": out.get("request_id")}


def main(argv: list[str] | None = None) -> None:
    """``rbt-range [--timeout N] -- <command...>`` (or without ``--``). Clone + socket come from
    the environment. stdout/stderr/exit code mirror the remote command.

    Like ``ssh host <args>``, the command args are joined with spaces into one line that the
    guest shell parses, so pass a shell-quoted command as a single argument when quoting matters
    (``rbt-range -- "sh -c 'exit 7'"``)."""
    args = list(sys.argv[1:] if argv is None else argv)
    timeout: float | None = None
    if args[:1] == ["--timeout"]:
        if len(args) < 2:
            sys.exit("rbt-range: --timeout needs a value")
        try:
            timeout = float(args[1])
        except ValueError:
            sys.exit("rbt-range: --timeout must be a number")
        args = args[2:]
    if args[:1] == ["--"]:
        args = args[1:]
    if not args:
        sys.exit("usage: rbt-range [--timeout N] -- <command...>")

    socket_path = os.environ.get(SOCKET_ENV)
    clone = os.environ.get(CLONE_ENV)
    if not socket_path or not clone:
        sys.exit(f"rbt-range: {SOCKET_ENV} and {CLONE_ENV} must be set (this runs inside an armed "
                 "engagement)")

    command = " ".join(args)
    # Tiered commands (#108) are held for operator approval: notify once, then poll until a
    # single-use approval lands or we time out. RBT_APPROVAL_WAIT=0 disables waiting.
    wait_total = float(os.environ.get("RBT_APPROVAL_WAIT", "600"))
    poll = float(os.environ.get("RBT_APPROVAL_POLL", "5"))
    deadline = time.time() + wait_total
    notified = False
    while True:
        try:
            res = range_exec(socket_path, clone, command, timeout=timeout)
        except VerifyError as e:
            sys.exit(f"rbt-range: {e}")
        if not res["approval_required"]:
            break
        if not notified:
            sys.stderr.write(f"rbt-range: APPROVAL REQUIRED for this command on clone {clone!r} "
                             f"(request {res['request_id']}). A reviewer must run: "
                             f"rhubarb herdr approve <engagement> {res['request_id']}\n")
            sys.stderr.flush()
            notified = True
        if wait_total <= 0 or time.time() >= deadline:
            sys.exit(f"rbt-range: not approved within {wait_total:.0f}s (request {res['request_id']})")
        time.sleep(poll)
    sys.stdout.buffer.write(res["stdout"])
    sys.stdout.buffer.flush()
    sys.stderr.buffer.write(res["stderr"])
    code = res["exit_code"]
    sys.exit(code if 0 <= code < 256 else 1)
