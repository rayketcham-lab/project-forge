"""External signal feeds for fresh idea seeds.

Each feed module provides:
- fetch(): network call + cache write
- load_cached(): read items from cache (None if stale/missing)
- health(): FeedHealth status

Common helpers in this package:
- FeedCache: file-backed JSON cache with TTL
- FeedHealth: ok/age/count summary
- format_for_prompt: render items as seed lines for build_generation_prompt
"""

from __future__ import annotations


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


__all__ = ["format_for_prompt"]
