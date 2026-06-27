"""NVD CVE feed parser + fetcher (CVE 2.0 JSON API).

Fetches recent CVEs from https://services.nvd.nist.gov/rest/json/cves/2.0
and exposes them as feed items. The parser is pure (testable in isolation);
the fetcher writes to FeedCache and degrades to empty list on network failure.
"""

from __future__ import annotations

import json
import logging
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING, Any

from project_forge.feeds._http import http_get_bytes as _http_get_bytes  # exported for monkeypatch

if TYPE_CHECKING:
    from project_forge.feeds.cache import FeedCache

logger = logging.getLogger(__name__)

NVD_API_URL = "https://services.nvd.nist.gov/rest/json/cves/2.0"


def parse_nvd_response(payload: dict[str, Any]) -> list[dict]:
    """Convert an NVD CVE 2.0 response into feed items.

    Each item: {id, title, summary, url, ts}
    """
    items: list[dict] = []
    for vuln in payload.get("vulnerabilities") or []:
        cve = vuln.get("cve", {})
        cve_id = cve.get("id")
        if not cve_id:
            continue

        # Pick the English description if available
        descriptions = cve.get("descriptions") or []
        summary = ""
        for d in descriptions:
            if d.get("lang") == "en":
                summary = d.get("value", "")
                break
        if not summary and descriptions:
            summary = descriptions[0].get("value", "")

        items.append(
            {
                "id": cve_id,
                "title": cve_id,
                "summary": summary,
                "url": f"https://nvd.nist.gov/vuln/detail/{cve_id}",
                "ts": cve.get("published", ""),
            }
        )
    return items


def fetch(*, cache: FeedCache, days: int = 7, results_per_page: int = 50) -> list[dict]:
    """Fetch recent CVEs from NVD and write to cache.

    Returns parsed items (empty list on network/parse failure).
    """
    pub_start = (datetime.now(UTC) - timedelta(days=days)).strftime("%Y-%m-%dT00:00:00.000")
    pub_end = datetime.now(UTC).strftime("%Y-%m-%dT00:00:00.000")
    url = f"{NVD_API_URL}?pubStartDate={pub_start}&pubEndDate={pub_end}&resultsPerPage={results_per_page}"

    try:
        raw = _http_get_bytes(url, timeout=15.0)
        payload = json.loads(raw)
    except (OSError, json.JSONDecodeError) as exc:
        logger.warning("NVD fetch failed: %s", exc)
        return []

    items = parse_nvd_response(payload)
    cache.write(items)
    return items


def load(cache: FeedCache) -> list[dict] | None:
    """Read items from cache. Returns None when stale/missing."""
    return cache.read()
