"""arXiv Atom feed parser + fetcher for the cs.CR security category.

The arXiv API returns Atom XML; we parse it with the stdlib (no extra deps).
The fetcher writes to FeedCache and degrades to empty list on network failure.
"""

from __future__ import annotations

import logging
import xml.etree.ElementTree as ET
from typing import TYPE_CHECKING

from project_forge.feeds._http import http_get_bytes as _http_get_bytes

if TYPE_CHECKING:
    from project_forge.feeds.cache import FeedCache

logger = logging.getLogger(__name__)

_NS = "{http://www.w3.org/2005/Atom}"
ARXIV_API_URL = "http://export.arxiv.org/api/query"


def parse_arxiv_atom(xml_text: str) -> list[dict]:
    """Parse arXiv Atom feed XML into feed items."""
    try:
        root = ET.fromstring(xml_text)  # noqa: S314 — input is from arxiv.org
    except ET.ParseError as exc:
        logger.warning("Failed to parse arXiv atom: %s", exc)
        return []

    items: list[dict] = []
    for entry in root.findall(f"{_NS}entry"):
        id_elem = entry.find(f"{_NS}id")
        title_elem = entry.find(f"{_NS}title")
        summary_elem = entry.find(f"{_NS}summary")
        published_elem = entry.find(f"{_NS}published")
        link_elem = entry.find(f"{_NS}link")

        url = ""
        if link_elem is not None:
            url = link_elem.get("href") or ""
        elif id_elem is not None and id_elem.text:
            url = id_elem.text

        items.append({
            "id": (id_elem.text if id_elem is not None and id_elem.text else "").strip(),
            "title": (title_elem.text if title_elem is not None and title_elem.text else "").strip(),
            "summary": (summary_elem.text if summary_elem is not None and summary_elem.text else "").strip(),
            "url": url,
            "ts": (published_elem.text if published_elem is not None and published_elem.text else "").strip(),
        })
    return items


def fetch(*, cache: FeedCache, category: str = "cs.CR", max_results: int = 25) -> list[dict]:
    """Fetch recent arXiv papers in `category` and write to cache."""
    url = (
        f"{ARXIV_API_URL}?search_query=cat:{category}"
        f"&sortBy=submittedDate&sortOrder=descending&max_results={max_results}"
    )
    try:
        raw = _http_get_bytes(url, timeout=15.0)
    except OSError as exc:
        logger.warning("arXiv fetch failed: %s", exc)
        return []

    items = parse_arxiv_atom(raw.decode("utf-8", errors="replace"))
    cache.write(items)
    return items


def load(cache: FeedCache) -> list[dict] | None:
    return cache.read()
