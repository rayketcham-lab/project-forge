"""Scoreboard — the autonomous LEARN loop (v0.17).

The engine predicts fundability / ambition / snipe and then forgets. The
Scoreboard closes that loop: it captures *real* outcome signals for the
engine's bets and asks the only question that matters — were the
predictions right?

v1 grounds on the cleanest keyless signal available: for each Sniper idea,
the star count of the strongest open-source challenger to the named
incumbent (a real proxy for "was this a live wedge"). Signals land in the
``outcome_signals`` table; ``build_calibration`` then checks whether higher
predicted scores actually track higher realized signal, per axis and per
category, and surfaces recommendations. Recalibration stays human-gated —
consistent with the project's "autonomous thinking, gated doing" stance.

Capture is degrade-safe: an injected fetcher that returns None or raises
just skips that idea. The fetcher is injectable so tests need no network.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Callable
from datetime import UTC, datetime
from typing import TYPE_CHECKING
from uuid import uuid4

if TYPE_CHECKING:
    from project_forge.storage.db import Database

logger = logging.getLogger(__name__)

# Predicted-score band split for the median fallback isn't used; we split on
# the median of observed predictions so the report adapts to the corpus.
_MIN_PAIRS = 2


async def read_signals(db: Database) -> list[dict]:
    """All captured outcome signals, newest first."""
    cur = await db.db.execute(
        "SELECT id, idea_id, axis, predicted, metric, value, entity_ref, captured_at "
        "FROM outcome_signals ORDER BY captured_at DESC"
    )
    rows = await cur.fetchall()
    return [dict(r) for r in rows]


async def record_signal(
    db: Database,
    *,
    idea_id: str,
    axis: str,
    predicted: float,
    metric: str,
    value: float,
    entity_ref: str,
) -> None:
    """Persist one realized outcome signal."""
    await db.db.execute(
        "INSERT INTO outcome_signals "
        "(id, idea_id, axis, predicted, metric, value, entity_ref, captured_at) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        (
            uuid4().hex[:12],
            idea_id,
            axis,
            float(predicted),
            metric,
            float(value),
            entity_ref,
            datetime.now(UTC).isoformat(),
        ),
    )
    await db.db.commit()


async def capture_outcome_signals(
    db: Database,
    *,
    gh_stars: Callable[[str], int | None],
    limit: int = 50,
) -> dict:
    """Capture realized signals for the engine's Sniper bets.

    ``gh_stars(incumbent)`` returns the top OSS-challenger star count for an
    incumbent (or None to skip). Injected so tests need no network; in
    production it's wired to the market-intel feed. Never raises — a fetcher
    that errors on one incumbent just drops that signal.
    """
    cur = await db.db.execute(
        "SELECT id, snipe_score, target_incumbent FROM ideas "
        "WHERE snipe_score IS NOT NULL AND target_incumbent IS NOT NULL "
        "AND status NOT IN ('archived', 'rejected') "
        "ORDER BY generated_at DESC LIMIT ?",
        (limit,),
    )
    rows = await cur.fetchall()
    captured = 0
    for r in rows:
        incumbent = r["target_incumbent"]
        try:
            # `gh_stars` is a blocking HTTP callback; awaiting it off the
            # loop keeps the dashboard responsive while the sweep runs.
            stars = await asyncio.to_thread(gh_stars, incumbent)
        except Exception:  # noqa: BLE001 — capture is best-effort
            logger.warning("scoreboard: signal fetch failed for %s", incumbent)
            continue
        if stars is None:
            continue
        await record_signal(
            db,
            idea_id=r["id"],
            axis="snipe",
            predicted=r["snipe_score"],
            metric="oss_challenger_stars",
            value=stars,
            entity_ref=incumbent,
        )
        captured += 1
    return {"captured": captured}


def _direction(pairs: list[tuple[float, float]]) -> str:
    """Median-split: do higher predicted scores track higher realized signal?"""
    if len(pairs) < _MIN_PAIRS:
        return "insufficient"
    ordered = sorted(pairs, key=lambda p: p[0])
    mid = len(ordered) // 2
    lower = ordered[:mid] or ordered[:1]
    upper = ordered[mid:] or ordered[-1:]
    lo = sum(v for _, v in lower) / len(lower)
    hi = sum(v for _, v in upper) / len(upper)
    if hi > lo:
        return "aligned"
    if hi < lo:
        return "inverted"
    return "flat"


async def build_calibration(db: Database) -> dict:
    """Per-axis + per-category report of predicted vs realized, with recs."""
    cur = await db.db.execute(
        "SELECT s.axis, s.predicted, s.value, i.category FROM outcome_signals s LEFT JOIN ideas i ON i.id = s.idea_id"
    )
    rows = await cur.fetchall()

    by_axis: dict[str, list[tuple[float, float]]] = {}
    by_cat: dict[str, list[tuple[float, float]]] = {}
    for r in rows:
        by_axis.setdefault(r["axis"], []).append((r["predicted"], r["value"]))
        if r["category"]:
            by_cat.setdefault(r["category"], []).append((r["predicted"], r["value"]))

    axes = {}
    recommendations: list[str] = []
    for axis, pairs in by_axis.items():
        direction = _direction(pairs)
        axes[axis] = {
            "n": len(pairs),
            "direction": direction,
            "avg_predicted": round(sum(p for p, _ in pairs) / len(pairs), 3),
            "avg_realized": round(sum(v for _, v in pairs) / len(pairs), 1),
        }
        if direction == "inverted":
            recommendations.append(
                f"Axis '{axis}' is INVERTED (n={len(pairs)}): higher predicted "
                f"scores are tracking LOWER realized signal — review the "
                f"{axis} scorer weights and category bonuses."
            )
        elif direction == "flat":
            recommendations.append(
                f"Axis '{axis}' is FLAT (n={len(pairs)}): predicted score is not "
                f"separating winners from losers yet — the signal may be too "
                f"noisy or the sample too small."
            )

    categories = {
        cat: {
            "n": len(pairs),
            "avg_predicted": round(sum(p for p, _ in pairs) / len(pairs), 3),
            "avg_realized": round(sum(v for _, v in pairs) / len(pairs), 1),
        }
        for cat, pairs in by_cat.items()
    }
    if not axes:
        recommendations.append("No outcome signals captured yet — the scoreboard cadence will fill this in.")

    return {"axes": axes, "categories": categories, "recommendations": recommendations}


# --------------------------------------------------------------------------- #
# Auto-tune (gated): the engine actually learns                               #
# --------------------------------------------------------------------------- #
# When FORGE_SCOREBOARD_AUTOTUNE is on, the scoreboard cadence converts the
# calibration into small, CLAMPED per-(axis, category) score nudges and stores
# them. The fundability / ambition / snipe heuristics add the learned nudge to
# their category bonus. Default: empty cache → learned_nudge returns 0.0 →
# zero behaviour change, so existing scoring is untouched until you opt in.

_NUDGE_CLAMP = 0.05
_MIN_SIGNALS_FOR_NUDGE = 4
_NUDGE_CACHE: dict[tuple[str, str], float] = {}


def learned_nudge(axis: str, category) -> float:
    """The learned score nudge for (axis, category), or 0.0. Sync + cheap so
    the heuristic scorers can call it inline."""
    cat = getattr(category, "value", category)
    return _NUDGE_CACHE.get((axis, cat), 0.0)


async def load_nudges(db: Database) -> None:
    """Refresh the in-memory nudge cache from the calibration_weights table."""
    _NUDGE_CACHE.clear()
    try:
        cur = await db.db.execute("SELECT category, axis, nudge FROM calibration_weights")
        for r in await cur.fetchall():
            _NUDGE_CACHE[(r["axis"], r["category"])] = float(r["nudge"])
    except Exception:  # noqa: BLE001 — best-effort; missing table → no nudges
        logger.debug("load_nudges: no calibration_weights yet")


async def compute_weight_nudges(db: Database) -> list[dict]:
    """Derive small clamped nudges: reward categories that out-perform their
    predicted rank, penalize those that under-perform. Per-axis median split."""
    cur = await db.db.execute(
        "SELECT s.axis, s.predicted, s.value, i.category FROM outcome_signals s LEFT JOIN ideas i ON i.id = s.idea_id"
    )
    rows = await cur.fetchall()
    by_axis: dict[str, list[tuple[str, float, float]]] = {}
    for r in rows:
        if r["category"]:
            by_axis.setdefault(r["axis"], []).append((r["category"], r["predicted"], r["value"]))

    nudges: list[dict] = []
    for axis, items in by_axis.items():
        if len(items) < _MIN_SIGNALS_FOR_NUDGE:
            continue
        med_p = sorted(p for _, p, _ in items)[len(items) // 2]
        med_v = sorted(v for _, _, v in items)[len(items) // 2]
        cats: dict[str, list[tuple[float, float]]] = {}
        for cat, p, v in items:
            cats.setdefault(cat, []).append((p, v))
        for cat, pv in cats.items():
            ap = sum(p for p, _ in pv) / len(pv)
            av = sum(v for _, v in pv) / len(pv)
            if av > med_v and ap < med_p:
                nudges.append({"axis": axis, "category": cat, "nudge": _NUDGE_CLAMP})
            elif av < med_v and ap > med_p:
                nudges.append({"axis": axis, "category": cat, "nudge": -_NUDGE_CLAMP})
    return nudges


async def apply_autotune(db: Database) -> dict:
    """Compute + persist nudges and refresh the cache. Idempotent upsert."""
    nudges = await compute_weight_nudges(db)
    ts = datetime.now(UTC).isoformat()
    for n in nudges:
        await db.db.execute(
            "INSERT INTO calibration_weights (category, axis, nudge, updated_at) "
            "VALUES (?, ?, ?, ?) "
            "ON CONFLICT(category, axis) DO UPDATE SET "
            "nudge=excluded.nudge, updated_at=excluded.updated_at",
            (n["category"], n["axis"], n["nudge"], ts),
        )
    await db.db.commit()
    await load_nudges(db)
    return {"applied": len(nudges), "nudges": nudges}


def format_calibration_markdown(cal: dict) -> str:
    """Render a calibration report as a compact markdown memo."""
    lines = ["## Scoreboard — predicted vs realized", ""]
    axes = cal.get("axes") or {}
    if axes:
        lines.append("| Axis | n | direction | avg predicted | avg realized |")
        lines.append("|------|---|-----------|---------------|--------------|")
        for axis, a in axes.items():
            lines.append(f"| {axis} | {a['n']} | {a['direction']} | {a['avg_predicted']} | {a['avg_realized']} |")
    else:
        lines.append("_No outcome signals captured yet._")
    recs = cal.get("recommendations") or []
    if recs:
        lines.append("")
        lines.append("### Recommendations")
        lines.extend(f"- {r}" for r in recs)
    return "\n".join(lines)
