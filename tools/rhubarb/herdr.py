"""herdr integration — ``arm`` (#108 slice 2, charter model A): launch an engagement's agents.

``arm`` drives herdr.dev (unmodified) over its CLI to put each configured agent in its own pane,
pinned to one range clone: the pane carries ``RBT_SERVICE_SOCKET`` and ``RBT_RANGE_CLONE`` and has
the repo on ``PATH``, so the agent's only way into a range is ``rbt-range`` (the scoped client,
#108 slice 1) — every command journaled as evidence, no clone credential, no raw route.

Which agents run, of what kind, against which clone is **herdr config**, committed and reviewable
at ``engagements/<id>.herdr.json`` (charter: policy version-controlled; its hash is recorded as an
``arm`` evidence entry so a sealed vault shows what the agent was permitted). The engagement
manifest stays authoritative for the hard boundary (targets, ranges, budget).

This module drives herdr and reads the typed core (engagement/clones/evidence); it never touches
``tart`` or the keychain itself (that is the core's job, reached through ``api``).
"""

import json
import os
import re
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path

from . import api
from . import engagements as _engagements
from . import evidence as _evidence
from .common import ROOT, VerifyError, sha256_file
from .service import default_socket_path

AGENT_KEYS = {"name", "kind", "clone"}
NAME_RE = re.compile(r"^[a-z0-9][a-z0-9-]{0,39}$")
# herdr's supported agent kinds (herdr 0.9 `agent start --kind`); kept as a sanity check only.
KINDS = {"pi", "claude", "codex", "gemini", "cursor", "devin", "agy", "cline", "omp", "mastracode",
         "opencode", "copilot", "kimi", "kiro", "droid", "amp", "grok", "hermes", "kilo",
         "qodercli", "qwen", "letta", "maki", "muse"}


def config_path(engagement: str) -> Path:
    return ROOT / "engagements" / f"{engagement}.herdr.json"


def herdr_bin() -> str:
    """Locate the herdr binary. ``env.sh`` narrows PATH for the toolchain, so also look where
    herdr installs by default. ``RBT_HERDR`` overrides."""
    override = os.environ.get("RBT_HERDR")
    if override:
        if not (Path(override).is_file() and os.access(override, os.X_OK)):
            raise VerifyError(f"RBT_HERDR={override!r} is not an executable file")
        return override
    found = shutil.which("herdr")
    if found:
        return found
    for cand in (Path.home() / ".local/bin/herdr", Path("/opt/homebrew/bin/herdr"),
                 Path("/usr/local/bin/herdr")):
        if cand.is_file() and os.access(cand, os.X_OK):
            return str(cand)
    raise VerifyError("herdr not found (install herdr.dev, or set RBT_HERDR to its path)")


def _herdr(*args: str, check: bool = True) -> dict:
    """Run one herdr CLI command and return its ``result`` object. herdr prints one JSON line
    ``{"id":..., "result":{...}}`` (or ``{"error":...}``)."""
    proc = subprocess.run([herdr_bin(), *args], capture_output=True, text=True)
    out = (proc.stdout or "").strip()
    try:
        parsed = json.loads(out.splitlines()[-1]) if out else {}
    except (ValueError, IndexError):
        parsed = {}
    if proc.returncode != 0 or "error" in parsed:
        msg = parsed.get("error") if isinstance(parsed.get("error"), str) else \
              (proc.stderr or out or f"exit {proc.returncode}").strip()
        if check:
            raise VerifyError(f"herdr {' '.join(args[:2])}: {msg}")
        return {"_error": msg}
    return parsed.get("result", {})


@dataclass(frozen=True)
class ArmedAgent:
    name: str
    kind: str
    clone: str
    pane: str
    note: str | None = None   # e.g. the agent CLI was slow to start; the pane is still armed


@dataclass(frozen=True)
class ArmResult:
    engagement: str
    workspace: str
    agents: list[ArmedAgent]


