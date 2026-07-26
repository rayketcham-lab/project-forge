"""API and page routes for the Project Forge dashboard."""

import asyncio
import logging
import subprocess
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Literal, get_args
from urllib.parse import urlparse

from fastapi import APIRouter, HTTPException, Query, Request
from fastapi.responses import HTMLResponse
from pydantic import BaseModel, Field, field_validator

from project_forge.config import settings
from project_forge.engine.dedup import filter_and_save
from project_forge.engine.llm_backend import resolve_backend
from project_forge.engine.scorer import score_summary
from project_forge.models import (
    CASHFLOW_CATEGORIES,
    CLAUDE_LAB_CATEGORIES,
    CRYPTO_CATEGORIES,
    MONEY_CATEGORIES,
    SNIPER_CATEGORIES,
    Challenge,
    Idea,
    IdeaCategory,
    IdeaDenial,
    IdeaStatus,
    Mission,
    MissionCreateRequest,
    Resource,
    SelectionRound,
    TextIngestRequest,
    UrlIngestRequest,
)
from project_forge.scaffold.github import create_issue
from project_forge.web.app import db, templates

logger = logging.getLogger(__name__)
router = APIRouter()


# === PAGE ROUTES ===


@router.get("/", response_class=HTMLResponse)
async def dashboard(request: Request):
    from project_forge.engine.verticals import KNOWN_VERTICALS, infer_verticals

    stats = await db.get_stats()
    all_top = await db.list_ideas(limit=20)
    all_top.sort(key=lambda i: i.feasibility_score, reverse=True)
    top_ideas = [i for i in all_top if not i.name.startswith("[SUPER]")][:6]
    # Dedicated query for super ideas — no cap, shows all active ones
    super_ideas = await db.list_super_ideas()
    # SQL-optimized category counts + avg scores (no in-memory loading)
    cat_counts = await db.count_ideas_by_category()
    cursor = await db.db.execute("SELECT category, AVG(feasibility_score) FROM ideas GROUP BY category")
    cat_avgs = {row[0]: round(row[1], 2) for row in await cursor.fetchall()}
    categories = [
        {"name": cat, "count": cat_counts.get(cat, 0), "avg_score": cat_avgs.get(cat, 0)} for cat in cat_counts
    ]

    # Per-vertical counts + top idea per vertical. Sample a wide pool because
    # vertical inference is content-based (not indexed); 500 captures the
    # high-feasibility tail well enough for the panel. Single-pass grouping
    # — call infer_verticals ONCE per idea (was N×V → 6s, now N → ~600ms).
    pool = await db.list_ideas(limit=500)
    by_vertical: dict[str, list[Idea]] = {slug: [] for slug in KNOWN_VERTICALS}
    for idea in pool:
        for slug in infer_verticals(idea):
            by_vertical[slug].append(idea)
    vertical_data = []
    for slug, matches in by_vertical.items():
        if not matches:
            continue
        matches.sort(key=lambda i: i.feasibility_score, reverse=True)
        vertical_data.append({"slug": slug, "count": len(matches), "top": matches[0]})
    vertical_data.sort(key=lambda v: v["count"], reverse=True)

    return templates.TemplateResponse(
        request,
        "dashboard.html",
        {
            "stats": stats,
            "top_ideas": top_ideas,
            "super_ideas": super_ideas,
            "categories": sorted(categories, key=lambda c: c["count"], reverse=True),
            "verticals": vertical_data,
            "score_summary": score_summary,
        },
    )


