"""Shared HTTP helper for feed fetchers.

Single GET that returns bytes. Raises on any failure so the caller can
log + degrade gracefully. Kept tiny and testable — the production stdlib
path is opaque to type checkers, but the function is easy to monkey-patch.

GitHub calls are authenticated when a token is present. Unauthenticated,
GitHub allows 60 requests an hour and one venue probe makes 26, so a few
cycles exhaust the quota and every source starts returning 403 — which the
probes correctly degrade to "no candidates", leaving a board that silently
stops producing. A token raises the ceiling to 5,000/hour. It is optional:
without one everything still works, it just runs out sooner.
"""

from __future__ import annotations

import os
import urllib.error
import urllib.parse
import urllib.request

# Hosts the GitHub token may be sent to. A credential must never travel to
# a host that did not issue it.
_GITHUB_HOSTS = frozenset({"api.github.com", "github.com", "raw.githubusercontent.com"})


def _github_token() -> str | None:
    """A GitHub token from the environment, if the operator provided one."""
    for var in ("GH_TOKEN", "GITHUB_TOKEN", "FORGE_GITHUB_TOKEN"):
        value = (os.environ.get(var) or "").strip()
        if value:
            return value
    return None


def http_get_bytes(url: str, *, timeout: float = 15.0) -> bytes:
    """Fetch a URL and return raw bytes. Raises on any HTTP/network error."""
    headers = {"User-Agent": "project-forge/feeds 0.1"}

    host = (urllib.parse.urlparse(url).hostname or "").lower()
    if host in _GITHUB_HOSTS:
        token = _github_token()
        if token:
            headers["Authorization"] = f"Bearer {token}"
            headers["X-GitHub-Api-Version"] = "2022-11-28"

    req = urllib.request.Request(  # noqa: S310 — feeds use https://, validated by caller
        url,
        headers=headers,
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:  # noqa: S310
            return resp.read()
    except urllib.error.URLError as exc:
        raise OSError(f"http_get_bytes failed for {url}: {exc}") from exc
