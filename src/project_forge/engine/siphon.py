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
import os
import re
from collections import defaultdict
from datetime import UTC, datetime

from project_forge.engine.dedup import _normalize, _tokenize, tagline_similarity
from project_forge.models import IdeaCategory
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
        "SELECT id, name, tagline, category, feasibility_score, status "
        "FROM ideas "
        "WHERE status NOT IN ('archived', 'rejected') "
        "AND name NOT LIKE '[SUPER]%'",
    )
    rows = await cur.fetchall()
    buckets: dict[str, list] = defaultdict(list)
    for r in rows:
        buckets[r["category"]].append(
            {
                "id": r["id"],
                "name": r["name"],
                "tagline": r["tagline"],
                "score": float(r["feasibility_score"] or 0.0),
                # Carried so siphon_duplicates can refuse to archive
                # human-blessed ideas (mirrors siphon_verticals).
                "status": r["status"],
            }
        )
    return buckets


def _cluster_within_bucket(
    ideas: list[dict],
    *,
    tagline_threshold: float = TAGLINE_THRESHOLD,
    name_threshold: float = NAME_THRESHOLD,
) -> list[list[dict]]:
    """Connected-components clustering by tagline / name similarity.

    Idea A and B link if EITHER:
      - tagline Jaccard ≥ tagline_threshold
      - name Jaccard ≥ name_threshold
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
            if t_sim >= tagline_threshold or n_sim >= name_threshold:
                union(i, j)

    groups: dict[int, list[dict]] = defaultdict(list)
    for i, idea in enumerate(ideas):
        groups[find(i)].append(idea)

    return list(groups.values())


async def find_duplicate_clusters(
    db: Database,
    *,
    tagline_threshold: float = TAGLINE_THRESHOLD,
    name_threshold: float = NAME_THRESHOLD,
) -> list[list[dict]]:
    """Return all clusters (size ≥ 1) of duplicate ideas across active corpus."""
    buckets = await _active_ideas_by_category(db)
    out: list[list[dict]] = []
    for ideas in buckets.values():
        for cluster in _cluster_within_bucket(
            ideas,
            tagline_threshold=tagline_threshold,
            name_threshold=name_threshold,
        ):
            out.append(cluster)
    return out


async def siphon_duplicates(
    db: Database,
    *,
    dry_run: bool = True,
    tagline_threshold: float = TAGLINE_THRESHOLD,
    name_threshold: float = NAME_THRESHOLD,
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
    clusters_raw = await find_duplicate_clusters(
        db,
        tagline_threshold=tagline_threshold,
        name_threshold=name_threshold,
    )
    report_clusters = []
    applied_ids: list[str] = []
    planned_archive: list[str] = []

    for cluster in clusters_raw:
        if len(cluster) <= 1:
            continue
        # Keep highest feasibility score; tiebreak on shorter id (stable).
        cluster.sort(key=lambda x: (-x["score"], x["id"]))
        keep = cluster[0]
        # Human-blessed ideas are never archived as duplicates, even when a
        # higher-scoring twin exists — the operator's approval outranks the
        # feasibility score. Same contract siphon_verticals enforces.
        archive = [c for c in cluster[1:] if c.get("status") not in _TERMINAL_STATUSES]
        if not archive:
            continue
        archive_ids = [c["id"] for c in archive]
        planned_archive.extend(archive_ids)

        report_clusters.append(
            {
                "members": [c["id"] for c in cluster],
                "keep": keep["id"],
                "keep_name": keep["name"],
                "keep_score": keep["score"],
                "archive": archive_ids,
            }
        )

        if not dry_run:
            now = datetime.now(UTC).isoformat()
            for victim in archive:
                await db.db.execute(
                    "UPDATE ideas SET status = 'archived', archived_reason = ?, archived_at = ? WHERE id = ?",
                    ("retroactive_dedup", now, victim["id"]),
                )
                applied_ids.append(victim["id"])
            await db.db.commit()

    return {
        "dry_run": dry_run,
        "cluster_count": len(report_clusters),
        "archived_count": len(planned_archive),
        "applied_count": len(applied_ids),
        "archived_ids": planned_archive,
        "clusters": report_clusters,
    }


# --------------------------------------------------------------------------- #
# Super-idea dedup by component overlap                                       #
# --------------------------------------------------------------------------- #


# Status values that the user has explicitly accepted; never auto-archive.
_TERMINAL_STATUSES = {"approved", "scaffolded", "implemented", "contributed", "rejected"}


_COMPONENT_BULLET_RE = re.compile(r"^-\s*\*\*(?P<name>[^*]+)\*\*", re.MULTILINE)


def _extract_super_components(description: str) -> set[str]:
    """Pull the component names out of a super-idea description.

    The synthesiser writes each component as a markdown bullet:
        - **Component Name**: blurb...
    We extract Component Name (normalised) so two supers that share an
    atom are detected even if the framing prose around them differs.
    """
    components: set[str] = set()
    for match in _COMPONENT_BULLET_RE.finditer(description):
        norm = _normalize(match.group("name"))
        if norm:
            components.add(norm)
    return components


async def _active_supers(db: Database) -> list[dict]:
    cur = await db.db.execute(
        "SELECT id, name, description, feasibility_score "
        "FROM ideas "
        "WHERE name LIKE '[SUPER]%' "
        "AND status NOT IN ('archived', 'rejected', 'approved', "
        "                   'scaffolded', 'implemented', 'contributed')"
    )
    rows = await cur.fetchall()
    return [
        {
            "id": r["id"],
            "name": r["name"],
            "components": _extract_super_components(r["description"] or ""),
            "score": float(r["feasibility_score"] or 0.0),
        }
        for r in rows
    ]


async def siphon_supers_by_components(
    db: Database,
    *,
    dry_run: bool = True,
    overlap_min: int = 3,
    name_jaccard: float = 0.65,
) -> dict:
    """Find super-idea clusters by shared atomic components OR near-identical names.

    Two supers link when EITHER:
      - they share ≥ `overlap_min` parsed atomic components, OR
      - their stripped-prefix names have token Jaccard ≥ `name_jaccard`
    (the second arm catches "Drift Tracker" / "Drift Tracking" / "Verification"
    variants whose component sets diverge but whose theme is the same).

    Lower-scored cluster members are archived with reason='super_overlap'.
    """
    supers = await _active_supers(db)
    n = len(supers)

    def _stripped_name(s: str) -> str:
        return _normalize(s.replace("[SUPER]", ""))

    # PAIRWISE archive — each super gets archived if it has at least one
    # strictly-higher-scored near-neighbor. This deliberately avoids the
    # connected-components cascade where A↔B and B↔C drag unrelated A & C
    # into the same cluster ("Privacy Compliance" ↔ "Defense Visualizer"
    # via "Automation" atoms shared by something in between).
    def _is_near_dup(a: dict, b: dict) -> bool:
        shared = len(a["components"] & b["components"])
        if shared >= overlap_min:
            return True
        n_jac = _name_token_jaccard(
            _stripped_name(a["name"]),
            _stripped_name(b["name"]),
        )
        return n_jac >= name_jaccard

    victim_to_winner: dict[int, int] = {}
    for i in range(n):
        best_winner_idx: int | None = None
        best_winner_score = supers[i]["score"]
        for j in range(n):
            if i == j:
                continue
            sj = supers[j]["score"]
            if sj < best_winner_score:
                continue
            # On tied scores, prefer the lexicographically-smaller id as a
            # stable tiebreak so the same idea always loses.
            if sj == best_winner_score and supers[j]["id"] >= supers[i]["id"]:
                continue
            if not _is_near_dup(supers[i], supers[j]):
                continue
            if best_winner_idx is None or sj > supers[best_winner_idx]["score"]:
                best_winner_idx = j
                best_winner_score = sj
        if best_winner_idx is not None:
            victim_to_winner[i] = best_winner_idx

    # Group victims by winner for a readable report.
    winners: dict[int, list[int]] = defaultdict(list)
    for v, w in victim_to_winner.items():
        winners[w].append(v)

    applied_ids: list[str] = []
    report_clusters: list[dict] = []
    planned_archive: list[str] = []
    for winner_idx, victim_idxs in winners.items():
        keep = supers[winner_idx]
        archive = [supers[v] for v in victim_idxs]
        archive_ids = [c["id"] for c in archive]
        planned_archive.extend(archive_ids)
        report_clusters.append(
            {
                "keep": keep["id"],
                "keep_name": keep["name"],
                "keep_score": keep["score"],
                "archive": archive_ids,
            }
        )
        if not dry_run:
            now = datetime.now(UTC).isoformat()
            for victim in archive:
                await db.db.execute(
                    "UPDATE ideas SET status='archived', archived_reason=?, archived_at=? WHERE id=?",
                    ("super_overlap", now, victim["id"]),
                )
                applied_ids.append(victim["id"])
            await db.db.commit()

    return {
        "dry_run": dry_run,
        "cluster_count": len(report_clusters),
        "archived_count": len(planned_archive),
        "applied_count": len(applied_ids),
        "archived_ids": planned_archive,
        "clusters": report_clusters,
    }


# --------------------------------------------------------------------------- #
# Vertical-cap collapse: 'X for {vertical}' pattern                           #
# --------------------------------------------------------------------------- #


_FOR_VERTICAL_RE = re.compile(r"\s+for\s+[A-Za-z][\w\-]*$", re.IGNORECASE)


def _strip_vertical(name: str) -> str | None:
    """Return the concept stem of 'X for {vertical}', or None if the name
    doesn't match the pattern. Case-insensitive; collapses whitespace.
    """
    m = _FOR_VERTICAL_RE.search(name)
    if not m:
        return None
    stem = name[: m.start()].strip()
    return _normalize(stem) or None


async def siphon_verticals(
    db: Database,
    *,
    dry_run: bool = True,
    cap: int = 2,
) -> dict:
    """Cap the 'X for {vertical}' clones at `cap` per stripped concept.

    Groups ideas whose names match the 'X for {vertical}' pattern by the
    normalised concept stem. Keeps the top-`cap` by feasibility_score;
    archives the rest with reason='vertical_cap'. Skips terminal-status
    ideas (approved/scaffolded/implemented/contributed) and also excludes
    them from the cap budget — a human-blessed idea shouldn't push an
    unrelated 'new' idea off the keep list.
    """
    cur = await db.db.execute(
        "SELECT id, name, feasibility_score, status "
        "FROM ideas "
        "WHERE lower(name) LIKE '% for %' "
        "AND status NOT IN ('archived', 'rejected')"
    )
    rows = await cur.fetchall()

    groups: dict[str, list[dict]] = defaultdict(list)
    for r in rows:
        stem = _strip_vertical(r["name"])
        if stem is None:
            continue
        groups[stem].append(
            {
                "id": r["id"],
                "name": r["name"],
                "score": float(r["feasibility_score"] or 0.0),
                "status": r["status"],
            }
        )

    applied_ids: list[str] = []
    report_clusters: list[dict] = []
    planned_archive: list[str] = []
    for stem, members in groups.items():
        # Terminal-status members are protected — they survive unconditionally
        # and do NOT count against the cap budget. The cap caps how many
        # *new* ideas we keep per concept; a human-blessed idea sitting in
        # the same concept is additive, not competitive.
        protected = [m for m in members if m["status"] in _TERMINAL_STATUSES]
        candidates = sorted(
            (m for m in members if m["status"] not in _TERMINAL_STATUSES),
            key=lambda m: (-m["score"], m["id"]),
        )
        if len(candidates) <= cap:
            continue
        kept = candidates[:cap]
        archive = candidates[cap:]
        if not archive:
            continue
        archive_ids = [a["id"] for a in archive]
        planned_archive.extend(archive_ids)
        report_clusters.append(
            {
                "concept": stem,
                "keep": [k["id"] for k in (protected + kept)],
                "archive": archive_ids,
            }
        )
        if not dry_run:
            now = datetime.now(UTC).isoformat()
            for victim in archive:
                await db.db.execute(
                    "UPDATE ideas SET status='archived', archived_reason=?, archived_at=? WHERE id=?",
                    ("vertical_cap", now, victim["id"]),
                )
                applied_ids.append(victim["id"])
            await db.db.commit()

    return {
        "dry_run": dry_run,
        "cluster_count": len(report_clusters),
        "archived_count": len(planned_archive),
        "applied_count": len(applied_ids),
        "archived_ids": planned_archive,
        "clusters": report_clusters,
    }


# --------------------------------------------------------------------------- #
# Density thinning (#97)                                                      #
# --------------------------------------------------------------------------- #


# Per-category active cap for the density pass. The near-dupe siphons above
# collapse PARAPHRASES; this pass thins DISTINCT-but-crowded zones the
# pairwise thresholds can never reach (a 191-idea category of unique
# micro-saas pitches contains few pairs above any similarity bar).
# 60 -> 50 in #98: with 36 market categories, cap x categories IS the
# steady-state pool size; 50 holds the whole pool under ~1.9k active.
DENSITY_CAP = int(os.environ.get("FORGE_DENSITY_CAP", "50"))


def _density_composite(row) -> float:
    """Keep-ranking for the density pass: the best signal we have about an
    idea. Max board-axis score when any exists; otherwise feasibility
    scaled down (template feasibility is uniform noise); small bonus for
    LLM-mode ideas over template ones."""
    axes = [
        row["fundability_score"],
        row["ambition_score"],
        row["snipe_score"],
        row["cashflow_score"],
    ]
    scored = [a for a in axes if a is not None]
    base = max(scored) if scored else (row["feasibility_score"] or 0.0) * 0.85
    if row["generation_mode"]:
        base += 0.05
    return base


def _density_protected(row) -> bool:
    """Rows the density pass must never archive: operator-touched lifecycle
    states, operator-directed (mission) or operator-ingested (source_url)
    ideas, anything ever promoted to GitHub, and super-ideas (they have
    their own siphon)."""
    return bool(
        row["status"] != "new"
        or row["mission_id"]
        or row["source_url"]
        or row["github_issue_url"]
        or (row["name"] or "").startswith("[SUPER]")
    )


async def siphon_density(
    db: Database,
    *,
    dry_run: bool = True,
    cap: int | None = None,
) -> dict:
    """Thin every over-cap category down to `cap` active ideas, keeping
    protected rows plus the best of the rest by `_density_composite`.
    Victims get archived_reason='saturation_thin' — fully reversible via
    `restore_dedup_archive`. SELF_IMPROVEMENT is exempt (introspection-
    engine-managed, not a market category)."""
    cap = DENSITY_CAP if cap is None else cap
    cur = await db.db.execute(
        "SELECT id, name, category, status, generated_at, generation_mode, "
        "mission_id, source_url, github_issue_url, feasibility_score, "
        "fundability_score, ambition_score, snipe_score, cashflow_score "
        "FROM ideas WHERE status NOT IN ('archived', 'rejected')"
    )
    rows = await cur.fetchall()

    by_category: dict[str, list] = defaultdict(list)
    for row in rows:
        by_category[row["category"]].append(row)

    report_categories: list[dict] = []
    planned_archive: list[str] = []
    applied_ids: list[str] = []

    for cat_value, members in sorted(by_category.items()):
        if cat_value == IdeaCategory.SELF_IMPROVEMENT.value:
            continue
        if len(members) <= cap:
            continue
        protected = [r for r in members if _density_protected(r)]
        pool = [r for r in members if not _density_protected(r)]
        keep_n = max(0, cap - len(protected))
        pool.sort(key=lambda r: (-_density_composite(r), r["generated_at"], r["id"]))
        victims = pool[keep_n:]
        if not victims:
            continue
        victim_ids = [r["id"] for r in victims]
        planned_archive.extend(victim_ids)
        report_categories.append(
            {
                "category": cat_value,
                "active": len(members),
                "protected": len(protected),
                "kept": len(protected) + keep_n,
                "archived_count": len(victim_ids),
            }
        )
        if not dry_run:
            now = datetime.now(UTC).isoformat()
            for vid in victim_ids:
                await db.db.execute(
                    "UPDATE ideas SET status = 'archived', archived_reason = ?, archived_at = ? WHERE id = ?",
                    ("saturation_thin", now, vid),
                )
                applied_ids.append(vid)
            await db.db.commit()

    return {
        "dry_run": dry_run,
        "cap": cap,
        "categories": report_categories,
        "archived_count": len(planned_archive),
        "applied_count": len(applied_ids),
        "archived_ids": planned_archive,
    }


# --------------------------------------------------------------------------- #
# Cross-category dedup (#98)                                                  #
# --------------------------------------------------------------------------- #


# Link threshold for the retro cross-category pass. Looser than the 0.80
# INSERT gate (retro output is reviewable + reversible) but tighter than the
# 0.45 within-category atomic profile — cross-category false positives are
# costlier, and name similarity is deliberately NOT used here (it is
# same-category-scoped by contract).
CROSS_TAGLINE_THRESHOLD = 0.60

# Prefilter: skip tokens shared by more than this many taglines — a token
# like "detector" links half the corpus and adds nothing but O(n^2) pain.
_CROSS_BUCKET_CAP = 200


async def siphon_cross_category(
    db: Database,
    *,
    dry_run: bool = True,
    tagline_threshold: float = CROSS_TAGLINE_THRESHOLD,
) -> dict:
    """Find near-duplicate taglines ACROSS categories and (optionally)
    archive the losers as 'cross_category_dedup' (reversible).

    Only clusters spanning >= 2 distinct categories are acted on —
    within-category paraphrases are `siphon_duplicates`' job at its own
    thresholds. Keep = best `_density_composite`; protected rows
    (`_density_protected`) are never archived. SI and supers excluded.
    """
    cur = await db.db.execute(
        "SELECT id, name, tagline, category, status, generated_at, generation_mode, "
        "mission_id, source_url, github_issue_url, feasibility_score, "
        "fundability_score, ambition_score, snipe_score, cashflow_score "
        "FROM ideas WHERE status NOT IN ('archived', 'rejected') "
        "AND category != 'self-improvement' AND name NOT LIKE '[SUPER]%'"
    )
    rows = list(await cur.fetchall())
    n = len(rows)
    tokens = [_tokenize(r["tagline"] or "") for r in rows]

    # Token inverted index → candidate pairs share at least one useful token.
    index: dict[str, list[int]] = defaultdict(list)
    for i, toks in enumerate(tokens):
        for t in toks:
            index[t].append(i)

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

    seen: set[tuple[int, int]] = set()
    for ids in index.values():
        if len(ids) > _CROSS_BUCKET_CAP:
            continue
        for ai in range(len(ids)):
            for bi in range(ai + 1, len(ids)):
                pair = (ids[ai], ids[bi])
                if pair in seen:
                    continue
                seen.add(pair)
                ta, tb = tokens[pair[0]], tokens[pair[1]]
                if not ta or not tb:
                    continue
                if len(ta & tb) / len(ta | tb) >= tagline_threshold:
                    union(pair[0], pair[1])

    groups: dict[int, list[int]] = defaultdict(list)
    for i in range(n):
        groups[find(i)].append(i)

    report_clusters: list[dict] = []
    planned_archive: list[str] = []
    applied_ids: list[str] = []

    for members in groups.values():
        if len(members) < 2:
            continue
        cats = {rows[i]["category"] for i in members}
        if len(cats) < 2:
            continue  # within-category — the atomic pass owns it
        ranked = sorted(
            members,
            key=lambda i: (-_density_composite(rows[i]), rows[i]["generated_at"], rows[i]["id"]),
        )
        keep = ranked[0]
        victims = [i for i in ranked[1:] if not _density_protected(rows[i])]
        if not victims:
            continue
        victim_ids = [rows[i]["id"] for i in victims]
        planned_archive.extend(victim_ids)
        report_clusters.append(
            {
                "categories": sorted(cats),
                "members": [rows[i]["id"] for i in members],
                "keep": rows[keep]["id"],
                "keep_name": rows[keep]["name"],
                "archive": victim_ids,
            }
        )
        if not dry_run:
            now = datetime.now(UTC).isoformat()
            for vid in victim_ids:
                await db.db.execute(
                    "UPDATE ideas SET status = 'archived', archived_reason = ?, archived_at = ? WHERE id = ?",
                    ("cross_category_dedup", now, vid),
                )
                applied_ids.append(vid)
            await db.db.commit()

    return {
        "dry_run": dry_run,
        "cluster_count": len(report_clusters),
        "archived_count": len(planned_archive),
        "applied_count": len(applied_ids),
        "archived_ids": planned_archive,
        "clusters": report_clusters,
    }


# --------------------------------------------------------------------------- #
# Combined entrypoint                                                         #
# --------------------------------------------------------------------------- #


async def siphon_all(
    db: Database,
    *,
    dry_run: bool = True,
    atomic_tagline_threshold: float = 0.45,
    atomic_name_threshold: float = 0.55,
    super_overlap_min: int = 3,
    super_name_jaccard: float = 0.65,
    vertical_cap: int = 2,
    density_cap: int | None = None,
) -> dict:
    """Run all four siphons (atomic / super / vertical / density) and
    return a combined report. Defaults are the aggressive one-shot trim
    profile. Density runs LAST so paraphrase clusters collapse first and
    the cap keeps the most diverse best-of-category set (#97)."""
    atomic = await siphon_duplicates(
        db,
        dry_run=dry_run,
        tagline_threshold=atomic_tagline_threshold,
        name_threshold=atomic_name_threshold,
    )
    supers = await siphon_supers_by_components(
        db,
        dry_run=dry_run,
        overlap_min=super_overlap_min,
        name_jaccard=super_name_jaccard,
    )
    verticals = await siphon_verticals(db, dry_run=dry_run, cap=vertical_cap)
    cross = await siphon_cross_category(db, dry_run=dry_run)
    density = await siphon_density(db, dry_run=dry_run, cap=density_cap)
    return {
        "dry_run": dry_run,
        "atomic": atomic,
        "supers": supers,
        "verticals": verticals,
        "cross": cross,
        "density": density,
        "total_archived": atomic["archived_count"]
        + supers["archived_count"]
        + verticals["archived_count"]
        + cross["archived_count"]
        + density["archived_count"],
    }


_DEDUP_REASONS = ("retroactive_dedup", "super_overlap", "vertical_cap", "saturation_thin", "cross_category_dedup")


async def restore_dedup_archive(
    db: Database,
    reasons: tuple[str, ...] = _DEDUP_REASONS,
) -> int:
    """Reverse a siphon apply. Restores only rows the siphon archived
    (archived_reason in `reasons`); leaves manually-archived ideas
    untouched. Default covers all three siphon reasons —
    `retroactive_dedup` (atomic), `super_overlap` (super-component dedup),
    and `vertical_cap` ('X for {vertical}' collapse). Pass a narrower
    tuple to restore only one kind. Returns the number of rows restored.
    """
    # Placeholders are `?,?,?` built from the *length* of the reasons tuple,
    # not from the values themselves. Values are passed parameterised. The
    # noqa silences ruff's S608 — there is no string interpolation of caller
    # data into the SQL text.
    placeholders = ",".join("?" * len(reasons))
    cur = await db.db.execute(
        f"SELECT COUNT(*) FROM ideas WHERE archived_reason IN ({placeholders})",  # noqa: S608
        reasons,
    )
    n = (await cur.fetchone())[0]
    if n == 0:
        return 0
    # Placeholders is `?,?,?` derived from len(reasons); no caller-controlled
    # string interpolation. Ruff S608 false-positive.
    update_sql = (
        "UPDATE ideas SET status = 'new', archived_reason = NULL, "  # noqa: S608
        f"archived_at = NULL WHERE archived_reason IN ({placeholders})"
    )
    await db.db.execute(update_sql, reasons)
    await db.db.commit()
    return n