@router.get("/explore", response_class=HTMLResponse)
async def explore(
    request: Request,
    category: str | None = None,
    status: str | None = None,
    vertical: str | None = None,
    q: str | None = None,
    challenged: int = 0,
    page: int = Query(default=1, ge=1),
):
    from project_forge.engine.verticals import KNOWN_VERTICALS, matches_vertical

    status = status or None  # treat ?status= (empty string) as no filter
    if status is not None and status not in get_args(IdeaStatus):
        raise HTTPException(status_code=422, detail=f"Invalid status: {status!r}")
    typed_status: IdeaStatus | None = status  # type: ignore[assignment]
    if vertical and vertical not in KNOWN_VERTICALS:
        # Render empty result instead of 400 — keeps URL bookmarkable.
        vertical = "__nomatch__"
    limit = 12
    if category:
        try:
            cat = IdeaCategory(category)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=f"Unknown category: {category!r}") from exc
    else:
        cat = None

    if challenged:
        # ?challenged=1 short-circuits other filters: the set of challenged
        # ideas is small enough (typically <50) that we list all of them and
        # paginate the result client-side. We still respect ?q if present.
        offset = (page - 1) * limit
        if q:
            all_chal = await db.list_challenged_ideas(limit=10000)
            ql = q.lower()
            filtered = [
                i for i in all_chal if ql in i.name.lower() or ql in i.tagline.lower() or ql in i.description.lower()
            ]
        else:
            filtered = await db.list_challenged_ideas(limit=10000)
        total = len(filtered)
        ideas = filtered[offset : offset + limit]
        return templates.TemplateResponse(
            request,
            "explore.html",
            {
                "ideas": ideas,
                "total": total,
                "page": page,
                "pages": max(1, (total + limit - 1) // limit),
                "status_filter": status,
                "category_filter": category,
                "vertical_filter": None,
                "challenged_filter": True,
                "search_query": q or "",
                "categories": list(IdeaCategory),
                "verticals": sorted(KNOWN_VERTICALS),
                "score_summary": score_summary,
            },
        )

    if vertical:
        # Vertical filter is inferred from text — applied in Python after fetch.
        # Pull a wider window, filter, then paginate.
        if q:
            candidates = await db.search_ideas(q, limit=10000)
        else:
            candidates = await db.list_ideas(
                status=typed_status,
                category=cat,
                limit=10000,
                offset=0,
            )
        if vertical == "__nomatch__":
            filtered = []
        else:
            filtered = [i for i in candidates if matches_vertical(i, vertical)]
        total = len(filtered)
        offset = (page - 1) * limit
        ideas = filtered[offset : offset + limit]
    else:
        offset = (page - 1) * limit
        if q:
            ideas = await db.search_ideas(q, limit=limit, offset=offset)
            total = len(await db.search_ideas(q, limit=10000))
        else:
            ideas = await db.list_ideas(
                status=typed_status,
                category=cat,
                limit=limit,
                offset=offset,
            )
            total = await db.count_ideas(status=typed_status)

    # Pass canonical verticals through to the template for chip rendering
    return templates.TemplateResponse(
        request,
        "explore.html",
        {
            "ideas": ideas,
            "total": total,
            "page": page,
            "pages": max(1, (total + limit - 1) // limit),
            "status_filter": status,
            "category_filter": category,
            "vertical_filter": (vertical if vertical != "__nomatch__" else None),
            "challenged_filter": False,
            "search_query": q or "",
            "categories": list(IdeaCategory),
            "verticals": sorted(KNOWN_VERTICALS),
            "score_summary": score_summary,
        },
    )


# Derived from the canonical groupings in models.py so /money-bots,
# /claude-lab, the stats counter, and the auto-promote picker can't drift.
# Kept as plain .value strings here to match the SQL bindings below.
_MONEY_CATEGORIES = tuple(c.value for c in MONEY_CATEGORIES)

_CLAUDE_LAB_CATEGORIES = tuple(c.value for c in CLAUDE_LAB_CATEGORIES)

_SNIPER_CATEGORIES = tuple(c.value for c in SNIPER_CATEGORIES)

_CRYPTO_CATEGORIES = tuple(c.value for c in CRYPTO_CATEGORIES)

_CASHFLOW_CATEGORIES = tuple(c.value for c in CASHFLOW_CATEGORIES)


@router.get("/claude-lab", response_class=HTMLResponse)
async def claude_lab(
    request: Request,
    category: str | None = None,
    limit: int = Query(default=30, ge=1, le=100),
):
    """Frontier-AI ideas across the Claude / agent ecosystem, sorted by
    ambition_score DESC. Mirrors /money-bots but for the
    'how do we extend Claude' question instead of 'how do we monetize'."""
    cats = (category,) if category in _CLAUDE_LAB_CATEGORIES else _CLAUDE_LAB_CATEGORIES
    placeholders = ",".join("?" * len(cats))
    cur = await db.db.execute(
        f"SELECT id FROM ideas "  # noqa: S608
        f"WHERE category IN ({placeholders}) "
        f"AND status NOT IN ('archived', 'rejected') "
        f"AND ambition_score IS NOT NULL "
        f"ORDER BY ambition_score DESC, generated_at DESC LIMIT ?",
        (*cats, limit),
    )
    rows = await cur.fetchall()
    ideas = []
    for r in rows:
        idea = await db.get_idea(r["id"])
        if idea is not None:
            ideas.append(idea)
    cur = await db.db.execute(
        f"SELECT COUNT(*) FROM ideas WHERE category IN ({placeholders}) "  # noqa: S608
        f"AND status NOT IN ('archived', 'rejected')",
        cats,
    )
    total = (await cur.fetchone())[0]
    return templates.TemplateResponse(
        request,
        "claude_lab.html",
        {
            "ideas": ideas,
            "total": total,
            "categories": list(_CLAUDE_LAB_CATEGORIES),
            "category_filter": category if category in _CLAUDE_LAB_CATEGORIES else None,
        },
    )


@router.get("/api/claude-lab/top")
async def api_claude_lab_top(limit: int = Query(default=10, ge=1, le=100)):
    """JSON: top-N ambition_score-ranked ideas across the Claude / agent
    ecosystem categories."""
    placeholders = ",".join("?" * len(_CLAUDE_LAB_CATEGORIES))
    cur = await db.db.execute(
        f"SELECT id, name, tagline, category, ambition_score, "  # noqa: S608
        f"fundability_score, generation_mode, status "
        f"FROM ideas WHERE category IN ({placeholders}) "
        f"AND status NOT IN ('archived', 'rejected') "
        f"AND ambition_score IS NOT NULL "
        f"ORDER BY ambition_score DESC, generated_at DESC LIMIT ?",
        (*_CLAUDE_LAB_CATEGORIES, limit),
    )
    rows = await cur.fetchall()
    return [
        {
            "id": r["id"],
            "name": r["name"],
            "tagline": r["tagline"],
            "category": r["category"],
            "ambition_score": r["ambition_score"],
            "fundability_score": r["fundability_score"],
            "generation_mode": r["generation_mode"],
            "status": r["status"],
        }
        for r in rows
    ]


@router.get("/sniper", response_class=HTMLResponse)
async def sniper(
    request: Request,
    category: str | None = None,
    limit: int = Query(default=30, ge=1, le=100),
):
    """Competitive-displacement ideas — each wedges into a market-proven
    incumbent's demand — sorted by snipe_score DESC. Filters on
    snipe_score IS NOT NULL (not category), so any snipe surfaces here
    regardless of which domain its incumbent lives in. Optional category
    filter narrows to one of the SNIPER_CATEGORIES hunting grounds."""
    cat_filter = category if category in _SNIPER_CATEGORIES else None
    if cat_filter:
        where = "category = ? AND snipe_score IS NOT NULL"
        params: tuple = (cat_filter,)
    else:
        where = "snipe_score IS NOT NULL"
        params = ()
    cur = await db.db.execute(
        f"SELECT id FROM ideas "  # noqa: S608
        f"WHERE {where} "
        f"AND status NOT IN ('archived', 'rejected') "
        f"ORDER BY snipe_score DESC, generated_at DESC LIMIT ?",
        (*params, limit),
    )
    rows = await cur.fetchall()
    ideas = []
    for r in rows:
        idea = await db.get_idea(r["id"])
        if idea is not None:
            ideas.append(idea)
    cur = await db.db.execute(
        f"SELECT COUNT(*) FROM ideas WHERE {where} "  # noqa: S608
        f"AND status NOT IN ('archived', 'rejected')",
        params,
    )
    total = (await cur.fetchone())[0]
    return templates.TemplateResponse(
        request,
        "sniper.html",
        {
            "ideas": ideas,
            "total": total,
            "categories": list(_SNIPER_CATEGORIES),
            "category_filter": cat_filter,
        },
    )


@router.get("/api/sniper/top")
async def api_sniper_top(limit: int = Query(default=10, ge=1, le=100)):
    """JSON: top-N snipe_score-ranked competitive-displacement ideas."""
    cur = await db.db.execute(
        "SELECT id, name, tagline, category, snipe_score, target_incumbent, "
        "artifact_type, generation_mode, status "
        "FROM ideas WHERE snipe_score IS NOT NULL "
        "AND status NOT IN ('archived', 'rejected') "
        "ORDER BY snipe_score DESC, generated_at DESC LIMIT ?",
        (limit,),
    )
    rows = await cur.fetchall()
    return [
        {
            "id": r["id"],
            "name": r["name"],
            "tagline": r["tagline"],
            "category": r["category"],
            "snipe_score": r["snipe_score"],
            "target_incumbent": r["target_incumbent"],
            "angle": r["artifact_type"],
            "generation_mode": r["generation_mode"],
            "status": r["status"],
        }
        for r in rows
    ]


@router.get("/scoreboard", response_class=HTMLResponse)
async def scoreboard_page(request: Request):
    """The autonomous LEARN loop: predicted-vs-realized calibration across the
    scoring axes, plus the recommendations the engine surfaces for itself."""
    from project_forge.engine.scoreboard import build_calibration, read_signals

    cal = await build_calibration(db)
    signals = await read_signals(db)
    return templates.TemplateResponse(
        request,
        "scoreboard.html",
        {"cal": cal, "signals": signals[:50], "signal_count": len(signals)},
    )


@router.get("/api/scoreboard")
async def api_scoreboard():
    """JSON: the calibration report (axes + categories + recommendations)."""
    from project_forge.engine.scoreboard import build_calibration

    return await build_calibration(db)


# === v0.17 autonomous avenues: Labs hub + Foundry / Pulse / Cartographer /
# Kill board / Launchpad / Recruiter ===


@router.get("/labs", response_class=HTMLResponse)
async def labs(request: Request):
    """Hub for the engine's autonomous thinking/doing avenues."""
    return templates.TemplateResponse(request, "labs.html", {})


@router.get("/foundry", response_class=HTMLResponse)
async def foundry_page(request: Request):
    """The Foundry — turn a top idea into a ready-to-create starter repo.
    The plan itself loads via JS so the page stays fast (no LLM on GET)."""
    cur = await db.db.execute(
        "SELECT id FROM ideas WHERE status NOT IN ('archived', 'rejected') ORDER BY feasibility_score DESC LIMIT 30"
    )
    rows = await cur.fetchall()
    ideas = []
    for r in rows:
        idea = await db.get_idea(r["id"])
        if idea is not None:
            ideas.append(idea)
    return templates.TemplateResponse(
        request,
        "foundry.html",
        {"ideas": ideas, "total": len(ideas), "plan": None, "selected_idea": None},
    )


@router.post("/api/foundry/plan/{idea_id}")
async def api_foundry_plan(idea_id: str):
    from project_forge.engine.foundry import build_scaffold_plan

    idea = await db.get_idea(idea_id)
    if not idea:
        raise HTTPException(status_code=404, detail="Idea not found")
    return {"plan": build_scaffold_plan(idea), "idea_id": idea_id}


@router.post("/api/foundry/create/{idea_id}")
async def api_foundry_create(idea_id: str, request: Request):
    """Human-gated: turn the Foundry plan into a REAL GitHub repo. Reuses the
    proven scaffold flow (template skeleton + push) and additionally files the
    Foundry plan's LLM-tailored starter issues. Closes the think -> build loop.
    """
    import logging
    import tempfile
    from pathlib import Path

    from project_forge.config import settings
    from project_forge.engine.foundry import build_scaffold_plan
    from project_forge.scaffold.builder import build_scaffold_spec, render_scaffold
    from project_forge.scaffold.github import create_issue, create_repo, push_initial_commit

    logger = logging.getLogger(__name__)
    client_ip = request.client.host if request.client else "unknown"
    _check_rate_limit(f"foundry-create:{client_ip}")

    idea = await db.get_idea(idea_id)
    if not idea:
        raise HTTPException(status_code=404, detail="Idea not found")
    if idea.status not in ("new", "approved"):
        raise HTTPException(status_code=400, detail=f"Cannot build idea with status: {idea.status}")

    owner = settings.github_owner
    try:
        plan = build_scaffold_plan(idea)
        spec = build_scaffold_spec(idea)
        with tempfile.TemporaryDirectory() as tmpdir:
            project_dir = render_scaffold(spec, idea, Path(tmpdir), owner=owner)
            repo_url = create_repo(spec.repo_name, idea.tagline[:200], public=True, owner=owner)
            push_initial_commit(str(project_dir), repo_url)
            full_repo = f"{owner}/{spec.repo_name}"
            issues_filed = 0
            for issue in plan.get("starter_issues") or []:
                try:
                    create_issue(full_repo, issue.get("title", "Task"), issue.get("body", ""))
                    issues_filed += 1
                except RuntimeError:
                    logger.warning("foundry: issue create failed: %s", issue.get("title"))
        await db.update_idea_urls(idea_id, project_repo_url=repo_url)
        await db.update_idea_status(idea_id, "scaffolded")
        logger.info("Foundry built %s -> %s (%d issues)", idea.name, repo_url, issues_filed)
        return {"status": "scaffolded", "id": idea_id, "repo_url": repo_url, "issues_filed": issues_filed}
    except HTTPException:
        raise
    except Exception as e:
        logger.error("Foundry create failed for %s: %s", idea_id, e)
        raise HTTPException(status_code=500, detail="Repo creation failed. Check server logs.") from e


@router.get("/pulse", response_class=HTMLResponse)
async def pulse_page(request: Request):
    """The Pulse — what just changed in the world, so generation can react."""
    from project_forge.feeds.pulse import fetch_pulse_signals, pick_hot_signal

    signals = fetch_pulse_signals()
    return templates.TemplateResponse(
        request,
        "pulse.html",
        {"signals": signals, "hot_signal": pick_hot_signal(signals), "total": len(signals)},
    )


@router.post("/api/pulse/churn")
async def api_pulse_churn():
    """Generate a fresh idea anchored to the hottest real-world signal."""
    import random as _random

    from project_forge.engine.dedup import filter_and_save
    from project_forge.engine.fundability import score_fundability
    from project_forge.engine.llm_generator import generate_idea_llm
    from project_forge.feeds.pulse import (
        fetch_pulse_signals,
        pick_hot_signal,
        signal_to_seed,
    )

    signals = fetch_pulse_signals()
    hot = pick_hot_signal(signals)
    seed = signal_to_seed(hot) if hot else None
    category = IdeaCategory(_random.choice(_MONEY_CATEGORIES))
    result = await generate_idea_llm(db, category, mode="novel", seed=seed)
    if result is None:
        return {"idea": None, "message": "LLM backend returned no idea; try again.", "seed": seed}
    result.idea.fundability_score = await score_fundability(result.idea)
    _saved, ok, reason = await filter_and_save(result.idea, db)
    if not ok:
        return {"idea": None, "message": f"dedup rejected: {reason}", "seed": seed}
    return {
        "idea": {
            "id": result.idea.id,
            "name": result.idea.name,
            "tagline": result.idea.tagline,
            "category": result.idea.category.value,
            "fundability_score": result.idea.fundability_score,
        },
        "seed": seed,
        "hot_signal": hot,
    }


@router.get("/cartographer", response_class=HTMLResponse)
async def cartographer_page(request: Request):
    """The Cartographer — white-space + saturation map over the whole corpus."""
    from project_forge.engine.cartographer import build_atlas, format_memo

    atlas = await build_atlas(db)
    return templates.TemplateResponse(
        request,
        "cartographer.html",
        {
            "atlas": atlas,
            "memo": format_memo(atlas),
            "white_space_threshold": 5,
            "saturation_count_threshold": 20,
            "saturation_rate_pct": 70,
        },
    )


@router.post("/cartographer/refresh")
async def cartographer_refresh():
    from fastapi.responses import RedirectResponse

    return RedirectResponse(url="/cartographer", status_code=303)


@router.get("/killboard", response_class=HTMLResponse)
async def killboard_page(request: Request):
    """The Kill Board — ideas ranked by survival odds (most likely to die first).
    Heuristic survival on load (cheap); full case-against on demand."""
    from project_forge.engine.premortem import score_survival_heuristic

    cur = await db.db.execute(
        "SELECT id FROM ideas WHERE status NOT IN ('archived', 'rejected') ORDER BY generated_at DESC LIMIT 60"
    )
    rows = await cur.fetchall()
    entries = []
    for r in rows:
        idea = await db.get_idea(r["id"])
        if idea is None:
            continue
        entries.append({"idea": idea, "premortem": {"survival_odds": score_survival_heuristic(idea)}})
    entries.sort(key=lambda e: e["premortem"]["survival_odds"])
    return templates.TemplateResponse(
        request,
        "killboard.html",
        {"ideas": entries[:30], "total": len(entries)},
    )


@router.post("/api/premortem/{idea_id}")
async def api_premortem(idea_id: str):
    from project_forge.engine.premortem import generate_premortem

    idea = await db.get_idea(idea_id)
    if not idea:
        raise HTTPException(status_code=404, detail="Idea not found")
    return await generate_premortem(idea)


@router.post("/api/launchpad/{idea_id}")
async def api_launchpad(idea_id: str):
    from project_forge.engine.launchpad import generate_gtm_brief

    idea = await db.get_idea(idea_id)
    if not idea:
        raise HTTPException(status_code=404, detail="Idea not found")
    return generate_gtm_brief(idea)


@router.post("/api/recruiter/{idea_id}")
async def api_recruiter(idea_id: str):
    from project_forge.engine.recruiter import estimate_build

    idea = await db.get_idea(idea_id)
    if not idea:
        raise HTTPException(status_code=404, detail="Idea not found")
    return estimate_build(idea)


@router.get("/money-bots", response_class=HTMLResponse)
async def money_bots(
    request: Request,
    category: str | None = None,
    limit: int = Query(default=30, ge=1, le=100),
):
    """Top monetizable ideas across money-friendly categories, sorted by
    fundability_score DESC. Optionally filter by a specific category."""
    cats = (category,) if category in _MONEY_CATEGORIES else _MONEY_CATEGORIES
    placeholders = ",".join("?" * len(cats))
    cur = await db.db.execute(
        f"SELECT id FROM ideas "  # noqa: S608
        f"WHERE category IN ({placeholders}) "
        f"AND status NOT IN ('archived', 'rejected') "
        f"AND fundability_score IS NOT NULL "
        f"ORDER BY fundability_score DESC, generated_at DESC LIMIT ?",
        (*cats, limit),
    )
    rows = await cur.fetchall()
    ideas = []
    for r in rows:
        idea = await db.get_idea(r["id"])
        if idea is not None:
            ideas.append(idea)
    # Total in scope (no fundability_score filter — show the headline).
    cur = await db.db.execute(
        f"SELECT COUNT(*) FROM ideas WHERE category IN ({placeholders}) "  # noqa: S608
        f"AND status NOT IN ('archived', 'rejected')",
        cats,
    )
    total = (await cur.fetchone())[0]
    return templates.TemplateResponse(
        request,
        "money_bots.html",
        {
            "ideas": ideas,
            "total": total,
            "categories": list(_MONEY_CATEGORIES),
            "category_filter": category if category in _MONEY_CATEGORIES else None,
        },
    )


@router.post("/api/promote/{idea_id}")
async def api_promote(idea_id: str):
    """Manually promote a single idea — files a GitHub issue with the full
    MVP spec, flips status to 'approved', stamps auto_promoted_at.

    Replaces the v0.14 auto_promote cadence (removed because uvicorn
    reloads re-fired it). Always human-initiated now: nothing autonomous
    creates GitHub issues anymore.

    Idempotent: re-promoting an already-promoted idea returns the existing
    issue URL without creating a new one.
    """
    from datetime import UTC as _UTC
    from datetime import datetime as _dt

    from project_forge.cron.auto_promote_runner import (
        _create_promotion_issue,
        build_issue_body,  # noqa: F401  — kept for tests / introspection
    )

    idea = await db.get_idea(idea_id)
    if not idea:
        raise HTTPException(status_code=404, detail="Idea not found")
    if idea.auto_promoted_at is not None:
        return {
            "promoted": False,
            "already_promoted": True,
            "idea_id": idea.id,
            "issue_url": idea.github_issue_url,
        }

    try:
        issue_url = _create_promotion_issue(idea)
    except Exception as exc:
        logger.exception("manual promote: issue creation failed for %s", idea_id)
        raise HTTPException(
            status_code=502,
            detail=f"GitHub issue creation failed: {exc}",
        ) from exc

    idea.status = "approved"
    idea.github_issue_url = issue_url
    idea.auto_promoted_at = _dt.now(_UTC)
    await db.save_idea(idea)
    return {
        "promoted": True,
        "idea_id": idea.id,
        "name": idea.name,
        "issue_url": issue_url,
        "fundability_score": idea.fundability_score,
    }


@router.get("/api/backend-info")
async def api_backend_info():
    """Diagnostic: which LLM backend the engine is actually using, and
    whether the user's API-key env vars are visible to the running
    uvicorn process. Returns a CENSORED view — never the raw key, just
    presence + first-7-char prefix.

    User asked 2026-06-08 "how is that coming and shouldn the money bot
    have made API hits? Or we using Claude code?" — this is the answer
    surface so they can self-check without grep'ing logs.
    """
    import os as _os

    from project_forge.config import settings as _settings
    from project_forge.engine.llm_backend import (
        _has_claude_cli,
        resolve_backend,
        resolve_cheap_backend,
    )

    def _maskprefix(v: str) -> str | None:
        if not v:
            return None
        return v[:7] + "…"

    env_view = {
        "ANTHROPIC_API_KEY": _maskprefix(_os.environ.get("ANTHROPIC_API_KEY", "")),
        "FORGE_ANTHROPIC_API_KEY": _maskprefix(_os.environ.get("FORGE_ANTHROPIC_API_KEY", "")),
        "FORGE_HAIKU_API_KEY": _maskprefix(_os.environ.get("FORGE_HAIKU_API_KEY", "")),
        "FORGE_LLM_BACKEND": _os.environ.get("FORGE_LLM_BACKEND", ""),
        "FORGE_LLM_MODEL": _os.environ.get("FORGE_LLM_MODEL", ""),
        "settings.anthropic_api_key": _maskprefix(_settings.anthropic_api_key),
    }

    default_b = resolve_backend()
    cheap_b = resolve_cheap_backend()
    return {
        "claude_cli_on_path": _has_claude_cli(),
        "default_backend": default_b.name if default_b else None,
        "cheap_backend": cheap_b.name if cheap_b else None,
        "env_visible_to_process": env_view,
        "note": (
            "If cheap_backend starts with 'claude-code:' the calls run "
            "through your Claude Code (Pro Max) subscription — no API "
            "token spend. If it starts with 'anthropic-api:' you're "
            "spending API credits."
        ),
    }


@router.post("/api/churn")
async def api_churn(request: Request):
    """On-demand idea generation. Fires the LLM-first generator once
    for the given (or auto-picked) category, runs dedup + scoring,
    returns the new idea (or a reason if it couldn't land).

    Powers the Churn Now button on /money-bots, /claude-lab AND /sniper.
    The `lab` param picks which family + scoring axis applies:
      lab=money  (default) → fundability scored, money categories
      lab=claude            → ambition scored, claude-lab categories
      lab=snipe             → snipe scored, grounded incumbent wedge

    Cheap — ~1 generation Haiku call + ~1 scoring call (~$0.003 total).
    Snipe also makes a couple of keyless HTTP calls for live incumbent
    intel (cached per incumbent)."""
    import random as _random

    from project_forge.engine.ambition import score_ambition
    from project_forge.engine.dedup import filter_and_save
    from project_forge.engine.fundability import score_fundability
    from project_forge.engine.llm_generator import (
        GENERATION_MODES,
        generate_idea_llm,
        generate_snipe_llm,
        pick_least_used_mode,
    )
    from project_forge.engine.snipe import score_snipe

    try:
        payload = await request.json()
    except Exception:
        payload = {}

    lab = (payload.get("lab") or "money").strip().lower()
    allowed = {
        "claude": _CLAUDE_LAB_CATEGORIES,
        "snipe": _SNIPER_CATEGORIES,
        "crypto": _CRYPTO_CATEGORIES,
        "cashflow": _CASHFLOW_CATEGORIES,
    }.get(lab, _MONEY_CATEGORIES)

    cat_str = (payload.get("category") or "").strip()
    if cat_str in allowed:
        category = IdeaCategory(cat_str)
    else:
        # v0.21 (#97): auto-pick is inverse-density weighted — a crowded
        # category stops out-drawing the board's white space.
        from project_forge.engine.saturation import pick_weighted_category

        category = await pick_weighted_category(db, [IdeaCategory(c) for c in allowed], rng=_random)

    # Generate. Snipe has its own grounded path; the others share one.
    if lab == "snipe":
        result = await generate_snipe_llm(db, category)
    else:
        mode = payload.get("mode") or await pick_least_used_mode(db, category)
        if mode not in GENERATION_MODES:
            mode = "novel"
        result = await generate_idea_llm(db, category, mode=mode)

    if result is None:
        return {
            "idea": None,
            "message": "LLM backend returned no parseable idea; try again.",
        }

    # Score for the right axis.
    if lab == "claude":
        result.idea.ambition_score = await score_ambition(result.idea)
    elif lab == "snipe":
        result.idea.snipe_score = await score_snipe(result.idea)
    elif lab == "cashflow":
        from project_forge.engine.cashflow import score_cashflow

        result.idea.cashflow_score = await score_cashflow(result.idea)
    else:
        result.idea.fundability_score = await score_fundability(result.idea)

    _saved, ok, reason = await filter_and_save(result.idea, db)
    if not ok:
        return {
            "idea": None,
            "message": f"dedup rejected: {reason}",
            "rejected_name": result.idea.name,
        }
    return {
        "idea": {
            "id": result.idea.id,
            "name": result.idea.name,
            "tagline": result.idea.tagline,
            "category": result.idea.category.value,
            "fundability_score": result.idea.fundability_score,
            "ambition_score": result.idea.ambition_score,
            "snipe_score": result.idea.snipe_score,
            "cashflow_score": result.idea.cashflow_score,
            "target_incumbent": result.idea.target_incumbent,
            "generation_mode": result.mode,
            "artifact_type": result.artifact_type,
            "persona": result.persona,
        },
    }


@router.get("/api/money-bots/top")
async def api_money_bots_top(limit: int = Query(default=10, ge=1, le=100)):
    """JSON: top-N money-bot ideas across money categories by fundability_score."""
    placeholders = ",".join("?" * len(_MONEY_CATEGORIES))
    cur = await db.db.execute(
        f"SELECT id, name, tagline, category, fundability_score, "  # noqa: S608
        f"generation_mode, status, github_issue_url, auto_promoted_at "
        f"FROM ideas WHERE category IN ({placeholders}) "
        f"AND status NOT IN ('archived', 'rejected') "
        f"AND fundability_score IS NOT NULL "
        f"ORDER BY fundability_score DESC, generated_at DESC LIMIT ?",
        (*_MONEY_CATEGORIES, limit),
    )
    rows = await cur.fetchall()
    return [
        {
            "id": r["id"],
            "name": r["name"],
            "tagline": r["tagline"],
            "category": r["category"],
            "fundability_score": r["fundability_score"],
            "generation_mode": r["generation_mode"],
            "status": r["status"],
            "github_issue_url": r["github_issue_url"],
            "auto_promoted_at": r["auto_promoted_at"],
        }
        for r in rows
    ]


@router.get("/cashflow", response_class=HTMLResponse)
async def cashflow(
    request: Request,
    category: str | None = None,
    limit: int = Query(default=30, ge=1, le=100),
):
    """Folding-cash ideas — capital-light systems with the shortest path
    to actual dollars — sorted by cashflow_score DESC. Where /money-bots
    ranks by fundability (can we sell it as a product), /cashflow ranks by
    time-to-first-dollar. Optionally filter by a cashflow category."""
    cats = (category,) if category in _CASHFLOW_CATEGORIES else _CASHFLOW_CATEGORIES
    placeholders = ",".join("?" * len(cats))
    cur = await db.db.execute(
        f"SELECT id FROM ideas "  # noqa: S608
        f"WHERE category IN ({placeholders}) "
        f"AND status NOT IN ('archived', 'rejected') "
        f"AND cashflow_score IS NOT NULL "
        f"ORDER BY cashflow_score DESC, generated_at DESC LIMIT ?",
        (*cats, limit),
    )
    rows = await cur.fetchall()
    ideas = []
    for r in rows:
        idea = await db.get_idea(r["id"])
        if idea is not None:
            ideas.append(idea)
    cur = await db.db.execute(
        f"SELECT COUNT(*) FROM ideas WHERE category IN ({placeholders}) "  # noqa: S608
        f"AND status NOT IN ('archived', 'rejected')",
        cats,
    )
    total = (await cur.fetchone())[0]
    return templates.TemplateResponse(
        request,
        "cashflow.html",
        {
            "ideas": ideas,
            "total": total,
            "categories": list(_CASHFLOW_CATEGORIES),
            "category_filter": category if category in _CASHFLOW_CATEGORIES else None,
        },
    )


@router.get("/api/cashflow/top")
async def api_cashflow_top(limit: int = Query(default=10, ge=1, le=100)):
    """JSON: top-N time-to-first-dollar-ranked folding-cash ideas."""
    placeholders = ",".join("?" * len(_CASHFLOW_CATEGORIES))
    cur = await db.db.execute(
        f"SELECT id, name, tagline, category, cashflow_score, fundability_score, "  # noqa: S608
        f"generation_mode, status, github_issue_url, auto_promoted_at "
        f"FROM ideas WHERE category IN ({placeholders}) "
        f"AND status NOT IN ('archived', 'rejected') "
        f"AND cashflow_score IS NOT NULL "
        f"ORDER BY cashflow_score DESC, generated_at DESC LIMIT ?",
        (*_CASHFLOW_CATEGORIES, limit),
    )
    rows = await cur.fetchall()
    return [
        {
            "id": r["id"],
            "name": r["name"],
            "tagline": r["tagline"],
            "category": r["category"],
            "cashflow_score": r["cashflow_score"],
            "fundability_score": r["fundability_score"],
            "generation_mode": r["generation_mode"],
            "status": r["status"],
            "github_issue_url": r["github_issue_url"],
            "auto_promoted_at": r["auto_promoted_at"],
        }
        for r in rows
    ]


@router.get("/crypto", response_class=HTMLResponse)
async def crypto(
    request: Request,
    category: str | None = None,
    limit: int = Query(default=30, ge=1, le=100),
):
    """Fundable crypto/web3 ideas across on-chain infra, security, DeFi
    tooling, stablecoin payments, and compliance — sorted by
    fundability_score DESC. Same shape as /money-bots (reuses that axis);
    a separate board so the on-chain money map stays distinct from the
    general money-bots. Optionally filter by a specific crypto category."""
    cats = (category,) if category in _CRYPTO_CATEGORIES else _CRYPTO_CATEGORIES
    placeholders = ",".join("?" * len(cats))
    cur = await db.db.execute(
        f"SELECT id FROM ideas "  # noqa: S608
        f"WHERE category IN ({placeholders}) "
        f"AND status NOT IN ('archived', 'rejected') "
        f"AND fundability_score IS NOT NULL "
        f"ORDER BY fundability_score DESC, generated_at DESC LIMIT ?",
        (*cats, limit),
    )
    rows = await cur.fetchall()
    ideas = []
    for r in rows:
        idea = await db.get_idea(r["id"])
        if idea is not None:
            ideas.append(idea)
    cur = await db.db.execute(
        f"SELECT COUNT(*) FROM ideas WHERE category IN ({placeholders}) "  # noqa: S608
        f"AND status NOT IN ('archived', 'rejected')",
        cats,
    )
    total = (await cur.fetchone())[0]
    return templates.TemplateResponse(
        request,
        "crypto.html",
        {
            "ideas": ideas,
            "total": total,
            "categories": list(_CRYPTO_CATEGORIES),
            "category_filter": category if category in _CRYPTO_CATEGORIES else None,
        },
    )


@router.get("/api/crypto/top")
async def api_crypto_top(limit: int = Query(default=10, ge=1, le=100)):
    """JSON: top-N fundability-ranked crypto/web3 ideas."""
    placeholders = ",".join("?" * len(_CRYPTO_CATEGORIES))
    cur = await db.db.execute(
        f"SELECT id, name, tagline, category, fundability_score, "  # noqa: S608
        f"generation_mode, status, github_issue_url, auto_promoted_at "
        f"FROM ideas WHERE category IN ({placeholders}) "
        f"AND status NOT IN ('archived', 'rejected') "
        f"AND fundability_score IS NOT NULL "
        f"ORDER BY fundability_score DESC, generated_at DESC LIMIT ?",
        (*_CRYPTO_CATEGORIES, limit),
    )
    rows = await cur.fetchall()
    return [
        {
            "id": r["id"],
            "name": r["name"],
            "tagline": r["tagline"],
            "category": r["category"],
            "fundability_score": r["fundability_score"],
            "generation_mode": r["generation_mode"],
            "status": r["status"],
            "github_issue_url": r["github_issue_url"],
            "auto_promoted_at": r["auto_promoted_at"],
        }
        for r in rows
    ]


@router.get("/ideas", response_class=HTMLResponse)
async def ideas_list(
    request: Request,
    status: str | None = None,
    category: str | None = None,
    page: int = Query(default=1, ge=1),
):
    return await explore(request, category=category, status=status, page=page)


@router.get("/ideas/{idea_id}", response_class=HTMLResponse)
async def idea_detail(request: Request, idea_id: str):
    idea = await db.get_idea(idea_id)
    if not idea:
        raise HTTPException(status_code=404, detail="Idea not found")
    # Get related ideas (same category)
    related = await db.list_ideas(category=idea.category, limit=4)
    related = [r for r in related if r.id != idea.id][:3]
    challenges = await db.list_challenges(idea_id)
    return templates.TemplateResponse(
        request,
        "idea_detail.html",
        {"idea": idea, "related": related, "challenges": challenges, "score_summary": score_summary},
    )


def _promote_to_ci_queue(idea) -> str:
    """Create a GitHub issue with ci-queue label for a self-improvement idea.

    Returns the issue URL. Raises RuntimeError on GH failure.
    """
    repo = f"{settings.github_owner}/{settings.github_repo}"
    body = (
        f"## {idea.tagline}\n\n"
        f"{idea.description}\n\n"
        f"**Feasibility:** {idea.feasibility_score:.2f}\n"
        f"**MVP Scope:** {idea.mvp_scope}"
    )
    return create_issue(repo, f"[Think Tank] {idea.name}", body, labels=["ci-queue"])


@router.post("/ideas/{idea_id}/approve")
async def approve_idea(idea_id: str, request: Request):
    client_ip = request.client.host if request.client else "unknown"
    _check_rate_limit(f"approve:{client_ip}")
    idea = await db.get_idea(idea_id)
    if not idea:
        raise HTTPException(status_code=404, detail="Idea not found")

    # Self-improvement ideas auto-promote to a GitHub issue with ci-queue label
    if idea.category == IdeaCategory.SELF_IMPROVEMENT:
        # Idempotency: skip if already promoted
        if idea.github_issue_url:
            return {"status": "approved", "id": idea_id, "issue_url": idea.github_issue_url}
        try:
            issue_url = _promote_to_ci_queue(idea)
        except RuntimeError as exc:
            logger.error("GitHub issue creation failed for %s: %s", idea_id, exc)
            raise HTTPException(status_code=502, detail="GitHub issue creation failed. Check server logs.") from exc
        await db.update_idea_urls(idea_id, github_issue_url=issue_url)
        await db.update_idea_status(idea_id, "approved")
        check_verdict = await _run_approval_check(idea)
        return {"status": "approved", "id": idea_id, "issue_url": issue_url, "check": check_verdict}

    await db.update_idea_status(idea_id, "approved")
    check_verdict = await _run_approval_check(idea)
    return {"status": "approved", "id": idea_id, "check": check_verdict}


async def _run_approval_check(idea) -> str:
    """Run the think-tank sanity check on an approved idea, persist the
    result, and return the top-level verdict. Non-blocking: any check
    failure is logged but does NOT revert the approval — the dashboard
    surfaces the warning so a human can act on it."""
    from project_forge.engine.approval_check import save_approval_check, validate_idea

    try:
        result = validate_idea(idea)
        await save_approval_check(db, idea.id, result)
        if result.verdict != "pass":
            logger.warning(
                "Approval check for %s verdict=%s: %s",
                idea.id,
                result.verdict,
                "; ".join(f"{c['name']}={c['status']}" for c in result.checks if c["status"] != "pass"),
            )
        return result.verdict
    except Exception:
        logger.exception("approval check for %s failed", idea.id)
        return "error"


@router.get("/api/ideas/{idea_id}/approval-check")
async def api_get_approval_check(idea_id: str):
    """Return the latest persisted approval-check result for an idea."""
    from project_forge.engine.approval_check import get_approval_check

    check = await get_approval_check(db, idea_id)
    if check is None:
        raise HTTPException(status_code=404, detail="No approval check recorded")
    return check


@router.post("/ideas/{idea_id}/reject")
async def reject_idea(idea_id: str):
    idea = await db.update_idea_status(idea_id, "rejected")
    if not idea:
        raise HTTPException(status_code=404, detail="Idea not found")
    return {"status": "rejected", "id": idea_id}


class DenyRequest(BaseModel):
    reason: str = Field(min_length=1)
    denied_by: str | None = None


@router.post("/api/ideas/{idea_id}/deny")
async def deny_idea(idea_id: str, body: DenyRequest):
    idea = await db.get_idea(idea_id)
    if not idea:
        raise HTTPException(status_code=404, detail="Idea not found")
    denial = IdeaDenial(idea_id=idea_id, reason=body.reason, denied_by=body.denied_by)
    await db.save_denial(denial)
    return {"status": "rejected", "id": idea_id, "denial_id": denial.id}


class CreateRoundRequest(BaseModel):
    idea_ids: list[str] = Field(min_length=2)


@router.post("/api/rounds")
async def create_round(body: CreateRoundRequest):
    # Auto-determine round number
    existing = await db.list_rounds()
    round_number = len(existing) + 1
    sr = SelectionRound(round_number=round_number, idea_ids=body.idea_ids)
    await db.save_round(sr)
    return {"id": sr.id, "round_number": sr.round_number, "idea_ids": sr.idea_ids, "status": sr.status}


@router.get("/api/rounds")
async def list_rounds():
    rounds = await db.list_rounds()
    return {
        "rounds": [
            {
                "id": r.id,
                "round_number": r.round_number,
                "idea_ids": r.idea_ids,
                "status": r.status,
                "results": r.results,
            }
            for r in rounds
        ]
    }


@router.get("/api/rounds/{round_id}")
async def get_round(round_id: str):
    sr = await db.get_round(round_id)
    if not sr:
        raise HTTPException(status_code=404, detail="Round not found")
    return {
        "id": sr.id,
        "round_number": sr.round_number,
        "idea_ids": sr.idea_ids,
        "status": sr.status,
        "results": sr.results,
    }


@router.post("/api/rounds/{round_id}/compare")
async def run_round_comparisons(round_id: str):
    from itertools import combinations

    from project_forge.engine.compare import compare_ideas

    sr = await db.get_round(round_id)
    if not sr:
        raise HTTPException(status_code=404, detail="Round not found")

    # Fetch all ideas in the round
    ideas = {}
    for idea_id in sr.idea_ids:
        idea = await db.get_idea(idea_id)
        if idea:
            ideas[idea_id] = idea

    # Run pairwise comparisons
    results = []
    for id_a, id_b in combinations(ideas.keys(), 2):
        comp = compare_ideas(ideas[id_a], ideas[id_b])
        results.append(
            {
                "idea_a": id_a,
                "idea_b": id_b,
                "winner": comp["winner"],
                "overlap_score": comp["overlap_score"],
                "verdict": comp["verdict"],
                "reason": comp["reason"],
                "matching_keywords": comp["matching_keywords"],
            }
        )

    # Auto-deny losers with high overlap
    for r in results:
        if r["overlap_score"] >= 0.4 and r["verdict"] in ("similar", "duplicate"):
            loser_id = r["idea_b"] if r["winner"] == r["idea_a"] else r["idea_a"]
            loser = await db.get_idea(loser_id)
            if loser and loser.status not in ("rejected", "archived"):
                denial = IdeaDenial(
                    idea_id=loser_id,
                    reason=f"Auto-denied in round {sr.round_number} comparison: {r['verdict']} "
                    f"with '{ideas[r['winner']].name}' (overlap: {r['overlap_score']:.0%})",
                    denied_by="selection_round",
                )
                await db.save_denial(denial)

    await db.save_round_results(round_id, results)
    return {"status": "completed", "results": results}


@router.post("/ideas/{idea_id}/scaffold")
async def scaffold_idea(
    idea_id: str,
    request: Request,
    owner: str = Query(default=None),
    visibility: str = Query(default="public"),
):
    """Create a real GitHub repo from an idea."""
    import logging
    import tempfile
    from pathlib import Path

    from project_forge.config import settings
    from project_forge.scaffold.builder import build_scaffold_spec, render_scaffold
    from project_forge.scaffold.github import create_issue, create_repo, push_initial_commit

    # Fix #76 — scaffold spawns a real GitHub repo + push, much more
    # expensive than any other write path. Same rate-limit shape as
    # /approve, /promote, /report.
    client_ip = request.client.host if request.client else "unknown"
    _check_rate_limit(f"scaffold:{client_ip}")

    logger = logging.getLogger(__name__)
    idea = await db.get_idea(idea_id)
    if not idea:
        raise HTTPException(status_code=404, detail="Idea not found")
    if idea.status not in ("new", "approved"):
        raise HTTPException(status_code=400, detail=f"Cannot scaffold idea with status: {idea.status}")

    owner = owner or settings.github_owner
    is_public = visibility != "private"

    try:
        spec = build_scaffold_spec(idea)
        with tempfile.TemporaryDirectory() as tmpdir:
            project_dir = render_scaffold(spec, idea, Path(tmpdir), owner=owner)
            repo_url = create_repo(spec.repo_name, idea.tagline[:200], public=is_public, owner=owner)
            push_initial_commit(str(project_dir), repo_url)

            # Create initial issues (non-fatal if labels don't exist yet)
            full_repo = f"{owner}/{spec.repo_name}"
            for issue in spec.initial_issues:
                try:
                    create_issue(full_repo, issue["title"], issue["body"])
                except RuntimeError:
                    logger.warning("Failed to create issue: %s", issue["title"])

        await db.update_idea_urls(idea_id, project_repo_url=repo_url)
        await db.update_idea_status(idea_id, "scaffolded")
        logger.info("Scaffolded %s to %s", idea.name, repo_url)
        return {"status": "scaffolded", "id": idea_id, "repo_url": repo_url}
    except Exception as e:
        logger.error("Scaffold failed for %s: %s", idea_id, e)
        raise HTTPException(status_code=500, detail="Scaffolding failed. Check server logs.") from e


@router.get("/thinktank", response_class=HTMLResponse)
async def thinktank_page(request: Request):
    """Think Tank — Forge Lab (AI proposals) + Roadmap (GitHub issues)."""
    from datetime import UTC, datetime

    from project_forge.scaffold.github import list_self_issues

    # Roadmap: GitHub issues
    try:
        all_issues = list_self_issues()
        open_issues = [i for i in all_issues if i.get("state") == "OPEN"]
        closed_issues = [i for i in all_issues if i.get("state") == "CLOSED"]
        error = None
    except RuntimeError:
        open_issues = []
        closed_issues = []
        error = "Could not fetch issues from GitHub."

    # Forge Lab: self-improvement ideas from DB, split by status
    all_si = await db.list_ideas(category=IdeaCategory.SELF_IMPROVEMENT, limit=100)
    proposals = [i for i in all_si if i.status == "new"]
    promoted = [i for i in all_si if i.status == "approved"]
    rejected = [i for i in all_si if i.status == "rejected"]

    # Self-improvement heartbeat: every number on this page is scoped to the
    # self-improvement category to match the Forge Lab tiles below. Whole-engine
    # activity belongs on the dashboard, not here.
    si_value = IdeaCategory.SELF_IMPROVEMENT.value

    cursor = await db.db.execute(
        "SELECT generated_at FROM ideas WHERE category = ? ORDER BY generated_at DESC LIMIT 1",
        (si_value,),
    )
    row = await cursor.fetchone()
    last_proposal = row[0] if row else None

    cursor = await db.db.execute(
        "SELECT COUNT(*) FROM filtered_ideas WHERE filtered_at >= datetime('now', '-1 day') AND idea_category = ?",
        (si_value,),
    )
    row = await cursor.fetchone()
    filtered_24h = row[0] if row else 0

    cursor = await db.db.execute(
        "SELECT COUNT(*) FROM ideas WHERE generated_at >= datetime('now', '-1 day') AND category = ?",
        (si_value,),
    )
    row = await cursor.fetchone()
    accepted_24h = row[0] if row else 0

    # Recent SI activity feed: the actual events the introspect runner has
    # produced — accepted ideas + filtered attempts — interleaved by timestamp.
    # This replaces a "Last 5 runs" view that pulled from generation_runs,
    # which the introspect runner doesn't write to (it was showing 5-week-old
    # leftover entries from when SI was a regular scheduler category).
    cursor = await db.db.execute(
        "SELECT 'accepted' AS kind, name AS title, generated_at AS ts, "
        "       NULL AS reason "
        "FROM ideas WHERE category = ? "
        "UNION ALL "
        "SELECT 'filtered' AS kind, idea_name AS title, filtered_at AS ts, "
        "       filter_reason AS reason "
        "FROM filtered_ideas WHERE idea_category = ? "
        "ORDER BY ts DESC LIMIT 8",
        (si_value, si_value),
    )
    recent_events = []
    for row in await cursor.fetchall():
        recent_events.append(
            {
                "kind": row[0],
                "title": row[1],
                "when": row[2],
                "reason": (row[3] or "")[:100] if row[3] else None,
            }
        )

    heartbeat = {
        "now": datetime.now(UTC).isoformat(),
        "recent_events": recent_events,
        "last_proposal": last_proposal,
        "filtered_24h": filtered_24h,
        "accepted_24h": accepted_24h,
    }

    return templates.TemplateResponse(
        request,
        "thinktank.html",
        {
            "open_issues": open_issues,
            "closed_issues": closed_issues,
            "open_count": len(open_issues),
            "closed_count": len(closed_issues),
            "proposals": proposals,
            "proposal_count": len(proposals),
            "promoted": promoted,
            "promoted_count": len(promoted),
            "rejected": rejected,
            "rejected_count": len(rejected),
            "error": error,
            "heartbeat": heartbeat,
        },
    )


@router.get("/projects", response_class=HTMLResponse)
async def projects_list(request: Request):
    ideas = await db.list_ideas(status="scaffolded")
    return templates.TemplateResponse(
        request,
        "projects.html",
        {"projects": ideas},
    )


@router.get("/api/ideas/{idea_id}/check-repo")
async def check_idea_repo(idea_id: str) -> dict:
    """Check whether the GitHub repo associated with an idea still exists.

    Returns ``{"exists": bool, "repo": str | None}``.
    """
    idea = await db.get_idea(idea_id)
    if not idea:
        raise HTTPException(status_code=404, detail="Idea not found")

    if not idea.project_repo_url:
        return {"exists": False, "repo": None}

    repo = urlparse(idea.project_repo_url).path.lstrip("/")
    try:
        result = subprocess.run(
            ["gh", "repo", "view", repo, "--json", "name"],
            capture_output=True,
            timeout=10,
        )
        return {"exists": result.returncode == 0, "repo": repo}
    except Exception as exc:
        logger.warning("gh repo view failed for %s: %s", repo, exc)
        return {"exists": False, "repo": repo}


@router.get("/api/ideas/{idea_id}")
async def api_idea_detail(idea_id: str):
    """JSON representation of one idea, plus the bits the in-window detail
    modal needs (recent challenges, related-by-category list). Replaces
    the page navigation to /ideas/{id} so the dashboard can stay on one
    page — see the modal in app.js (handleIdeaCardClick)."""
    idea = await db.get_idea(idea_id)
    if not idea:
        raise HTTPException(status_code=404, detail="Idea not found")
    challenges = await db.list_challenges(idea_id)
    related = await db.list_ideas(category=idea.category, limit=5)
    related = [r for r in related if r.id != idea.id][:4]
    return {
        "idea": idea.model_dump(mode="json"),
        "challenges": [c.model_dump(mode="json") for c in challenges],
        "related": [
            {
                "id": r.id,
                "name": r.name,
                "tagline": r.tagline,
                "fundability_score": r.fundability_score,
                "feasibility_score": r.feasibility_score,
                "generation_mode": r.generation_mode,
            }
            for r in related
        ],
    }


@router.delete("/api/ideas/{idea_id}")
async def delete_idea(idea_id: str) -> dict:
    """Hard-delete an idea from the database.

    Blocked (409) if the idea's GitHub repository still exists on GitHub.
    """
    idea = await db.get_idea(idea_id)
    if not idea:
        raise HTTPException(status_code=404, detail="Idea not found")

    if idea.project_repo_url:
        repo = urlparse(idea.project_repo_url).path.lstrip("/")
        try:
            result = subprocess.run(
                ["gh", "repo", "view", repo, "--json", "name"],
                capture_output=True,
                timeout=10,
            )
            if result.returncode == 0:
                raise HTTPException(
                    status_code=409,
                    detail=f"Cannot delete: repository {repo} still exists on GitHub",
                )
        except HTTPException:
            raise
        except Exception as exc:
            logger.warning("gh repo view check failed for %s: %s", repo, exc)

    await db.delete_idea(idea_id)
    return {"status": "deleted", "id": idea_id}


# === API ROUTES ===


@router.post("/api/admin/reload")
async def admin_reload():
    """Trigger a watchfiles-based reload by appending a timestamp comment to __init__.py.

    Localhost-only; no Bearer token required (bypassed in BearerTokenMiddleware).
    """
    import time

    init_py = Path(__file__).parent / "__init__.py"
    with init_py.open("a") as f:
        f.write(f"# reload-{int(time.time())}\n")
    return {"status": "reloading"}


@router.get("/health")
async def health():
    db_ok = False
    try:
        if db._db:
            await db._db.execute("SELECT 1")
            db_ok = True
    except Exception:
        logger.warning("Health check DB probe failed", exc_info=True)
    return {"status": "ok" if db_ok else "degraded", "service": "project-forge", "db_ok": db_ok}


@router.get("/api/stats")
async def api_stats():
    stats = await db.get_stats()
    stats["query_stats"] = db.get_query_stats()
    return stats


@router.get("/api/top-ideas")
async def api_top_ideas(limit: int = Query(default=10, ge=1, le=50)):
    ideas = await db.list_ideas(limit=100)
    ideas.sort(key=lambda i: i.feasibility_score, reverse=True)
    return [i.model_dump() for i in ideas[:limit]]


@router.get("/api/categories")
async def api_categories():
    cat_counts = await db.count_ideas_by_category()
    cursor = await db.db.execute("SELECT category, AVG(feasibility_score) FROM ideas GROUP BY category")
    cat_avgs = {row[0]: round(row[1], 2) for row in await cursor.fetchall()}
    return [{"name": cat, "count": cat_counts.get(cat, 0), "avg_score": cat_avgs.get(cat, 0)} for cat in IdeaCategory]


@router.get("/api/ideas")
async def api_ideas(
    category: str | None = None,
    status: IdeaStatus | None = None,
    limit: int = Query(default=20, ge=1, le=100),
    offset: int = Query(default=0, ge=0),
):
    cat = IdeaCategory(category) if category else None
    ideas = await db.list_ideas(status=status, category=cat, limit=limit, offset=offset)
    total = await db.count_ideas(status=status)
    return {"ideas": [i.model_dump() for i in ideas], "total": total}


@router.get("/api/thinktank")
async def api_thinktank():
    """Think Tank API — returns Project Forge's own improvement issues and proposals."""
    from project_forge.scaffold.github import list_self_issues

    try:
        all_issues = list_self_issues()
    except RuntimeError as e:
        logger.error("Failed to list self-issues: %s", e)
        raise HTTPException(status_code=502, detail="Failed to list issues. Check server logs.") from e
    open_issues = [i for i in all_issues if i.get("state") == "OPEN"]
    closed_issues = [i for i in all_issues if i.get("state") == "CLOSED"]
    all_si = await db.list_ideas(category=IdeaCategory.SELF_IMPROVEMENT, limit=100)
    proposals = [i for i in all_si if i.status == "new"]
    promoted = [i for i in all_si if i.status == "approved"]
    rejected = [i for i in all_si if i.status == "rejected"]
    return {
        "open": open_issues,
        "closed": closed_issues,
        "open_count": len(open_issues),
        "closed_count": len(closed_issues),
        "proposals": [p.model_dump() for p in proposals],
        "proposal_count": len(proposals),
        "promoted": [p.model_dump() for p in promoted],
        "promoted_count": len(promoted),
        "rejected": [p.model_dump() for p in rejected],
        "rejected_count": len(rejected),
    }


@router.post("/api/thinktank/{idea_id}/promote")
async def promote_proposal(idea_id: str, request: Request):
    """Promote a self-improvement proposal to a GitHub issue."""
    client_ip = request.client.host if request.client else "unknown"
    _check_rate_limit(f"promote:{client_ip}")
    idea = await db.get_idea(idea_id)
    if not idea:
        raise HTTPException(status_code=404, detail="Idea not found")
    if idea.category != IdeaCategory.SELF_IMPROVEMENT:
        raise HTTPException(status_code=400, detail="Only self-improvement ideas can be promoted")

    # Idempotency: skip if already promoted
    if idea.github_issue_url:
        return {"status": "promoted", "issue_url": idea.github_issue_url}

    url = _promote_to_ci_queue(idea)
    await db.update_idea_urls(idea_id, github_issue_url=url)
    await db.update_idea_status(idea_id, "approved")
    return {"status": "promoted", "issue_url": url}


@router.post("/api/thinktank/{idea_id}/reject")
async def reject_proposal(idea_id: str):
    """Reject a self-improvement proposal."""
    idea = await db.get_idea(idea_id)
    if not idea:
        raise HTTPException(status_code=404, detail="Idea not found")
    await db.update_idea_status(idea_id, "rejected")
    return {"status": "rejected", "id": idea_id}


@router.get("/mechanic", response_class=HTMLResponse)
async def mechanic_page(request: Request):
    """Review panel (#100) — the operator's gate on the Forge Mechanic's
    autonomous self-improvement PRs. Approve = squash-merge; Reject = close.
    Nothing ships without a click here."""
    from project_forge.engine.mechanic_review import list_open_prs

    prs = list_open_prs()
    return templates.TemplateResponse(request, "mechanic.html", {"prs": prs, "total": len(prs)})


@router.get("/api/mechanic/prs")
async def api_mechanic_prs():
    """JSON: open Mechanic PRs awaiting review."""
    from project_forge.engine.mechanic_review import list_open_prs

    return {"prs": list_open_prs()}


@router.get("/api/mechanic/status")
async def api_mechanic_status():
    """Live progress of the current/last mechanic run — polled by the panel
    so a multi-minute run shows an animated, stage-by-stage status."""
    from project_forge.engine.mechanic_status import read_status

    return read_status()


@router.post("/api/mechanic/run")
async def api_mechanic_run(request: Request):
    """Human-triggered single mechanic cycle (validation / on-demand). Launches
    a detached one-shot process so the server never blocks on the agent; the
    resulting PR appears in the panel for review. Rate-limited."""
    client_ip = request.client.host if request.client else "unknown"
    _check_rate_limit(f"mechanic-run:{client_ip}")
    from project_forge.cron.mechanic_runner import spawn_mechanic_run
    from project_forge.engine.mechanic_status import read_status, write_status

    # Guard: one run at a time. A second concurrent run would double the
    # subscription spend and race on the same branch.
    status = read_status()
    if not status.get("terminal"):
        return {"status": "already_running", "detail": status.get("message", "A mechanic run is already in progress.")}

    # Write an immediate non-terminal status BEFORE spawning, so the panel's
    # first poll sees progress instead of the previous run's stale/idle state
    # (that race is what made 'Run now' look like nothing happened).
    write_status("selecting")
    spawn_mechanic_run()
    return {"status": "started"}


@router.post("/api/mechanic/prs/{number}/approve")
async def api_mechanic_approve(number: int, request: Request):
    """Human-gated: squash-merge a Mechanic PR (the ship action). This is the
    ONLY path that merges mechanic work to main — never automatic."""
    client_ip = request.client.host if request.client else "unknown"
    _check_rate_limit(f"mechanic-merge:{client_ip}")
    from project_forge.engine.mechanic_review import merge_pr

    result = merge_pr(number)
    if not result["ok"]:
        raise HTTPException(status_code=502, detail=f"merge failed: {result['detail']}")
    return {"status": "merged", "number": number}


@router.post("/api/mechanic/prs/{number}/reject")
async def api_mechanic_reject(number: int, request: Request):
    """Human-gated: close (reject) a Mechanic PR and delete its branch."""
    client_ip = request.client.host if request.client else "unknown"
    _check_rate_limit(f"mechanic-reject:{client_ip}")
    from project_forge.engine.mechanic_review import close_pr

    result = close_pr(number)
    if not result["ok"]:
        raise HTTPException(status_code=502, detail=f"close failed: {result['detail']}")
    return {"status": "rejected", "number": number}


@router.get("/thinktank/audit", response_class=HTMLResponse)
async def thinktank_audit_page(request: Request):
    """Audit page — shows implementation status of promoted ideas."""
    from project_forge.engine.audit import audit_summary, run_promoted_audit

    project_root = Path(__file__).resolve().parent.parent.parent.parent
    audits = await run_promoted_audit(db, project_root=project_root)
    summary = audit_summary(audits)

    return templates.TemplateResponse(
        request,
        "thinktank_audit.html",
        {"audits": audits, "summary": summary},
    )


@router.get("/api/thinktank/audit")
async def api_thinktank_audit():
    """JSON audit of all promoted ideas."""
    from project_forge.engine.audit import audit_summary, run_promoted_audit

    project_root = Path(__file__).resolve().parent.parent.parent.parent
    audits = await run_promoted_audit(db, project_root=project_root)
    summary = audit_summary(audits)

    return {
        "audits": [a.model_dump() for a in audits],
        "summary": summary,
    }


@router.get("/api/repos")
async def api_repos(org: str | None = None):
    """List org repos for the compare dropdown."""
    from project_forge.scaffold.github import list_org_repos

    try:
        repos = list_org_repos(org)
        return {"repos": repos}
    except RuntimeError as e:
        logger.error("Failed to list repos for %s: %s", org, e)
        raise HTTPException(status_code=502, detail="Failed to list repos. Check server logs.") from e


@router.post("/api/ideas/{idea_id}/compare")
async def compare_idea(
    idea_id: str,
    owner: str = Query(default=None),
    repo: str = Query(...),
):
    """Compare an idea against an existing GitHub repo."""
    from project_forge.engine.compare import compare_idea_to_repo
    from project_forge.scaffold.github import get_repo_details

    idea = await db.get_idea(idea_id)
    if not idea:
        raise HTTPException(status_code=404, detail="Idea not found")

    owner = owner or "rayketcham-lab"
    try:
        repo_details = get_repo_details(owner, repo)
    except RuntimeError as e:
        logger.error("Failed to fetch repo %s/%s: %s", owner, repo, e)
        raise HTTPException(status_code=502, detail="Failed to fetch repo details. Check server logs.") from e

    result = compare_idea_to_repo(idea, repo_details)
    result["repo_name"] = repo
    return result


@router.post("/api/ideas/{idea_id}/add-to-project")
async def add_idea_to_project(
    idea_id: str,
    owner: str = Query(default=None),
    repo: str = Query(...),
):
    """Add an idea as a GitHub issue on an existing project repo."""
    from project_forge.scaffold.github import create_issue, create_label

    idea = await db.get_idea(idea_id)
    if not idea:
        raise HTTPException(status_code=404, detail="Idea not found")

    owner = owner or "rayketcham-lab"
    full_repo = f"{owner}/{repo}"

    title = f"[Forge Idea] {idea.name}"
    body = (
        f"> **External Idea — Evaluate Before Acting**\n"
        f"> This issue was auto-generated by "
        f"[Project Forge](https://github.com/rayketcham-lab/project-forge), "
        f"an autonomous AI idea generator. It is **not** a vetted requirement.\n"
        f">\n"
        f"> Before implementing, verify that this idea would genuinely enhance "
        f"this project. Evaluate fit, scope overlap, and whether it aligns with "
        f"the project's roadmap. Treat this as a suggestion to critically "
        f"evaluate, not a directive.\n\n"
        f"## {idea.tagline}\n\n"
        f"{idea.description}\n\n"
        f"### Market Analysis\n{idea.market_analysis}\n\n"
        f"### MVP Scope\n{idea.mvp_scope}\n\n"
        f"### Tech Stack\n{', '.join(idea.tech_stack)}\n\n"
        f"### Feasibility Score\n{idea.feasibility_score:.0%}\n\n"
        f"---\n*Generated by [Project Forge](https://github.com/rayketcham-lab/project-forge) "
        f"from idea `{idea.id}`*"
    )
    labels = ["project-forge", idea.category.value]

    # Ensure labels exist on target repo (create_label is idempotent)
    for label in labels:
        try:
            create_label(repo=full_repo, name=label, color="6366f1", description="Auto-created by Project Forge")
        except RuntimeError:
            pass  # Label may already exist or we lack permission — proceed anyway

    try:
        issue_url = create_issue(repo=full_repo, title=title, body=body, labels=labels)
    except RuntimeError as e:
        logger.error("Failed to create issue on %s: %s", full_repo, e)
        raise HTTPException(status_code=502, detail="Failed to create issue. Check server logs.") from e

    repo_url = f"https://github.com/{full_repo}"
    await db.update_idea_status(idea_id, "contributed")
    await db.update_idea_urls(idea_id, github_issue_url=issue_url, project_repo_url=repo_url)

    return {"issue_url": issue_url, "repo": full_repo, "status": "contributed"}


@router.get("/api/search")
async def api_search(q: str = Query(min_length=1), limit: int = Query(default=20, ge=1, le=100)):
    ideas = await db.search_ideas(q, limit=limit)
    return {"ideas": [i.model_dump() for i in ideas], "total": len(ideas)}


# === MISSIONS (v0.18, #84) ===


class MissionStatusRequest(BaseModel):
    status: Literal["active", "paused", "archived"]


@router.get("/missions", response_class=HTMLResponse)
async def missions_page(request: Request, mission: str | None = None):
    """Missions — point the think tank at a target that matters to you."""
    missions = await db.list_missions()
    counts = await db.count_ideas_by_mission()
    selected = mission if any(m.id == mission for m in missions) else None
    ideas = await db.list_mission_ideas(mission_id=selected)
    return templates.TemplateResponse(
        request,
        "missions.html",
        {
            "missions": missions,
            "idea_counts": counts,
            "ideas": ideas,
            "selected_mission": selected,
            "categories": [c.value for c in IdeaCategory],
            "total": len(missions),
        },
    )


@router.get("/api/missions")
async def api_list_missions():
    missions = await db.list_missions()
    counts = await db.count_ideas_by_mission()
    out = []
    for m in missions:
        data = m.model_dump(mode="json")
        data["idea_count"] = counts.get(m.id, 0)
        out.append(data)
    return {"missions": out}


@router.post("/api/missions")
async def api_create_mission(request_body: MissionCreateRequest):
    mission = Mission(
        title=request_body.title,
        brief=request_body.brief,
        urls=request_body.urls,
        category=IdeaCategory(request_body.category) if request_body.category else None,
    )
    await db.save_mission(mission)
    return mission.model_dump(mode="json")


@router.post("/api/missions/{mission_id}/status")
async def api_mission_status(mission_id: str, request_body: MissionStatusRequest):
    mission = await db.get_mission(mission_id)
    if mission is None:
        raise HTTPException(status_code=404, detail="Unknown mission")
    await db.update_mission_status(mission_id, request_body.status)
    return {"id": mission_id, "status": request_body.status}


@router.post("/api/missions/{mission_id}/generate")
async def api_mission_generate(mission_id: str, request: Request):
    """Generate one idea anchored to the mission's brief + grounding URLs.

    LLM-cost-bearing, so it rides the same rate limiter as the ingest
    endpoints (fix #76). Resolves the engine through its module so tests
    can monkeypatch generate_mission_idea.
    """
    from project_forge.engine import mission as mission_engine

    mission = await db.get_mission(mission_id)
    if mission is None:
        raise HTTPException(status_code=404, detail="Unknown mission")
    if mission.status == "archived":
        raise HTTPException(status_code=409, detail="Mission is archived — reactivate it first")
    client_ip = request.client.host if request.client else "unknown"
    _check_rate_limit(f"mission:{client_ip}")

    result = await mission_engine.generate_mission_idea(db, mission)
    if result is None:
        return {"idea": None, "message": "LLM backend returned no idea; try again."}
    return {
        "idea": {
            "id": result.idea.id,
            "name": result.idea.name,
            "tagline": result.idea.tagline,
            "category": result.idea.category.value,
            "fundability_score": result.idea.fundability_score,
        },
        "saved": result.saved,
        "reason": result.reason,
        "mission_id": mission_id,
    }


@router.get("/api/missions/{mission_id}/ideas")
async def api_mission_ideas(mission_id: str, limit: int = Query(default=60, ge=1, le=200)):
    mission = await db.get_mission(mission_id)
    if mission is None:
        raise HTTPException(status_code=404, detail="Unknown mission")
    ideas = await db.list_mission_ideas(mission_id=mission_id, limit=limit)
    return {"ideas": [i.model_dump(mode="json") for i in ideas]}


# === URL INGESTION & RESOURCE ROUTES ===


async def ingest_idea_from_url(request_body: UrlIngestRequest):
    """Fetch URL, extract content, and generate an idea. Module-level for patching in tests."""
    from project_forge.engine.url_ingest import fetch_url_content, generate_idea_from_url

    content = await fetch_url_content(request_body.url)
    idea = await generate_idea_from_url(content, category_hint=request_body.category)
    return idea


@router.post("/api/ideas/from-url")
async def ingest_url(request_body: UrlIngestRequest, request: Request):
    """Generate a project idea from a URL."""
    # Fix #76 — LLM-backed ingest endpoints were uncapped, far more expensive
    # than the other rate-limited paths (full Claude round-trip per call).
    client_ip = request.client.host if request.client else "unknown"
    _check_rate_limit(f"ingest:{client_ip}")

    idea = await ingest_idea_from_url(request_body)
    _, accepted, reason = await filter_and_save(idea, db)
    if not accepted:
        return {"filtered": True, "reason": reason, "idea": idea.model_dump()}
    return idea.model_dump()


# Module-level for monkeypatching in tests.
async def generate_idea_from_text(text: str, category_hint: str | None = None):
    from project_forge.engine.text_ingest import generate_idea_from_text as _gen

    return await _gen(text=text, category_hint=category_hint)


@router.post("/api/ideas/from-text")
async def ingest_text(request_body: TextIngestRequest, request: Request):
    """Expand a free-form text fragment into a project idea via the LLM
    backend (or heuristic fallback when no backend is available)."""
    # Fix #76 — see ingest_url above.
    client_ip = request.client.host if request.client else "unknown"
    _check_rate_limit(f"ingest:{client_ip}")

    idea = await generate_idea_from_text(
        text=request_body.text,
        category_hint=request_body.category,
    )
    _, accepted, reason = await filter_and_save(idea, db)
    if not accepted:
        return {"filtered": True, "reason": reason, "idea": idea.model_dump()}
    return idea.model_dump()


# ── Multi-step wizard ───────────────────────────────────────────────


class _BuilderStepRequest(BaseModel):
    step: int = Field(..., ge=1, le=5)
    fragment: str
    answers: list[dict] = Field(default_factory=list)
    category: str | None = None

    @field_validator("fragment")
    @classmethod
    def _frag_not_empty(cls, v: str) -> str:
        if not (v or "").strip():
            raise ValueError("fragment cannot be empty")
        return v.strip()


@router.post("/api/ideas/builder/step")
async def builder_step(request_body: _BuilderStepRequest):
    """Run one phase of the 5-step idea builder wizard.

    Returns:
      {"questions": [...]}        — for steps 1-4 (follow-ups for the user)
      {"draft": {...}}            — for step 5 (final Idea draft, NOT yet saved)
      503 with helpful error      — when no LLM backend is available
    """
    from project_forge.engine.idea_builder import run_wizard_step

    result = run_wizard_step(
        step=request_body.step,
        fragment=request_body.fragment,
        answers=request_body.answers,
        category_hint=request_body.category,
    )
    if result is None:
        raise HTTPException(
            status_code=503,
            detail=(
                "No LLM backend available. Set ANTHROPIC_API_KEY in .env, or "
                "ensure `claude` CLI is on PATH so the wizard can call Sonnet."
            ),
        )
    return result


@router.post("/api/ideas/builder/save")
async def builder_save(payload: dict):
    """Persist a finalized wizard draft as an Idea.

    Accepts the draft fields from step 5 (after any user edits) and runs
    them through the same filter_and_save dedup gate as other ingest paths.
    """
    try:
        idea = Idea(
            name=payload["name"],
            tagline=payload["tagline"],
            description=payload["description"],
            category=IdeaCategory(payload["category"]),
            market_analysis=payload["market_analysis"],
            feasibility_score=max(0.0, min(1.0, float(payload["feasibility_score"]))),
            mvp_scope=payload["mvp_scope"],
            tech_stack=payload.get("tech_stack", []),
        )
    except (KeyError, ValueError) as exc:
        raise HTTPException(status_code=422, detail=f"Bad draft: {exc}") from exc
    _, accepted, reason = await filter_and_save(idea, db)
    if not accepted:
        return {"filtered": True, "reason": reason, "idea": idea.model_dump()}
    return idea.model_dump()


@router.get("/api/resources")
async def list_resources():
    """List all tracked source resources."""
    resources = await db.list_resources()
    return {"resources": [r.model_dump() for r in resources]}


@router.post("/api/resources")
async def add_resource(resource: Resource):
    """Add or update a source resource."""
    saved = await db.save_resource(resource)
    return saved.model_dump()


# === ISSUE REPORTER ===


class IssueReport(BaseModel):
    """User-submitted issue report from the frontend."""

    issue_type: Literal[
        "wrong_data",
        "missing_data",
        "ui_bug",
        "feature_request",
        "other",
    ] = Field(..., description="Issue category")
    description: str = Field(..., min_length=5, max_length=5000)
    page_url: str = Field("", description="Current page URL")
    page_context: str = Field("", description="Page context (e.g. idea_detail, dashboard)")
    expected_behavior: str | None = Field(None, description="What the user expected")
    severity: Literal["low", "medium", "high", "critical"] = Field("medium")
    element_info: str | None = Field(None, max_length=500, description="CSS selector of picked UI element")


_RATE_LIMIT_WINDOW = 60
_RATE_LIMIT_MAX = 5
# Evict fully-expired keys once the store grows past this. Without it the
# store leaks one dead entry for every (client-ip, action) pair ever seen —
# unbounded growth in a long-running process (a2923634 "Prune Stale
# Rate-Limit Keys").
_RATE_LIMIT_PRUNE_THRESHOLD = 1024
_rate_limit_store: dict[str, list[float]] = {}


def _prune_rate_limit_store(now: float) -> None:
    """Drop keys whose every timestamp has aged out of the window."""
    stale = [key for key, ts in _rate_limit_store.items() if all(now - t >= _RATE_LIMIT_WINDOW for t in ts)]
    for key in stale:
        del _rate_limit_store[key]


def _check_rate_limit(client_key: str) -> None:
    """Raise 429 if the client has exceeded the issue creation rate limit."""
    now = time.monotonic()
    # Opportunistically evict expired keys so the store can't grow unbounded.
    # Only sweeps when it has actually grown large, so the common path stays
    # O(1).
    if len(_rate_limit_store) > _RATE_LIMIT_PRUNE_THRESHOLD:
        _prune_rate_limit_store(now)
    timestamps = [t for t in _rate_limit_store.get(client_key, []) if now - t < _RATE_LIMIT_WINDOW]
    if len(timestamps) >= _RATE_LIMIT_MAX:
        raise HTTPException(status_code=429, detail="Rate limit exceeded. Try again in a minute.")
    timestamps.append(now)
    _rate_limit_store[client_key] = timestamps


def _fallback_issue(report: IssueReport) -> dict:
    """Create a structured GitHub issue from the report without AI."""
    type_prefixes = {
        "wrong_data": "data",
        "missing_data": "data",
        "ui_bug": "fix",
        "feature_request": "feat",
        "other": "issue",
    }
    prefix = type_prefixes.get(report.issue_type, "issue")

    title_text = report.description[:60].split("\n")[0]
    if len(report.description) > 60:
        title_text = title_text.rsplit(" ", 1)[0] + "..."
    title = f"{prefix}: {title_text}"

    body_parts = [f"## Summary\n\n{report.description}"]
    if report.page_url or report.page_context:
        body_parts.append(f"\n## Context\n\n- **Page:** {report.page_context} (`{report.page_url}`)")
    if report.element_info:
        body_parts.append(f"\n## UI Element\n\n`{report.element_info}`")
    if report.expected_behavior:
        body_parts.append(f"\n## Expected Behavior\n\n{report.expected_behavior}")
    body_parts.append(f"\n**Severity:** {report.severity}")

    label_map = {
        "wrong_data": ["bug", "data-quality"],
        "missing_data": ["enhancement"],
        "ui_bug": ["bug", "ui"],
        "feature_request": ["enhancement"],
        "other": ["bug"],
    }
    labels = label_map.get(report.issue_type, ["bug"])
    if report.severity == "critical":
        labels.append("critical")

    return {"title": title, "body": "\n".join(body_parts), "labels": labels}


async def create_gh_issue(title: str, body: str, labels: list[str]) -> str | None:
    """Create a GitHub issue using the gh CLI (subprocess_exec, not shell). Returns URL or None."""
    timestamp = datetime.now(UTC).strftime("%Y-%m-%d %H:%M UTC")
    body += f"\n\n---\n*Reported via in-app feedback on {timestamp}*"

    cmd = ["gh", "issue", "create", "--title", title, "--body", body]
    for label in labels:
        cmd.extend(["--label", label])

    try:
        # Uses create_subprocess_exec (list args, no shell) — safe from injection
        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            cwd="/opt/vmdata/project-forge",
        )
        stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=15)
        if proc.returncode == 0:
            url = stdout.decode().strip()
            logger.info("Created GitHub issue: %s", url)
            return url
        stderr_text = stderr.decode()
        logger.warning("gh issue create failed (rc=%d): %s", proc.returncode, stderr_text)
        if "label" in stderr_text.lower():
            cmd_no_labels = ["gh", "issue", "create", "--title", title, "--body", body]
            proc2 = await asyncio.create_subprocess_exec(
                *cmd_no_labels,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                cwd="/opt/vmdata/project-forge",
            )
            stdout2, _ = await asyncio.wait_for(proc2.communicate(), timeout=15)
            if proc2.returncode == 0:
                return stdout2.decode().strip()
    except Exception as exc:
        logger.error("Failed to create GitHub issue: %s", exc)
    return None


