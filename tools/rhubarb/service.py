"""Control-plane service (#104, PLAN Phase 5 step 3): the localhost surface herdr sits on.

A thin HTTP server over a **Unix domain socket**, bound at 0600 and owned by the operator — no
TCP, no token; filesystem permissions are the boundary, exactly like the StrictModes clone
records (docs/HERDR-CHARTER.md, "Service shape"). Every endpoint is a call into the typed core
(``api.py``); the service never touches ``tart`` or the keychain itself.

- **GET** (read-only, mirrors the TUI): images, clones, engagements, evidence, provenance.
- **POST** (guarded actions, each one core call that already journals evidence): provision,
  collect, seal, teardown, and exec (run a command in a clone). ``connect`` is a long-lived hold,
  so it belongs with the event-stream slice, not a request/response endpoint.

Stdlib only — no web framework, so nothing new to pin. Bytes fields (e.g. a command's stdout)
are base64-encoded in the JSON, since the response must round-trip arbitrary output losslessly;
the authoritative raw bytes are in the evidence store either way. The same module provides a tiny
client (``request``/``get_json``/``post_json``) so the CLI, tests and later herdr talk to the
socket the same way.
"""

import base64
import dataclasses
import http.client
import json
import os
import socket
import socketserver
import stat
import time
import urllib.parse
from collections.abc import Callable, Iterator
from http.server import BaseHTTPRequestHandler
from pathlib import Path

from . import api
from .clones import _secure_dir, state_dir
from .common import VerifyError

SOCKET_NAME = "service.sock"


def default_socket_path() -> Path:
    return _secure_dir(state_dir()) / SOCKET_NAME