def load_config(engagement: str) -> list[dict]:
    """Load + strictly validate ``engagements/<id>.herdr.json`` into a list of agent specs.

    Rejects unknown keys, a bad name/kind, a missing/duplicate name, or an empty agents list.
    Does not check the clone against the engagement (``arm`` does that against live clones).
    """
    path = config_path(engagement)
    disp = path.relative_to(ROOT) if ROOT in path.parents else path
    if not path.is_file():
        raise VerifyError(f"no herdr config for {engagement} (create {disp})")
    try:
        raw = json.loads(path.read_text())
    except ValueError as e:
        raise VerifyError(f"{disp}: invalid JSON ({e})") from None
    if not isinstance(raw, dict) or set(raw) - {"agents", "tiered"}:
        raise VerifyError(f"{disp}: allowed keys are 'agents' and 'tiered'")
    tiered = raw.get("tiered", [])
    if not isinstance(tiered, list) or not all(isinstance(t, str) for t in tiered):
        raise VerifyError(f"{disp}: 'tiered' must be a list of regex strings")
    for t in tiered:
        try:
            re.compile(t)
        except re.error as e:
            raise VerifyError(f"{disp}: tiered pattern {t!r} is not a valid regex ({e})") from None
    agents = raw.get("agents")
    if not isinstance(agents, list) or not agents:
        raise VerifyError(f"{disp}: 'agents' must be a non-empty list")
    seen: set[str] = set()
    for i, a in enumerate(agents):
        at = f"{disp}: agents[{i}]"
        if not isinstance(a, dict) or set(a) - AGENT_KEYS or not AGENT_KEYS <= set(a):
            raise VerifyError(f"{at}: each agent needs exactly {sorted(AGENT_KEYS)}")
        if not isinstance(a["name"], str) or not NAME_RE.match(a["name"]):
            raise VerifyError(f"{at}: name must match {NAME_RE.pattern}")
        if a["name"] in seen:
            raise VerifyError(f"{at}: duplicate agent name {a['name']!r}")
        seen.add(a["name"])
        if a["kind"] not in KINDS:
            raise VerifyError(f"{at}: unknown kind {a['kind']!r}; herdr kinds: {sorted(KINDS)}")
        if not isinstance(a["clone"], str) or not a["clone"].strip():
            raise VerifyError(f"{at}: clone must be a non-empty string")
    return agents


def arm(engagement: str, socket_path: str | None = None, progress=None) -> ArmResult:
    """Launch the engagement's configured agents under herdr, each pinned to its range clone.

    Refuses (``VerifyError``) if the engagement isn't provisioned, a config clone isn't one of its
    clones, the control-plane service isn't running, or herdr isn't installed. Records an ``arm``
    evidence entry with the effective policy (the config hash + the agents launched).
    """
    eng = _engagements.load_engagement(engagement)
    tagged = {c.name: c for c in api.engagement_clones(eng.id)}
    if not tagged:
        raise VerifyError(f"engagement {eng.id} has no clones; run: rhubarb engagement provision {eng.id}")
    agents = load_config(eng.id)
    for a in agents:
        if a["clone"] not in tagged:
            raise VerifyError(f"agent {a['name']!r}: clone {a['clone']!r} is not in engagement "
                              f"{eng.id} (clones: {sorted(tagged)})")

    socket = Path(socket_path) if socket_path else default_socket_path()
    if not socket.exists():
        raise VerifyError(f"control-plane service not running at {socket}; start it: rhubarb serve")
    herdr_bin()   # fail early if herdr is missing

    def envargs(clone: str) -> list[str]:
        return ["--env", f"RBT_SERVICE_SOCKET={socket}", "--env", f"RBT_RANGE_CLONE={clone}"]

    armed: list[ArmedAgent] = []
    workspace = ""
    prev_pane = ""
    for i, a in enumerate(agents):
        clone = a["clone"]
        if i == 0:
            res = _herdr("workspace", "create", "--label", f"rbt-{eng.id}", "--cwd", str(ROOT),
                         *envargs(clone), "--no-focus")
            workspace = res["workspace"]["workspace_id"]
            pane = res["root_pane"]["pane_id"]
        else:
            res = _herdr("pane", "split", "--pane", prev_pane, "--direction", "down",
                         "--cwd", str(ROOT), *envargs(clone), "--no-focus")
            pane = res["pane"]["pane_id"]
        prev_pane = pane
        # Put the repo on PATH additively (in the pane's own shell) so `rbt-range` resolves,
        # without clobbering the agent's PATH.
        _herdr("pane", "run", pane, f'export PATH="{ROOT}:$PATH"')
        started = _herdr("agent", "start", a["name"], "--kind", a["kind"], "--pane", pane,
                         check=False)
        note = started.get("_error")   # e.g. agent_not_ready: pane is armed, agent still coming up
        armed.append(ArmedAgent(name=a["name"], kind=a["kind"], clone=clone, pane=pane, note=note))
        if progress:
            progress(f"armed {a['name']} ({a['kind']}) on {clone} in pane {pane}"
                     + (f" [{note}]" if note else ""))

    _evidence.append(eng.id, "lifecycle", {
        "event": "arm", "workspace": workspace, "socket": str(socket),
        "config_sha256": sha256_file(config_path(eng.id)),
        "agents": [{"name": x.name, "kind": x.kind, "clone": x.clone, "pane": x.pane,
                    "note": x.note} for x in armed]})
    return ArmResult(engagement=eng.id, workspace=workspace, agents=armed)
