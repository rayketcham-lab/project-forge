"""Shared HTTP helper for feed fetchers.

Single GET that returns bytes. Raises on any failure so the caller can
log + degrade gracefully. Kept tiny and testable — the production stdlib
path is opaque to type checkers, but the function is easy to monkey-patch.
"""

from __future__ import annotations

import urllib.error
import urllib.request


def http_get_bytes(url: str, *, timeout: float = 15.0) -> bytes:
    """Fetch a URL and return raw bytes. Raises on any HTTP/network error."""
    req = urllib.request.Request(  # noqa: S310 — feeds use https://, validated by caller
        url,
        headers={"User-Agent": "project-forge/feeds 0.1"},
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:  # noqa: S310
            return resp.read()
    except urllib.error.URLError as exc:
        raise OSError(f"http_get_bytes failed for {url}: {exc}") from exc