@router.post("/api/issues/report")
async def report_issue(report: IssueReport, request: Request) -> dict:
    """Accept user feedback and create a GitHub issue."""
    client_ip = request.client.host if request.client else "unknown"
    _check_rate_limit(f"report:{client_ip}")
    logger.info("Issue report received: type=%s, page=%s", report.issue_type, report.page_url)

    issue = _fallback_issue(report)
    title = issue["title"]
    body = issue["body"]
    labels = issue["labels"]

    url = await create_gh_issue(title, body, labels)
    if url:
        return {"success": True, "issue_url": url, "title": title}
    return {"success": False, "error": "Failed to create GitHub issue. Check server logs.", "title": title}


# === CHALLENGE API ===


_CHALLENGE_TYPES = {
    "feasibility": "Technical Feasibility — Can this actually be built? Are the tech choices realistic?",
    "market": "Market Viability — Is there real demand? Who would pay for this?",
    "security": "Security & Risk — What attack surfaces, compliance gaps, or trust issues exist?",
    "scope": "Scope Check — Is the MVP too big? Too small? What should be cut or added?",
    "differentiation": "Differentiation — What makes this different from existing solutions?",
    "kill": "Kill Review — Make the case for why this idea should be abandoned.",
    "freeform": "Open Question — Ask anything about this idea.",
}

