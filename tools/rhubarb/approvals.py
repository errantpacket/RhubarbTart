"""Tiered-action approvals (#108 slice 3, charter): some agent commands pause for the operator.

An engagement's herdr config (``engagements/<id>.herdr.json``) may mark commands **tiered** with
a list of regexes. When an agent runs a matching command through the control-plane exec path, it
is **held for approval** instead of running: a ``requested`` entry lands in the engagement's
evidence, and the operator releases it with ``rhubarb herdr approve``. Each grant is **single-use**
— one approval authorizes one run — and the whole exchange (request, grant, consume) is evidence,
so a sealed vault shows exactly which sensitive actions were permitted and when.

The ledger *is* the evidence journal (evidence.py), so the service (which runs the command) and
the CLI (which grants) coordinate through it with no extra state. This is a workflow guardrail, not
a kernel boundary — the charter's honest boundary for the host model still applies.

No ``herdr``/``api`` import (avoids a cycle): reads the config file directly and the journal via
evidence.py.
"""

import hashlib
import json
import re

from . import evidence as _evidence
from .common import ROOT


def _config_path(engagement: str):
    return ROOT / "engagements" / f"{engagement}.herdr.json"


def tiered_patterns(engagement: str) -> list[re.Pattern]:
    """Compiled tiered regexes from the engagement's herdr config; [] if none/absent. Invalid
    patterns are skipped here (herdr.load_config validates them at write/arm time)."""
    path = _config_path(engagement)
    if not path.is_file():
        return []
    try:
        raw = json.loads(path.read_text())
    except ValueError:
        return []
    out = []
    for pat in raw.get("tiered") or []:
        try:
            out.append(re.compile(pat))
        except re.error:
            continue
    return out


def needs_approval(engagement: str, command: str) -> bool:
    return any(p.search(command) for p in tiered_patterns(engagement))


def request_id(clone: str, command: str) -> str:
    """A stable id for one (clone, command): the same tiered command reuses its request/grant."""
    return hashlib.sha256(f"{clone}\x00{command}".encode()).hexdigest()[:16]


def _counts(engagement: str, rid: str) -> tuple[int, int, int]:
    requested = granted = consumed = 0
    for e in _evidence.entries(engagement):
        d = e.get("data", {})
        if e.get("kind") != "approval" or d.get("request_id") != rid:
            continue
        state = d.get("state")
        requested += state == "requested"
        granted += state == "granted"
        consumed += state == "consumed"
    return requested, granted, consumed


def _status(engagement: str, rid: str) -> str:
    """'approved' (an unused grant is waiting), 'pending' (asked, not yet approved), or 'none'."""
    requested, granted, consumed = _counts(engagement, rid)
    if granted > consumed:
        return "approved"
    if requested > consumed:
        return "pending"
    return "none"


def is_approved_and_consume(engagement: str, clone: str, command: str) -> bool:
    """If an unused grant exists for this (clone, command), consume one (record it) and return
    True; else False. Single-use: the next identical tiered command needs a fresh grant."""
    rid = request_id(clone, command)
    if _status(engagement, rid) != "approved":
        return False
    _evidence.append(engagement, "approval",
                     {"state": "consumed", "request_id": rid, "clone": clone, "command": command})
    return True


def record_request(engagement: str, clone: str, command: str) -> str:
    """Record that a tiered command is awaiting approval; returns its request id. Idempotent
    while already pending, so an agent polling does not spam the journal."""
    rid = request_id(clone, command)
    if _status(engagement, rid) != "pending":
        _evidence.append(engagement, "approval",
                         {"state": "requested", "request_id": rid, "clone": clone, "command": command})
    return rid


def grant(engagement: str, rid: str) -> None:
    """Operator approves one run of the request. Records a single-use grant."""
    _evidence.append(engagement, "approval", {"state": "granted", "request_id": rid})


def pending(engagement: str) -> list[dict]:
    """Outstanding requests (asked, not yet approved), newest first: {request_id, clone, command}."""
    latest: dict[str, dict] = {}
    for e in _evidence.entries(engagement):
        d = e.get("data", {})
        if e.get("kind") == "approval" and d.get("state") == "requested":
            latest[d["request_id"]] = {"request_id": d["request_id"], "clone": d.get("clone"),
                                       "command": d.get("command")}
    return [v for rid, v in reversed(latest.items()) if _status(engagement, rid) == "pending"]
