"""Self-tests: the logs API behind the TUI Logs tab and build-output redaction."""

import os

from tests.support import _raises, check


def test_logs_api() -> None:
    """#120: list_logs classifies clone/build/events logs (skipping symlinks), read_log tails
    safely and refuses unsafe ids, new_build_log makes a private file, and build output shown in
    the TUI/log has the build VM's VNC password redacted."""
    from pathlib import Path

    from rhubarb import api
    from rhubarb.common import VerifyError
    from rhubarb.tui.actions import build as build_action

    state = Path(os.environ["RHUBARB_STATE_DIR"])   # the runner's per-test throwaway dir
    logs = state / "logs"
    logs.mkdir(parents=True, exist_ok=True)
    os.chmod(state, 0o700)
    os.chmod(logs, 0o700)
    (logs / "live-1.log").write_text("".join(f"line {i}\n" for i in range(5000)))
    (logs / "gone-1.log").write_text("old run\n")
    (logs / "build-kali-research-20260930T120000Z.log").write_text("installing kali\n")
    (state / "events.log").write_text("2026-09-30\tnew\tlive-1\t\n")
    (logs / "evil.log").symlink_to("/etc/passwd")
    (logs / "big.log").write_bytes(b"x" * 600_000 + b"\nlast-but-one\nlast\n")

    saved = api._clones.all_records
    try:
        api._clones.all_records = lambda: ([{"name": "live-1"}], [])
        refs = {r.id: r for r in api.list_logs()}
    finally:
        api._clones.all_records = saved
    check("list_logs finds clone, build and events logs",
          {"logs/live-1.log", "logs/gone-1.log", "logs/build-kali-research-20260930T120000Z.log",
           "events.log"} <= set(refs))
    check("list_logs skips a symlink", "logs/evil.log" not in refs)
    check("list_logs labels live, removed, build and events logs",
          refs["logs/live-1.log"].label == "live-1"
          and refs["logs/gone-1.log"].label == "gone-1 (removed)"
          and refs["logs/build-kali-research-20260930T120000Z.log"].kind == "build"
          and refs["events.log"].kind == "events")

    check("read_log returns the tail", api.read_log("logs/live-1.log", max_lines=3)
          == "line 4997\nline 4998\nline 4999")
    check("read_log tails a log larger than the read window",
          api.read_log("logs/big.log", max_lines=2) == "last-but-one\nlast")
    for bad in ("../x.log", "logs/../x.log", "logs/a/b.log", "/etc/passwd", "logs/x.txt", "logs/"):
        check(f"read_log refuses {bad!r}", _raises(lambda b=bad: api.read_log(b), VerifyError))
    check("read_log refuses to follow a symlink", _raises(lambda: api.read_log("logs/evil.log"), OSError))

    # tail_log (#122): a first read is a reset; later reads return only what was appended.
    grow = logs / "grow.log"
    grow.write_text("a\nb\npart")
    t1 = api.tail_log("logs/grow.log")
    check("tail_log: a first read resets and shows the unfinished last line",
          t1.reset and t1.text == "a\nb\npart" and t1.partial)
    with open(grow, "a") as f:
        f.write("ial\nc\n")
    t2 = api.tail_log("logs/grow.log", t1.cursor)
    check("tail_log: returns only the new text, re-sending the completed line",
          not t2.reset and t2.text == "partial\nc" and not t2.partial)
    t3 = api.tail_log("logs/grow.log", t2.cursor)
    check("tail_log: nothing new -> empty, not a reset", not t3.reset and t3.text == "")
    grow.write_text("fresh\n")                               # truncated in place
    check("tail_log: a truncated log resets", api.tail_log("logs/grow.log", t3.cursor).reset)
    t4 = api.tail_log("logs/grow.log")
    grow.unlink()
    grow.write_text("fresh\nreplacement is longer\n")        # a new file under the same name
    t5 = api.tail_log("logs/grow.log", t4.cursor)
    check("tail_log: a replaced log resets", t5.reset and t5.text.startswith("fresh"))
    check("tail_log: a large log resets to the tail, not a cut first line",
          api.tail_log("logs/big.log", max_lines=5).text == "last-but-one\nlast")
    check("tail_log refuses unsafe ids like read_log",
          _raises(lambda: api.tail_log("../x.log"), VerifyError)
          and _raises(lambda: api.tail_log("logs/evil.log"), OSError))

    p = api.new_build_log("kali-research")
    check("new_build_log makes a private build-<profile>-<time>.log",
          p.parent == logs and p.name.startswith("build-kali-research-") and p.suffix == ".log"
          and (p.stat().st_mode & 0o777) == 0o600)
    check("new_build_log refuses a path-like profile",
          _raises(lambda: api.new_build_log("../x"), VerifyError))

    line = 'connect via VNC with the password "chuckle-deny-lonely-legal" to'
    check("build output redacts the VNC password",
          "chuckle" not in build_action.redact(line) and '"***"' in build_action.redact(line))
