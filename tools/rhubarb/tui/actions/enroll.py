"""``enroll`` action — enroll a clone into a VPN/ZTNA service.

Contract: see ``rhubarb/tui/actions/__init__.py`` and the ``rm`` exemplar.

Thin dispatcher over ``api.enroll(name, service, org=...)``. The service is chosen in
the UI (one of :data:`SERVICES`) and handed in via ``ctx.params["service"]``; an optional
team/organization comes in via ``ctx.params["org"]`` (``warp`` in particular wants one).
The enrollment secret is handled entirely by the core (keychain / SSH stdin), never by the
UI. ``perimeter81`` is manual — the core's script only prints instructions and records
nothing — which the summary reflects (``EnrollResult.recorded`` is ``False``).

Not destructive: it adds an enrollment, it never tears a clone down. It does mutate the
clone record (for the recorded services), so ``needs_refresh`` stays ``True`` and the
Clones pane re-reads the new ``enrollments``.
"""

from . import ActionContext, ActionOutcome

ID = "enroll"
LABEL = "Enroll clone"
DESTRUCTIVE = False
REQUIRES_CLONE = True

# The services the core accepts (mirrors ``clones.SERVICES``). Exposed so the shell can
# build its service picker without reaching into the core; the core still validates
# authoritatively and raises ``VerifyError`` on anything else.
SERVICES = ("tailscale", "warp", "perimeter81")


def handle(ctx: ActionContext) -> ActionOutcome:
    """Enroll ``ctx.name`` in ``ctx.params["service"]`` via ``api.enroll`` (off the UI thread).

    Streams a milestone before the (blocking) enrollment and summarises the typed
    ``EnrollResult`` afterwards. Catches the core's typed errors and reports them as a
    failed ``ActionOutcome`` rather than raising, so a failed enrollment never crashes the
    app (mirrors the ``rm`` exemplar).
    """
    from rhubarb import api  # lazy: importing this package must not require tart

    service = (ctx.params or {}).get("service")
    org = (ctx.params or {}).get("org")
    if not service:
        return ActionOutcome(
            ok=False, needs_refresh=False,
            summary=f"enroll {ctx.name}: pick a service first ({', '.join(SERVICES)})")

    org_note = f" (org {org})" if org else ""
    ctx.progress(f"enrolling {ctx.name} in {service}{org_note}…")
    try:
        res = api.enroll(ctx.name, service, org=org)
    except api.VerifyError as e:
        return ActionOutcome(ok=False, needs_refresh=False,
                             summary=f"enroll {ctx.name} in {service} failed: {e}")
    except FileNotFoundError as e:
        tool = getattr(e, "filename", None) or str(e)
        return ActionOutcome(ok=False, needs_refresh=False,
                             summary=f"enroll {ctx.name} in {service} failed: {tool} not installed")

    if res.detail:  # enroll.sh status lines / manual instructions — surface them in the log
        for line in res.detail.splitlines():
            if line.strip():
                ctx.progress(line)
    if res.recorded:
        ctx.progress(f"{res.name}: enrolled in {res.service} (recorded {res.enrolled_at})")
        return ActionOutcome(
            ok=True,
            summary=f"enrolled {res.name} in {res.service} (recorded at {res.enrolled_at})")
    # perimeter81 is manual: the core's script only printed the steps; nothing was recorded.
    ctx.progress(f"{res.name}: {res.service} is manual — follow the printed steps")
    return ActionOutcome(
        ok=True, needs_refresh=False,
        summary=f"{res.name}: {res.service} enrollment is manual — follow the printed "
                "instructions; nothing recorded on the clone")