def _jsonable(obj):
    """Serialize the core's dataclasses / Paths / bytes to plain JSON types."""
    if dataclasses.is_dataclass(obj) and not isinstance(obj, type):
        return {k: _jsonable(v) for k, v in dataclasses.asdict(obj).items()}
    if isinstance(obj, bytes):
        return base64.b64encode(obj).decode()   # lossless; command output may be non-text
    if isinstance(obj, dict):
        return {k: _jsonable(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [_jsonable(v) for v in obj]
    if isinstance(obj, Path):
        return str(obj)
    return obj


# ---- routes ---------------------------------------------------------------------------------
# Each returns a JSON-able value from exactly one core call. A route raises VerifyError (-> 404)
# or FileNotFoundError (-> 404) exactly as the core does; anything else is a 500. POST routes
# also take the parsed JSON body and validate their inputs (-> 400 on bad input).

MAX_BODY_BYTES = 1 << 20


class BadRequest(Exception):
    """Malformed request input (-> 400)."""


def _str(body: dict, key: str) -> str:
    v = body.get(key)
    if not isinstance(v, str) or not v.strip():
        raise BadRequest(f"{key!r} must be a non-empty string")
    return v


def _opt_bool(body: dict, key: str, default: bool) -> bool:
    v = body.get(key, default)
    if not isinstance(v, bool):
        raise BadRequest(f"{key!r} must be a boolean")
    return v


def _opt_str(body: dict, key: str) -> str | None:
    v = body.get(key)
    if v is not None and (not isinstance(v, str) or not v.strip()):
        raise BadRequest(f"{key!r} must be a non-empty string when present")
    return v


def _opt_number(body: dict, key: str) -> float | None:
    v = body.get(key)
    if v is not None and (not isinstance(v, (int, float)) or isinstance(v, bool) or v <= 0):
        raise BadRequest(f"{key!r} must be a positive number when present")
    return v


def _get_route(parts: list[str]) -> Callable[[], object] | None:
    match parts:
        case ["health"]:
            from . import __version__
            return lambda: {"ok": True, "service": "rhubarbtart", "version": __version__}
        case ["images"]:
            return api.images
        case ["clones"]:
            return api.clones
        case ["engagements"]:
            return api.engagements
        case ["engagements", eid, "evidence"]:
            return lambda: api.evidence_entries(eid)
        case ["engagements", eid, "evidence", "verify"]:
            return lambda: api.verify_evidence(eid)
        case ["provenance", vm]:
            return lambda: api.provenance(vm)
    return None


def _post_route(parts: list[str]) -> Callable[[dict], object] | None:
    match parts:
        case ["engagements", eid, "provision"]:
            return lambda _b: api.provision(eid)
        case ["engagements", eid, "collect"]:
            return lambda _b: api.collect(eid)
        case ["engagements", eid, "seal"]:
            return lambda b: {"vault": str(api.seal_vault(eid, out_dir=_opt_str(b, "out_dir")))}
        case ["engagements", eid, "teardown"]:
            return lambda b: {"removed": api.teardown(eid, collect_first=_opt_bool(b, "collect_first", True))}
        case ["clones", name, "exec"]:
            return lambda b: api.exec(name, _str(b, "command"), timeout=_opt_number(b, "timeout"))
    return None


class _Handler(BaseHTTPRequestHandler):
    server_version = "rhubarb/1"
    protocol_version = "HTTP/1.1"

    def log_message(self, *_a):  # no stderr spam; client_address is empty for AF_UNIX anyway
        pass

    def _send(self, code: int, payload: object) -> None:
        body = json.dumps(_jsonable(payload)).encode() + b"\n"
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _parts(self) -> list[str]:
        return [p for p in self.path.split("?", 1)[0].strip("/").split("/") if p]

    def _query(self) -> dict[str, str]:
        q = self.path.split("?", 1)
        return {k: v[-1] for k, v in urllib.parse.parse_qs(q[1]).items()} if len(q) == 2 else {}

    def _run(self, call: Callable[[], object]) -> None:
        try:
            self._send(200, call())
        except BadRequest as e:
            self._send(400, {"error": str(e)})
        except (VerifyError, FileNotFoundError) as e:
            self._send(404, {"error": str(e)})
        except Exception as e:  # noqa: BLE001 - a route bug must not take the socket down
            self._send(500, {"error": f"{type(e).__name__}: {e}"})

    # Event stream: tail an engagement's evidence journal as NDJSON (the control plane's
    # authoritative record of what happened), for herdr's sidebar. Not a normal route because
    # the response is unbounded, so it can't carry a Content-Length.
    STREAM_POLL_SECONDS = 0.5
    STREAM_KEEPALIVE_SECONDS = 15

    def _stream_events(self, eid: str) -> None:
        q = self._query()
        try:
            from_seq = int(q.get("from", "0"))
            if from_seq < 0:
                raise ValueError
        except ValueError:
            self._send(400, {"error": "'from' must be a non-negative integer"})
            return
        follow = q.get("follow", "true").lower() not in ("false", "0", "no")
        try:
            api.evidence_entries(eid)   # validates the id; raises VerifyError on a bad one
        except (VerifyError, FileNotFoundError) as e:
            self._send(404, {"error": str(e)})
            return
        self.close_connection = True   # unbounded body -> framed by connection close
        self.send_response(200)
        self.send_header("Content-Type", "application/x-ndjson")
        self.send_header("Connection", "close")
        self.end_headers()
        last_seq = from_seq
        last_write = time.monotonic()
        try:
            while True:
                for entry in api.evidence_entries(eid):
                    if entry.get("seq", 0) > last_seq:
                        self.wfile.write(json.dumps(_jsonable(entry)).encode() + b"\n")
                        self.wfile.flush()
                        last_seq = entry["seq"]
                        last_write = time.monotonic()
                if not follow:
                    return
                if time.monotonic() - last_write >= self.STREAM_KEEPALIVE_SECONDS:
                    self.wfile.write(b"\n")   # blank line: keepalive, ignored by the client
                    self.wfile.flush()
                    last_write = time.monotonic()
                time.sleep(self.STREAM_POLL_SECONDS)
        except (BrokenPipeError, ConnectionResetError):
            return   # client went away; end the stream quietly

    def do_GET(self) -> None:
        parts = self._parts()
        if len(parts) == 3 and parts[0] == "engagements" and parts[2] == "events":
            self._stream_events(parts[1])
            return
        handler = _get_route(parts)
        if handler is None:
            code, msg = (405, "method not allowed") if _post_route(parts) else \
                        (404, f"no such route: /{'/'.join(parts)}")
            self._send(code, {"error": msg})
            return
        self._run(handler)

    def _read_body(self) -> dict:
        n = int(self.headers.get("Content-Length") or 0)
        if n < 0 or n > MAX_BODY_BYTES:
            raise BadRequest(f"request body too large (> {MAX_BODY_BYTES} bytes)")
        raw = self.rfile.read(n) if n else b""
        if not raw:
            return {}
        try:
            body = json.loads(raw)
        except ValueError:
            raise BadRequest("body is not valid JSON") from None
        if not isinstance(body, dict):
            raise BadRequest("body must be a JSON object")
        return body

    def do_POST(self) -> None:
        parts = self._parts()
        handler = _post_route(parts)
        if handler is None:
            code, msg = (405, "method not allowed") if _get_route(parts) else \
                        (404, f"no such route: /{'/'.join(parts)}")
            self._send(code, {"error": msg})
            return
        try:
            body = self._read_body()
        except BadRequest as e:
            self._send(400, {"error": str(e)})
            return
        self._run(lambda: handler(body))

    def _reject(self) -> None:
        self._send(405, {"error": "method not allowed"})

    do_PUT = do_DELETE = do_PATCH = _reject


class _Server(socketserver.ThreadingMixIn, socketserver.UnixStreamServer):
    daemon_threads = True
    allow_reuse_address = False


def _prepare_socket(path: Path) -> None:
    """The socket lives in a 0700 operator-owned dir; a stale socket (only a socket) is replaced.
    Anything else at the path is refused rather than clobbered."""
    _secure_dir(path.parent)
    if path.exists() or path.is_symlink():
        st = os.lstat(path)
        if not stat.S_ISSOCK(st.st_mode):
            raise VerifyError(f"{path} exists and is not a socket; refusing to replace it")
        path.unlink()


# The kernel's sockaddr_un.sun_path is ~104 bytes on macOS / 108 on Linux; bind() fails with a
# cryptic OSError past that. Fail early with something actionable instead.
_MAX_SOCKET_PATH = 100


def make_server(socket_path: str | Path | None = None) -> _Server:
    """Bind the service to its Unix socket at 0600. Caller runs ``serve_forever``/``shutdown``."""
    path = Path(socket_path) if socket_path else default_socket_path()
    if len(str(path)) > _MAX_SOCKET_PATH:
        raise VerifyError(f"socket path is too long ({len(str(path))} > {_MAX_SOCKET_PATH} bytes); "
                          "pass a shorter --socket (a Unix socket path is length-limited by the OS)")
    _prepare_socket(path)
    old = os.umask(0o177)  # socket created 0600
    try:
        server = _Server(str(path), _Handler)
    finally:
        os.umask(old)
    os.chmod(path, 0o600)
    return server


def serve(socket_path: str | Path | None = None,
          on_ready: Callable[[Path], None] | None = None) -> None:
    """Serve until interrupted, then remove the socket. Blocks."""
    server = make_server(socket_path)
    path = Path(server.server_address if isinstance(server.server_address, str) else str(socket_path))
    if on_ready:
        on_ready(path)
    try:
        server.serve_forever()
    finally:
        server.server_close()
        Path(path).unlink(missing_ok=True)


# ---- client (same module, so every caller reaches the socket identically) -------------------

class _UnixConnection(http.client.HTTPConnection):
    def __init__(self, socket_path: str, timeout: float = 30):
        super().__init__("localhost", timeout=timeout)
        self._socket_path = socket_path

    def connect(self) -> None:
        sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        sock.settimeout(self.timeout)
        sock.connect(self._socket_path)
        self.sock = sock


def request(socket_path: str | Path, method: str, path: str, body: dict | None = None,
            timeout: float = 30) -> tuple[int, object]:
    """One request to the service socket; returns (status, decoded-JSON-body)."""
    conn = _UnixConnection(str(socket_path), timeout=timeout)
    try:
        data = json.dumps(body).encode() if body is not None else None
        headers = {"Content-Type": "application/json"} if data is not None else {}
        conn.request(method, path, body=data, headers=headers)
        resp = conn.getresponse()
        raw = resp.read()
        try:
            body = json.loads(raw) if raw else None
        except ValueError:
            body = {"raw": raw.decode(errors="replace")}
        return resp.status, body
    finally:
        conn.close()


def get_json(socket_path: str | Path, path: str) -> object:
    """GET a route and return its body, raising ``VerifyError`` on a non-200."""
    status, body = request(socket_path, "GET", path)
    if status != 200:
        msg = body.get("error") if isinstance(body, dict) else body
        raise VerifyError(f"service {path} -> {status}: {msg}")
    return body


def post_json(socket_path: str | Path, path: str, body: dict | None = None,
              timeout: float = 300) -> object:
    """POST a guarded action and return its body, raising ``VerifyError`` on a non-200. The
    default timeout is generous because actions (provision, teardown) can take minutes."""
    status, out = request(socket_path, "POST", path, body=body or {}, timeout=timeout)
    if status != 200:
        msg = out.get("error") if isinstance(out, dict) else out
        raise VerifyError(f"service POST {path} -> {status}: {msg}")
    return out


def stream_events(socket_path: str | Path, engagement: str, from_seq: int = 0,
                  follow: bool = True, timeout: float | None = None) -> Iterator[dict]:
    """Yield an engagement's evidence entries as they are journaled (NDJSON over the socket).

    ``from_seq`` replays entries after that seq (0 = all). ``follow`` keeps tailing; set it False
    to replay and stop. Blank keepalive lines are skipped. Close the generator to end the stream.
    """
    conn = _UnixConnection(str(socket_path), timeout=timeout)
    path = f"/engagements/{engagement}/events?from={from_seq}&follow={'true' if follow else 'false'}"
    try:
        conn.request("GET", path)
        resp = conn.getresponse()
        if resp.status != 200:
            raw = resp.read()
            try:
                msg = json.loads(raw).get("error")
            except ValueError:
                msg = raw.decode(errors="replace")
            raise VerifyError(f"service {path} -> {resp.status}: {msg}")
        for line in resp:
            line = line.strip()
            if line:
                yield json.loads(line)
    finally:
        conn.close()
