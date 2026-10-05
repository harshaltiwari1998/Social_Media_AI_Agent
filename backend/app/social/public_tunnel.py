"""Temporarily expose one local media file at a public HTTPS URL.

Instagram's Content Publishing API only accepts media by URL, so while a post is
being created this serves the single file over a Cloudflare quick tunnel
(`cloudflared tunnel --url ...`, no account needed) and tears everything down
afterwards. Only that one file is reachable, under an unguessable path.
"""
from __future__ import annotations

import logging
import mimetypes
import re
import secrets
import subprocess
import threading
import time
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Iterator

import httpx

from app.config import settings

logger = logging.getLogger(__name__)

_URL_PATTERN = re.compile(r"https://[a-z0-9-]+\.trycloudflare\.com")


def _make_handler(file_path: Path, url_path: str) -> type[BaseHTTPRequestHandler]:
    content_type = mimetypes.guess_type(file_path.name)[0] or "application/octet-stream"

    class _SingleFileHandler(BaseHTTPRequestHandler):
        def _send(self, with_body: bool) -> None:
            if self.path.split("?")[0] != url_path:
                self.send_error(404)
                return
            data = file_path.read_bytes()
            self.send_response(200)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            if with_body:
                self.wfile.write(data)

        def do_GET(self) -> None:  # noqa: N802
            self._send(with_body=True)

        def do_HEAD(self) -> None:  # noqa: N802
            self._send(with_body=False)

        def log_message(self, format: str, *args) -> None:  # noqa: A002
            logger.debug("tunnel file server: " + format, *args)

    return _SingleFileHandler


def _wait_for_tunnel_url(process: subprocess.Popen, timeout_s: float) -> str:
    found: list[str] = []

    def _read() -> None:
        for line in process.stderr:  # cloudflared logs to stderr
            match = _URL_PATTERN.search(line)
            if match and not found:
                found.append(match.group(0))

    threading.Thread(target=_read, daemon=True).start()
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline and not found:
        if process.poll() is not None:
            raise RuntimeError("cloudflared exited before the tunnel came up")
        time.sleep(0.2)
    if not found:
        raise RuntimeError("timed out waiting for the cloudflared tunnel URL")
    return found[0]


def _wait_until_reachable(url: str, timeout_s: float) -> None:
    """Quick-tunnel hostnames take a few seconds to resolve; don't hand Instagram a dead URL.

    The check asks Cloudflare's public DNS-over-HTTPS resolver instead of fetching the
    URL locally: if the OS resolver looks the brand-new hostname up too early it caches
    the NXDOMAIN, and every local retry keeps failing while the tunnel is actually fine.
    """
    host = httpx.URL(url).host
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        try:
            answer = httpx.get(
                "https://cloudflare-dns.com/dns-query",
                params={"name": host, "type": "A"},
                headers={"accept": "application/dns-json"},
                timeout=10.0,
            ).json()
            if answer.get("Status") == 0 and answer.get("Answer"):
                time.sleep(5)  # let the edge finish wiring the tunnel to its hostname
                return
        except (httpx.HTTPError, ValueError):
            pass
        time.sleep(3)
    raise RuntimeError(f"tunnel hostname never resolved: {host}")


@contextmanager
def public_url_for(local_path: str) -> Iterator[str]:
    """Yield a public HTTPS URL serving `local_path` for the duration of the block."""
    file_path = Path(local_path).resolve()
    url_path = f"/{secrets.token_urlsafe(16)}/{file_path.name}"

    server = ThreadingHTTPServer(("127.0.0.1", 0), _make_handler(file_path, url_path))
    threading.Thread(target=server.serve_forever, daemon=True).start()
    port = server.server_address[1]

    process = subprocess.Popen(
        [settings.cloudflared_path, "tunnel", "--no-autoupdate", "--url", f"http://127.0.0.1:{port}"],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.PIPE,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    try:
        url = _wait_for_tunnel_url(process, timeout_s=45.0) + url_path
        _wait_until_reachable(url, timeout_s=120.0)
        logger.info("Serving %s publicly at %s", file_path.name, url)
        yield url
    finally:
        process.terminate()
        try:
            process.wait(timeout=10)
        except subprocess.TimeoutExpired:
            process.kill()
        server.shutdown()
        server.server_close()