_CHALLENGE_FOCUS = {
    "description": "The Problem & Solution description",
    "market_analysis": "Market Analysis",
    "mvp_scope": "MVP Scope",
    "tech_stack": "Tech Stack choices",
    "feasibility_score": "Feasibility Score",
    "all": "The entire proposal",
}

_TONE_LABELS = {
    "curious": "Curious — genuinely want to understand",
    "skeptical": "Skeptical — not convinced, show me the evidence",
    "adversarial": "Adversarial — assume this will fail and prove otherwise",
}


class ChallengeRequest(BaseModel):
    question: str = Field(..., min_length=1, max_length=2000)
    challenge_type: str = Field(default="freeform")
    focus_area: str = Field(default="all")
    tone: str = Field(default="skeptical")


def _heuristic_challenge(idea, question: str) -> dict:
    """Provide a heuristic-based challenge response when no API key is available."""
    points = []

    # Feasibility analysis
    if idea.feasibility_score < 0.5:
        points.append(
            f"The feasibility score ({idea.feasibility_score:.2f}) is below average, "
            "suggesting significant implementation challenges."
        )
    elif idea.feasibility_score >= 0.8:
        points.append(
            f"The feasibility score ({idea.feasibility_score:.2f}) is strong, "
            "indicating this idea is well within reach for an MVP."
        )

    # Description quality
    desc_words = len(idea.description.split())
    if desc_words < 30:
        points.append("The description is thin — more technical detail would strengthen the proposal.")

    # MVP scope check
    mvp_words = len(idea.mvp_scope.split())
    if mvp_words < 15:
        points.append("The MVP scope is vague. Consider defining specific deliverables.")
    elif mvp_words > 100:
        points.append("The MVP scope is broad — consider narrowing to a smaller first milestone.")

    # Tech stack analysis
    if not idea.tech_stack:
        points.append("No tech stack specified. Defining this would clarify implementation path.")
    elif len(idea.tech_stack) > 6:
        points.append(
            f"The tech stack lists {len(idea.tech_stack)} technologies — consider whether all are needed for an MVP."
        )

    if not points:
        points.append(
            f"This idea scores {idea.feasibility_score:.2f} feasibility with "
            f"a {desc_words}-word description. The fundamentals look reasonable."
        )

    verdict = "no_change"
    if idea.feasibility_score < 0.4:
        verdict = "narrow"
    elif desc_words < 30 or mvp_words < 15:
        verdict = "strengthen"

    response = " ".join(points) + " (heuristic analysis — add an API key for deeper AI review)"

    return {
        "response": response,
        "verdict": verdict,
        "confidence": 0.4,
        "changes": [],
    }


