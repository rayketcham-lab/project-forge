"""IETF Internet-Drafts RSS feed parser + fetcher.

Pulls from the IETF Datatracker's RSS for new draft submissions:
https://datatracker.ietf.org/feed/last-call/

Each item names a current standardization effort, which is great seed
material — concrete, technical, time-stamped, and pre-standardization
(real opportunity space for tooling).
"""

from __future__ import annotations

import logging
import xml.etree.ElementTree as ET
from typing import TYPE_CHECKING

from project_forge.feeds._http import http_get_bytes as _http_get_bytes

if TYPE_CHECKING:
    from project_forge.feeds.cache import FeedCache

logger = logging.getLogger(__name__)

IETF_FEED_URL = "https://datatracker.ietf.org/feed/last-call/"


def parse_ietf_rss(xml_text: str) -> list[dict]:
    """Convert IETF RSS XML into feed items."""
    try:
        root = ET.fromstring(xml_text)  # noqa: S314 — input is from datatracker.ietf.org
    except ET.ParseError as exc:
        logger.warning("Failed to parse IETF RSS: %s", exc)
        return []

    items: list[dict] = []
    for item in root.iter("item"):
        title = (item.findtext("title") or "").strip()
        desc = (item.findtext("description") or "").strip()
        link = (item.findtext("link") or "").strip()
        pub = (item.findtext("pubDate") or "").strip()
        if not title:
            continue
        items.append({
            "id": title,  # draft name is the natural id
            "title": title,
            "summary": desc,
            "url": link,
            "ts": pub,
        })
    return items


def fetch(*, cache: FeedCache) -> list[dict]:
    """Fetch IETF I-D last-call RSS and write to cache."""
    try:
        raw = _http_get_bytes(IETF_FEED_URL, timeout=15.0)
    except OSError as exc:
        logger.warning("IETF fetch failed: %s", exc)
        return []

    items = parse_ietf_rss(raw.decode("utf-8", errors="replace"))
    cache.write(items)
    return items


def load(cache: FeedCache) -> list[dict] | None:
    return cache.read()
