"""Pulse feed — event-driven world signals for reactive idea generation.

Watches two keyless, real-time sources:
  - HN front page via Algolia  (https://hn.algolia.com/api/v1/search?tags=front_page)
  - GitHub recently-trending repos (api.github.com/search/repositories?q=created:>DATE&sort=stars)

Each signal is normalised to: {source, title, url, score, ts}.
``fetch_pulse_signals`` merges both sources into one ranked list.
``pick_hot_signal`` returns the highest-scoring signal.
``signal_to_seed`` converts a signal into a one-line generation seed
that the churn pipeline can prepend to an LLM prompt.

Module shape mirrors market_intel.py exactly: pure parse_* functions,
injectable http_get, degrade to [] on any network/parse failure, no
LLM required, no secrets, no side effects in parse_*.
"""

from __future__ import annotations

import json
import logging
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from typing import Any

from project_forge.feeds._http import http_get_bytes as _http_get_bytes

logger = logging.getLogger(__name__)

HN_FRONT_URL = "https://hn.algolia.com/api/v1/search?tags=front_page&hitsPerPage=20"
GITHUB_SEARCH_URL = "https://api.github.com/search/repositories"


# --------------------------------------------------------------------------- #
# URL helpers                                                                  #
# --------------------------------------------------------------------------- #


def _github_trending_url(days_back: int = 7) -> str:
    """Build the GitHub trending URL for repos created within the last N days."""
    since = (datetime.now(UTC) - timedelta(days=days_back)).strftime("%Y-%m-%d")
    return f"{GITHUB_SEARCH_URL}?q=created:>{since}&sort=stars&order=desc&per_page=10"


# --------------------------------------------------------------------------- #
# Parsers — pure, no I/O, testable in isolation                               #
# --------------------------------------------------------------------------- #


def parse_hn_front(payload: dict[str, Any], *, max_items: int = 10) -> list[dict]:
    """Convert an HN Algolia front-page response into normalised pulse signals.

    Each returned dict: {source, title, url, score, ts}.
    Sorted by score descending. Skips hits with no title.
    """
    signals: list[dict] = []
    for hit in payload.get("hits") or []:
        title = (hit.get("title") or "").strip()
        if not title:
            continue
        object_id = hit.get("objectID", "")
        signals.append(
            {
                "source": "hn",
                "title": title,
                "url": (hit.get("url") or f"https://news.ycombinator.com/item?id={object_id}"),
                "score": int(hit.get("points") or 0),
                "ts": hit.get("created_at", ""),
            }
        )
    signals.sort(key=lambda s: s["score"], reverse=True)
    return signals[:max_items]


def parse_github_trending(payload: dict[str, Any], *, max_items: int = 10) -> list[dict]:
    """Convert a GitHub repo-search response into normalised pulse signals.

    Each returned dict: {source, title, url, score, ts}.
    Sorted by score (star count) descending. Skips repos with no full_name.
    """
    signals: list[dict] = []
    for repo in payload.get("items") or []:
        full = (repo.get("full_name") or "").strip()
        if not full:
            continue
        signals.append(
            {
                "source": "github",
                "title": full,
                "url": repo.get("html_url") or f"https://github.com/{full}",
                "score": int(repo.get("stargazers_count") or 0),
                "ts": repo.get("created_at", ""),
            }
        )
    signals.sort(key=lambda s: s["score"], reverse=True)
    return signals[:max_items]


# --------------------------------------------------------------------------- #
# Internal fetch helper — mirrors market_intel._get_json exactly             #
# --------------------------------------------------------------------------- #


def _get_json(url: str, http_get: Callable[..., bytes]) -> dict[str, Any]:
    """Fetch + parse JSON, degrading to {} on any network/parse failure."""
    try:
        raw = http_get(url, timeout=12.0)
        return json.loads(raw)
    except (OSError, json.JSONDecodeError, ValueError) as exc:
        logger.warning("pulse fetch failed for %s: %s", url, exc)
        return {}


# --------------------------------------------------------------------------- #
# Public API                                                                   #
# --------------------------------------------------------------------------- #


def fetch_pulse_signals(
    *,
    http_get: Callable[..., bytes] = _http_get_bytes,
    max_items: int = 10,
    days_back: int = 7,
) -> list[dict]:
    """Fetch and merge pulse signals from HN front page + GitHub trending.

    Returns a unified list of {source, title, url, score, ts} dicts sorted
    by score descending. Degrades to [] if all sources fail — a partial
    failure (one source down) still surfaces the other source's signals.
    """
    hn_payload = _get_json(HN_FRONT_URL, http_get)
    hn_signals = parse_hn_front(hn_payload, max_items=max_items)

    gh_url = _github_trending_url(days_back=days_back)
    gh_payload = _get_json(gh_url, http_get)
    gh_signals = parse_github_trending(gh_payload, max_items=max_items)

    merged = hn_signals + gh_signals
    merged.sort(key=lambda s: s["score"], reverse=True)
    return merged[: max_items * 2]


def pick_hot_signal(signals: list[dict]) -> dict | None:
    """Return the signal with the highest score, or None if the list is empty."""
    if not signals:
        return None
    return max(signals, key=lambda s: s["score"])


def signal_to_seed(signal: dict) -> str:
    """Convert a pulse signal into a one-line generation seed for the LLM prompt.

    The seed anchors the generator to a real-world event or trend without
    prescribing the solution. Designed to be prepended to a standard
    generation prompt so it survives any prompt structure.
    """
    source_label = "Hacker News" if signal.get("source") == "hn" else "GitHub trending"
    title = signal.get("title", "")
    url = signal.get("url", "")
    score = signal.get("score", 0)
    return (
        f"React to this {source_label} signal (score {score}): "
        f'"{title}" — {url} — '
        "generate a project idea directly inspired by or solving the problem this represents."
    )


__all__ = [
    "fetch_pulse_signals",
    "parse_github_trending",
    "parse_hn_front",
    "pick_hot_signal",
    "signal_to_seed",
]
