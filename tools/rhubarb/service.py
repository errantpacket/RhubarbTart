"""Control-plane service (#104, PLAN Phase 5 step 3): the localhost surface herdr sits on.

A thin HTTP server over a **Unix domain socket**, bound at 0600 and owned by the operator — no
TCP, no token; filesystem permissions are the boundary, exactly like the StrictModes clone
records (docs/HERDR-CHARTER.md, "Service shape"). Every endpoint is a call into the typed core
(``api.py``); the service never touches ``tart`` or the keychain itself. This first slice is
**read-only** (mirrors the TUI): images, clones, engagements, evidence, provenance. Guarded
actions and an event stream come in later slices.

Stdlib only — no web framework, so nothing new to pin. The same module provides a tiny client
(``request``/``get_json``) so the CLI, tests and later herdr talk to the socket the same way.
"""

import dataclasses
import http.client
import json
import os
import socket
import socketserver
import stat
from collections.abc import Callable
from http.server import BaseHTTPRequestHandler
from pathlib import Path

from . import api
from .clones import _secure_dir, state_dir
from .common import VerifyError

SOCKET_NAME = "service.sock"


def default_socket_path() -> Path:
    return _secure_dir(state_dir()) / SOCKET_NAME


def _jsonable(obj):
    """Serialize the core's dataclasses / Paths to plain JSON types."""
    if dataclasses.is_dataclass(obj) and not isinstance(obj, type):
        return {k: _jsonable(v) for k, v in dataclasses.asdict(obj).items()}
    if isinstance(obj, dict):
        return {k: _jsonable(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [_jsonable(v) for v in obj]
    if isinstance(obj, Path):
        return str(obj)
    return obj


# ---- routes (read-only) --------------------------------------------------------------------
# Each returns a JSON-able value from one core call. A route raises VerifyError (-> 404) or
# FileNotFoundError (-> 404) exactly as the core does; anything else is a 500.

def _route(method: str, parts: list[str]) -> Callable[[], object] | None:
    if method != "GET":
        return None
    match parts:
        case ["health"]:
            return lambda: {"ok": True, "service": "rhubarb", "readonly": True}
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

    def do_GET(self) -> None:
        parts = [p for p in self.path.split("?", 1)[0].strip("/").split("/") if p]
        handler = _route("GET", parts)
        if handler is None:
            self._send(404, {"error": f"no such route: /{'/'.join(parts)}"})
            return
        try:
            self._send(200, handler())
        except (VerifyError, FileNotFoundError) as e:
            self._send(404, {"error": str(e)})
        except Exception as e:  # noqa: BLE001 - a route bug must not take the socket down
            self._send(500, {"error": f"{type(e).__name__}: {e}"})

    def _reject(self) -> None:
        self._send(405, {"error": "read-only service: only GET is supported in this slice"})

    do_POST = do_PUT = do_DELETE = do_PATCH = _reject


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


def request(socket_path: str | Path, method: str, path: str,
            timeout: float = 30) -> tuple[int, object]:
    """One request to the service socket; returns (status, decoded-JSON-body)."""
    conn = _UnixConnection(str(socket_path), timeout=timeout)
    try:
        conn.request(method, path)
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