async def _challenge_idea(
    idea, question: str, challenge_type: str = "freeform", focus_area: str = "all", tone: str = "skeptical"
) -> dict:
    """Send the idea + question to an LLM via the backend resolver and
    return structured response + verdict + suggested changes.

    Routes through engine.llm_backend.resolve_backend() so it works with:
      - Anthropic API direct (when ANTHROPIC_API_KEY is set)
      - Claude Code CLI shell-out (when `claude` is on PATH)
      - Heuristic fallback (when neither — preserves the question's
        intent in the response so it's not a generic stub).

    Issue #70: previously this function only checked for ANTHROPIC_API_KEY
    and dropped to a heuristic stub on Claude Code-only hosts, leaving
    user challenges as inert DB rows.
    """
    backend = resolve_backend()
    if backend is None:
        return _heuristic_challenge(idea, question)

    type_desc = _CHALLENGE_TYPES.get(challenge_type, _CHALLENGE_TYPES["freeform"])
    focus_desc = _CHALLENGE_FOCUS.get(focus_area, _CHALLENGE_FOCUS["all"])
    tone_desc = _TONE_LABELS.get(tone, _TONE_LABELS["skeptical"])

    tone_instruction = {
        "curious": "Be thorough but constructive. Explain trade-offs. Help the idea improve.",
        "skeptical": "Be direct and evidence-based. Point out weaknesses. Demand specifics.",
        "adversarial": (
            "Be ruthless. Assume failure is the default outcome. "
            "Every claim needs proof. If this idea can't survive hard scrutiny, say so."
        ),
    }.get(tone, "Be direct and evidence-based.")

    prompt = (
        f"You are a senior technical reviewer. Respond ONLY with valid JSON.\n\n"
        f"You are reviewing a project idea proposal.\n\n"
        f"## Idea: {idea.name}\n"
        f"**Tagline:** {idea.tagline}\n"
        f"**Description:** {idea.description}\n"
        f"**Market Analysis:** {idea.market_analysis}\n"
        f"**MVP Scope:** {idea.mvp_scope}\n"
        f"**Tech Stack:** {', '.join(idea.tech_stack)}\n"
        f"**Feasibility Score:** {idea.feasibility_score}\n\n"
        f"## Challenge\n"
        f"**Type:** {type_desc}\n"
        f"**Focus Area:** {focus_desc}\n"
        f"**Tone:** {tone_desc}\n\n"
        f"**User's Question:**\n{question}\n\n"
        f"## Instructions\n"
        f"{tone_instruction}\n\n"
        f"Respond with JSON only (no markdown wrapping):\n"
        f"{{\n"
        f'  "response": "Your detailed answer to the challenge",\n'
        f'  "verdict": "strengthen|pivot|narrow|expand|kill|no_change",\n'
        f'  "confidence": 0.0 to 1.0,\n'
        f'  "changes": [\n'
        f'    {{"field": "mvp_scope|description|tech_stack|market_analysis|feasibility_score", '
        f'"action": "added|removed|modified", "text": "what changed (full replacement value, not a diff)"}}\n'
        f"  ]\n"
        f"}}\n\n"
        f"verdict meanings: strengthen=idea is solid, reinforce it; pivot=change direction; "
        f"narrow=reduce scope; expand=scope too small; kill=abandon; no_change=question answered, no changes needed.\n"
        f"changes array can be empty if no changes are warranted. Each change.text MUST be the COMPLETE new "
        f"value of the field (not a diff or a fragment), so the apply step can replace it directly."
    )

    raw = backend.call(prompt) or ""

    import json as _json

    raw = raw.strip()
    if "```json" in raw:
        raw = raw.split("```json")[1].split("```")[0].strip()
    elif "```" in raw:
        raw = raw.split("```")[1].split("```")[0].strip()

    try:
        data = _json.loads(raw)
    except _json.JSONDecodeError:
        data = {"response": raw or "(LLM returned no parseable response)", "changes": []}

    return {
        "response": data.get("response", ""),
        "verdict": data.get("verdict", "no_change"),
        "confidence": data.get("confidence", 0.5),
        "changes": data.get("changes", []),
    }


