"""Shared helpers: paths, logging, HTTP, hashing."""

import hashlib
import json
import os
import subprocess
import sys
import urllib.parse
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
CACHE = Path(os.environ.get("RHUBARB_CACHE", ROOT / "cache"))
KEYS = ROOT / "config" / "keys"


class VerifyError(Exception):
    pass


def log(msg: str) -> None:
    print(f"[resolve] {msg}", file=sys.stderr)


def warn(msg: str) -> None:
    print(f"[resolve] WARNING: {msg}", file=sys.stderr)


def _request(url: str, method: str = "GET") -> urllib.request.Request:
    if not url.startswith("https://"):
        raise VerifyError(f"refusing non-HTTPS URL: {url}")
    headers = {"User-Agent": "RhubarbTart-resolver"}
    host = urllib.parse.urlparse(url).hostname or ""
    if host == "api.github.com" and os.environ.get("GITHUB_TOKEN"):
        headers["Authorization"] = f"Bearer {os.environ['GITHUB_TOKEN']}"
    return urllib.request.Request(url, headers=headers, method=method)


def get_bytes(url: str) -> bytes:
    with urllib.request.urlopen(_request(url), timeout=120) as r:
        return r.read()


def get_json(url: str):
    return json.loads(get_bytes(url))


def head(url: str) -> dict[str, str]:
    """HEAD with redirects followed; includes the final URL as 'x-final-url'."""
    with urllib.request.urlopen(_request(url, "HEAD"), timeout=60) as r:
        hdrs = {k.lower(): v for k, v in r.headers.items()}
        hdrs["x-final-url"] = r.geturl()
        return hdrs


def download(url: str, dest: Path) -> None:
    """Resumable download via curl (handles multi-GB images far better than urllib)."""
    if not url.startswith("https://"):
        raise VerifyError(f"refusing non-HTTPS URL: {url}")
    dest.parent.mkdir(parents=True, exist_ok=True)
    part = dest.with_suffix(dest.suffix + ".part")
    log(f"downloading {url}")
    subprocess.run(
        ["curl", "--fail", "--location", "--proto", "=https", "--proto-redir", "=https",
         "--tlsv1.2", "--retry", "5", "--continue-at", "-", "--output", str(part), url],
        check=True,
    )
    part.rename(dest)


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        while chunk := f.read(8 * 1024 * 1024):
            h.update(chunk)
    return h.hexdigest()


def parse_sums(text: str) -> dict[str, str]:
    """`<hash>  <name>` lines (sha256sum/b2sum style) -> {name: hash}."""
    sums = {}
    for line in text.splitlines():
        parts = line.split()
        if len(parts) == 2:
            sums[parts[1].lstrip("*")] = parts[0].lower()
    return sums


def load_json(path: Path) -> dict:
    try:
        return json.loads(path.read_text())
    except FileNotFoundError:
        raise VerifyError(f"missing {path.relative_to(ROOT)}") from None
    except json.JSONDecodeError as e:
        raise VerifyError(f"{path.relative_to(ROOT)}: invalid JSON: {e}") from None
