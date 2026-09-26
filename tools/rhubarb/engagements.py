"""Engagements: the scoped unit of isolation — a committed, reviewable scope manifest.

engagements/<id>.json   identity + ranges + in-bounds targets + agent budget + evidence policy

One CTF / scoped pentest / research spike is one engagement; nothing crosses that line.
Phase 1 Stage 1A (this module): load + STRICTLY validate a manifest into a stable frozen
dataclass, mirroring profiles.py. No `tart`, no keychain, no network — stdlib only.

Only *identity* and *ranges* are acted on later (Stage 1B: provision/teardown a range set).
*targets*, *agent_budget* and *evidence* are validated syntactically and stored now; they are
enforced in later phases (targets → Phase 2 network isolation, evidence → Phase 4 vault,
agent_budget → Phase 5 agents). Validating the whole schema now keeps it stable ("no manifest,
no run"): a typo can't silently define a different engagement.

See docs/ENGAGEMENT-PLAN.md (Stage 1A) and docs/PLAN.md (Engagements: the unit of isolation).
"""

import re
from dataclasses import dataclass, field

from .common import ROOT, VerifyError, load_json
from .profiles import ID_RE, list_profiles, load_profile

ENGAGEMENTS = ROOT / "engagements"

# A range prefix must be usable as a clone-name stem in Stage 1B (lowercase letters, digits and
# dashes, never the reserved 'rbt-'); mirrors clones.CLONE_RE without importing that module.
PREFIX_RE = re.compile(r"^[a-z0-9][a-z0-9-]{0,39}$")

TOP_KEYS = {"id", "label", "operator", "authorization", "ranges", "targets", "agent_budget", "evidence"}
REQUIRED_KEYS = {"id", "label", "operator", "authorization", "ranges"}
RANGE_KEYS = {"profile", "count", "prefix"}
TARGET_KEYS = {"hosts", "cidrs", "domains", "urls"}
BUDGET_KEYS = {"agents", "wall_clock_minutes", "max_spend_usd", "kill_time"}
EVIDENCE_KEYS = {"vault", "retention_days"}


@dataclass(frozen=True)
class Range:
    """One set of identical VMs to stand up: `count` clones of an existing, buildable profile."""
    profile: str            # an existing profile id (profiles/<profile>.json, must load/build)
    count: int              # how many clones; int >= 1
    prefix: str | None = None   # optional clone-name prefix (PREFIX_RE); default derives from id


@dataclass(frozen=True)
class Targets:
    """In-bounds reach (recorded now, ENFORCED in Phase 2 network isolation — not acted on here)."""
    hosts: tuple[str, ...] = ()
    cidrs: tuple[str, ...] = ()
    domains: tuple[str, ...] = ()
    urls: tuple[str, ...] = ()


@dataclass(frozen=True)
class AgentBudget:
    """Which agents may run and the hard stops (recorded now, ENFORCED in Phase 5 — not acted on)."""
    agents: tuple[str, ...] = ()        # which agents may run
    wall_clock_minutes: int | None = None   # hard wall-clock stop; int >= 1
    max_spend_usd: float | None = None       # hard spend cap; number >= 0
    kill_time: str | None = None             # absolute kill-time, stored verbatim (not parsed)


@dataclass(frozen=True)
class Evidence:
    """Where this engagement's vault lives and its retention (recorded now, ENFORCED in Phase 4)."""
    vault: str | None = None            # vault location, stored verbatim (not acted on)
    retention_days: int | None = None   # retention; int >= 1


@dataclass(frozen=True)
class Engagement:
    """A fully validated scope manifest. Immutable; `ranges` reference existing profiles."""
    id: str
    label: str
    operator: str
    authorization: str
    ranges: tuple[Range, ...]
    targets: Targets = field(default_factory=Targets)
    agent_budget: AgentBudget = field(default_factory=AgentBudget)
    evidence: Evidence = field(default_factory=Evidence)


def _reject_unknown(where: str, obj: dict, allowed: set) -> None:
    unknown = set(obj) - allowed
    if unknown:
        raise VerifyError(f"{where}: unknown key(s) {sorted(unknown)}; allowed: {sorted(allowed)}")


def _require_str(where: str, obj: dict, key: str) -> str:
    """A required, non-empty (non-whitespace) string."""
    if key not in obj:
        raise VerifyError(f"{where}: missing required key {key!r}")
    val = obj[key]
    if not isinstance(val, str) or not val.strip():
        raise VerifyError(f"{where}: {key} must be a non-empty string")
    return val


def _opt_str(where: str, obj: dict, key: str) -> str | None:
    """An optional, non-empty string; None when absent."""
    if key not in obj:
        return None
    val = obj[key]
    if not isinstance(val, str) or not val.strip():
        raise VerifyError(f"{where}: {key} must be a non-empty string when present")
    return val


def _opt_int(where: str, obj: dict, key: str, lo: int) -> int | None:
    """An optional integer >= lo (bool rejected); None when absent."""
    if key not in obj:
        return None
    val = obj[key]
    if not isinstance(val, int) or isinstance(val, bool) or val < lo:
        raise VerifyError(f"{where}: {key} must be an integer >= {lo}")
    return val