@router.get("/api/challenge-options")
async def api_challenge_options():
    """Return available challenge types, focus areas, and tones for the frontend."""
    return {
        "types": [{"id": k, "label": v} for k, v in _CHALLENGE_TYPES.items()],
        "focus_areas": [{"id": k, "label": v} for k, v in _CHALLENGE_FOCUS.items()],
        "tones": [{"id": k, "label": v} for k, v in _TONE_LABELS.items()],
    }


@router.post("/api/ideas/{idea_id}/challenge")
async def api_challenge_idea(idea_id: str, req: ChallengeRequest):
    """Submit a challenge/question against an idea. Returns AI response + tracked changes."""
    idea = await db.get_idea(idea_id)
    if not idea:
        raise HTTPException(status_code=404, detail="Idea not found")

    result = await _challenge_idea(
        idea,
        req.question,
        challenge_type=req.challenge_type,
        focus_area=req.focus_area,
        tone=req.tone,
    )

    challenge = Challenge(
        idea_id=idea_id,
        question=req.question,
        challenge_type=req.challenge_type,
        focus_area=req.focus_area,
        tone=req.tone,
        response=result["response"],
        verdict=result.get("verdict", "no_change"),
        confidence=result.get("confidence", 0.5),
        changes=result["changes"],
    )
    await db.save_challenge(challenge)

    return challenge.model_dump()


