"""External signal feeds for fresh idea seeds.

Each feed module provides:
- fetch(*, cache): network call + cache write, returns parsed items
- load(cache): read items from cache (None if stale/missing)

Common helpers in this package:
- FeedCache: file-backed JSON cache with TTL
- FeedHealth: ok/age/count summary
- format_for_prompt: render items as seed lines for build_generation_prompt
- get_external_seeds: aggregate items across all feeds (skip unhealthy)
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from project_forge.feeds.cache import FeedCache


def get_external_seeds(
    *,
    nvd_cache: FeedCache | None = None,
    arxiv_cache: FeedCache | None = None,
    ietf_cache: FeedCache | None = None,
    max_per_feed: int = 5,
) -> list[dict]:
    """Aggregate cached items across all healthy feeds.

    Stale/missing caches are silently skipped — never raise. Caller passes
    None for any feed it doesn't want included.
    """
    out: list[dict] = []
    for cache in (nvd_cache, arxiv_cache, ietf_cache):
        if cache is None:
            continue
        items = cache.read()
        if not items:
            continue
        out.extend(items[:max_per_feed])
    return out


def format_for_prompt(items: list[dict], max_items: int = 5) -> str:
    """Render feed items as compact seed lines for the LLM prompt.

    Each line: "- [<id>] <title>: <summary>".
    Returns empty string when items is empty.
    """
    if not items:
        return ""

    lines = []
    for item in items[:max_items]:
        id_ = item.get("id", "?")
        title = item.get("title", "")
        summary = item.get("summary", "")
        # Trim summary aggressively — prompt budget
        summary = summary.strip().replace("\n", " ")[:160]
        lines.append(f"- [{id_}] {title}: {summary}")
    return "\n".join(lines)


__all__ = ["format_for_prompt", "get_external_seeds"]
