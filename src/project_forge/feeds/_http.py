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


# Feeds are operator-configured, but "trusted to be listed" is not "trusted to
# be well-behaved" — a compromised or merely broken endpoint that streams
# without end would take the single-process app down with it. The largest real
# feed here is a few MB of IETF JSON; 16 MiB leaves generous headroom.
MAX_FEED_BYTES = 16 * 1024 * 1024


def http_get_bytes(url: str, *, timeout: float = 15.0, max_bytes: int = MAX_FEED_BYTES) -> bytes:
    """Fetch a URL and return raw bytes. Raises on any HTTP/network error.

    Reads at most ``max_bytes`` and raises OSError past that, rather than
    buffering whatever the far end decides to send.
    """
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
            declared = resp.headers.get("Content-Length", "")
            if declared.isdigit() and int(declared) > max_bytes:
                raise OSError(f"http_get_bytes: {url} declared {declared} bytes, over the {max_bytes} cap")
            # Read one byte past the cap so an oversized body is detectable
            # rather than silently truncated into a half-parsed feed.
            body = resp.read(max_bytes + 1)
    except urllib.error.URLError as exc:
        raise OSError(f"http_get_bytes failed for {url}: {exc}") from exc

    if len(body) > max_bytes:
        raise OSError(f"http_get_bytes: {url} exceeded the {max_bytes} byte cap")
    return body