@router.post("/api/ideas/{idea_id}/challenges/{challenge_id}/apply")
async def api_apply_challenge(idea_id: str, challenge_id: str):
    """Apply a challenge's `changes` array to the idea (#70).

    Each change is `{field, action, text}` where text is the COMPLETE
    new value. Whitelisted fields only (see Database.apply_idea_field_updates).
    Idempotent: a second call returns {already_applied: True} without
    mutating again.
    """
    challenge = await db.get_challenge(challenge_id)
    if challenge is None or challenge.idea_id != idea_id:
        raise HTTPException(
            status_code=404,
            detail="Challenge not found or not associated with this idea",
        )

    if await db.is_challenge_applied(challenge_id):
        return {
            "already_applied": True,
            "challenge_id": challenge_id,
            "idea_id": idea_id,
        }

    # Convert challenge.changes (list of {field, action, text}) → dict
    # of {field: new_value}. Last entry wins on conflict.
    field_updates: dict[str, str] = {}
    for ch in challenge.changes or []:
        if not isinstance(ch, dict):
            continue
        field = ch.get("field")
        text = ch.get("text")
        if field and text is not None:
            field_updates[field] = text

    updated = await db.apply_idea_field_updates(idea_id, field_updates)
    if updated is None:
        raise HTTPException(status_code=404, detail="Idea not found")

    await db.mark_challenge_applied(challenge_id)
    return {
        "already_applied": False,
        "challenge_id": challenge_id,
        "idea_id": idea_id,
        "fields_updated": list(field_updates.keys()),
        "idea": updated.model_dump(),
    }


@router.get("/api/ideas/{idea_id}/challenges")
async def api_list_challenges(idea_id: str):
    """List all challenges for an idea, ordered by creation time."""
    challenges = await db.list_challenges(idea_id)
    return [c.model_dump() for c in challenges]


@router.post("/api/maintenance/dedup")
async def api_dedup():
    """Deduplicate existing self-improvement ideas, keeping the best per group."""
    result = await db.deduplicate_si_ideas()
    return result


@router.get("/api/issues/types")
async def get_issue_types() -> list[dict]:
    """Return available issue types for the frontend."""
    return [
        {
            "id": "wrong_data",
            "label": "Wrong Data",
            "description": "Data is incorrect or outdated",
            "color": "red",
        },
        {
            "id": "missing_data",
            "label": "Missing Data",
            "description": "Expected information is not shown",
            "color": "amber",
        },
        {
            "id": "ui_bug",
            "label": "UI / Display Bug",
            "description": "Layout broken, button not working, or visual glitch",
            "color": "blue",
        },
        {
            "id": "feature_request",
            "label": "Feature Request",
            "description": "I want something new or different",
            "color": "green",
        },
        {
            "id": "other",
            "label": "Other",
            "description": "Something else not covered above",
            "color": "gray",
        },
    ]
