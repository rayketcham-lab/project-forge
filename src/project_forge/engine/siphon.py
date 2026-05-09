"""Idea siphon — find near-duplicate clusters and archive the losers (#71).

One-shot retroactive dedup. The existing engine.dedup gate runs at INSERT
time but historical ideas accumulated under looser rules; this module
walks the corpus and surfaces clusters of paraphrases that slipped
through.

Two modes:
  - dry_run=True   → return a structured report; no mutation.
  - dry_run=False  → archive every cluster member except the highest
    feasibility score; mark archived_reason='retroactive_dedup'.

Idempotent: subsequent runs find no new clusters because the losers
are already archived (filtered out by the active-status guard).
"""

from __future__ import annotations

import logging
from collections import defaultdict
from datetime import UTC, datetime

from project_forge.engine.dedup import _normalize, tagline_similarity
from project_forge.storage.db import Database

logger = logging.getLogger(__name__)

# Tighter than the engine.dedup INSERT-time threshold — retroactive dedup
# can be more aggressive because the user reviews dry-run output before any
# mutation; false positives are visible and reversible.
TAGLINE_THRESHOLD = 0.6
NAME_THRESHOLD = 0.7


def _name_token_jaccard(a: str, b: str) -> float:
    ta = set(_normalize(a).split())
    tb = set(_normalize(b).split())
    if not ta and not tb:
        return 1.0
    if not ta or not tb:
        return 0.0
    return len(ta & tb) / len(ta | tb)


async def _active_ideas_by_category(db: Database) -> dict[str, list]:
    """Bucket active ideas by category (drops O(N²) → O(N²/K))."""
    cur = await db.db.execute(
        "SELECT id, name, tagline, category, feasibility_score "
        "FROM ideas "
        "WHERE status NOT IN ('archived', 'rejected') "
        "AND name NOT LIKE '[SUPER]%'",
    )
    rows = await cur.fetchall()
    buckets: dict[str, list] = defaultdict(list)
    for r in rows:
        buckets[r["category"]].append({
            "id": r["id"],
            "name": r["name"],
            "tagline": r["tagline"],
            "score": float(r["feasibility_score"] or 0.0),
        })
    return buckets


def _cluster_within_bucket(ideas: list[dict]) -> list[list[dict]]:
    """Connected-components clustering by tagline / name similarity.

    Idea A and B link if EITHER:
      - tagline Jaccard ≥ TAGLINE_THRESHOLD
      - name Jaccard ≥ NAME_THRESHOLD
    Then transitive closure over all links.
    """
    n = len(ideas)
    parent = list(range(n))

    def find(x: int) -> int:
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    def union(a: int, b: int) -> None:
        ra, rb = find(a), find(b)
        if ra != rb:
            parent[ra] = rb

    for i in range(n):
        for j in range(i + 1, n):
            t_sim = tagline_similarity(ideas[i]["tagline"], ideas[j]["tagline"])
            n_sim = _name_token_jaccard(ideas[i]["name"], ideas[j]["name"])
            if t_sim >= TAGLINE_THRESHOLD or n_sim >= NAME_THRESHOLD:
                union(i, j)

    groups: dict[int, list[dict]] = defaultdict(list)
    for i, idea in enumerate(ideas):
        groups[find(i)].append(idea)

    return list(groups.values())


async def find_duplicate_clusters(db: Database) -> list[list[dict]]:
    """Return all clusters (size ≥ 1) of duplicate ideas across active corpus."""
    buckets = await _active_ideas_by_category(db)
    out: list[list[dict]] = []
    for ideas in buckets.values():
        for cluster in _cluster_within_bucket(ideas):
            out.append(cluster)
    return out


async def siphon_duplicates(
    db: Database, *, dry_run: bool = True,
) -> dict:
    """Find duplicate clusters and (optionally) archive the losers.

    Returns a structured report:
        {
          "dry_run": bool,
          "clusters": [
             {"category": str, "members": [id, ...], "keep": id, "archive": [id, ...]},
             ...
          ],
          "cluster_count": int,
          "archived_count": int,  # 0 when dry_run
        }
    """
    clusters_raw = await find_duplicate_clusters(db)
    report_clusters = []
    archived_ids: list[str] = []

    for cluster in clusters_raw:
        if len(cluster) <= 1:
            continue
        # Keep highest feasibility score; tiebreak on shorter id (stable).
        cluster.sort(key=lambda x: (-x["score"], x["id"]))
        keep = cluster[0]
        archive = cluster[1:]

        report_clusters.append({
            "members": [c["id"] for c in cluster],
            "keep": keep["id"],
            "keep_name": keep["name"],
            "keep_score": keep["score"],
            "archive": [c["id"] for c in archive],
        })

        if not dry_run:
            now = datetime.now(UTC).isoformat()
            for victim in archive:
                await db.db.execute(
                    "UPDATE ideas SET status = 'archived', "
                    "archived_reason = ?, archived_at = ? "
                    "WHERE id = ?",
                    ("retroactive_dedup", now, victim["id"]),
                )
                archived_ids.append(victim["id"])
            await db.db.commit()

    return {
        "dry_run": dry_run,
        "cluster_count": len(report_clusters),
        "archived_count": len(archived_ids),
        "archived_ids": archived_ids,
        "clusters": report_clusters,
    }