def _str_list(where: str, obj: dict, key: str) -> tuple[str, ...]:
    """An optional list of non-empty strings; () when absent. Shape only — values are not
    parsed or reached (that is a later phase's job)."""
    if key not in obj:
        return ()
    val = obj[key]
    if not isinstance(val, list) or not all(isinstance(x, str) and x.strip() for x in val):
        raise VerifyError(f"{where}: {key} must be a list of non-empty strings")
    return tuple(val)


def _range(where: str, i: int, raw: object, known_profiles: set) -> Range:
    at = f"{where}: ranges[{i}]"
    if not isinstance(raw, dict):
        raise VerifyError(f"{at} must be an object")
    _reject_unknown(at, raw, RANGE_KEYS)
    profile = _require_str(at, raw, "profile")
    if profile not in known_profiles:
        raise VerifyError(f"{at}: unknown profile {profile!r}; known: {sorted(known_profiles)}")
    load_profile(profile)   # must load/build; raises VerifyError otherwise (existence + buildable)
    if "count" not in raw:
        raise VerifyError(f"{at}: missing required key 'count'")
    count = raw["count"]
    if not isinstance(count, int) or isinstance(count, bool) or count < 1:
        raise VerifyError(f"{at}: count must be an integer >= 1")
    prefix = _opt_str(at, raw, "prefix")
    if prefix is not None and (not PREFIX_RE.match(prefix) or prefix.startswith("rbt-")):
        raise VerifyError(f"{at}: prefix {prefix!r} must be lowercase letters, digits and dashes "
                          "(max 40), not starting with 'rbt-'")
    return Range(profile=profile, count=count, prefix=prefix)


def _targets(where: str, raw: object) -> Targets:
    at = f"{where}: targets"
    if not isinstance(raw, dict):
        raise VerifyError(f"{at} must be an object")
    _reject_unknown(at, raw, TARGET_KEYS)
    return Targets(hosts=_str_list(at, raw, "hosts"), cidrs=_str_list(at, raw, "cidrs"),
                   domains=_str_list(at, raw, "domains"), urls=_str_list(at, raw, "urls"))


def _agent_budget(where: str, raw: object) -> AgentBudget:
    at = f"{where}: agent_budget"
    if not isinstance(raw, dict):
        raise VerifyError(f"{at} must be an object")
    _reject_unknown(at, raw, BUDGET_KEYS)
    max_spend = raw.get("max_spend_usd")
    if "max_spend_usd" in raw and (not isinstance(max_spend, (int, float))
                                   or isinstance(max_spend, bool) or max_spend < 0):
        raise VerifyError(f"{at}: max_spend_usd must be a number >= 0")
    return AgentBudget(agents=_str_list(at, raw, "agents"),
                       wall_clock_minutes=_opt_int(at, raw, "wall_clock_minutes", 1),
                       max_spend_usd=float(max_spend) if "max_spend_usd" in raw else None,
                       kill_time=_opt_str(at, raw, "kill_time"))


def _evidence(where: str, raw: object) -> Evidence:
    at = f"{where}: evidence"
    if not isinstance(raw, dict):
        raise VerifyError(f"{at} must be an object")
    _reject_unknown(at, raw, EVIDENCE_KEYS)
    return Evidence(vault=_opt_str(at, raw, "vault"),
                    retention_days=_opt_int(at, raw, "retention_days", 1))


def list_engagements() -> list[str]:
    return sorted(p.stem for p in ENGAGEMENTS.glob("*.json"))


def load_engagement(eid: str) -> Engagement:
    """Load engagements/<eid>.json and validate it strictly into a frozen Engagement.

    Rejects: bad id / id != filename, unknown top-level or section keys, wrong types, an empty
    or missing authorization, an empty ranges list, unknown or unbuildable profiles, bad counts.
    """
    if not ID_RE.match(eid):
        raise VerifyError(f"invalid engagement id {eid!r} (expected {ID_RE.pattern})")
    where = f"engagements/{eid}.json"
    man = load_json(ENGAGEMENTS / f"{eid}.json")
    if not isinstance(man, dict):
        raise VerifyError(f"{where}: manifest must be a JSON object")
    _reject_unknown(where, man, TOP_KEYS)

    manifest_id = _require_str(where, man, "id")
    if manifest_id != eid:
        raise VerifyError(f"{where}: id must be {eid!r} (matching the filename), not {manifest_id!r}")
    label = _require_str(where, man, "label")
    operator = _require_str(where, man, "operator")
    authorization = _require_str(where, man, "authorization")   # required — "no manifest, no run"

    if "ranges" not in man:
        raise VerifyError(f"{where}: missing required key 'ranges'")
    if not isinstance(man["ranges"], list) or not man["ranges"]:
        raise VerifyError(f"{where}: ranges must be a non-empty list")
    known = set(list_profiles())
    ranges = tuple(_range(where, i, raw, known) for i, raw in enumerate(man["ranges"]))

    targets = _targets(where, man.get("targets", {}))
    agent_budget = _agent_budget(where, man.get("agent_budget", {}))
    evidence = _evidence(where, man.get("evidence", {}))

    return Engagement(id=eid, label=label, operator=operator, authorization=authorization,
                      ranges=ranges, targets=targets, agent_budget=agent_budget, evidence=evidence)
