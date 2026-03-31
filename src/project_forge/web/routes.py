"""API and page routes for the Project Forge dashboard."""

from fastapi import APIRouter, HTTPException, Query, Request
from fastapi.responses import HTMLResponse

from project_forge.engine.scorer import score_summary
from project_forge.models import IdeaCategory, IdeaStatus
from project_forge.web.app import db, templates

router = APIRouter()


# === PAGE ROUTES ===


@router.get("/", response_class=HTMLResponse)
async def dashboard(request: Request):
    stats = await db.get_stats()
    top_ideas = await db.list_ideas(limit=6)
    top_ideas.sort(key=lambda i: i.feasibility_score, reverse=True)
    # SQL-optimized category counts + avg scores (no in-memory loading)
    cat_counts = await db.count_ideas_by_category()
    cursor = await db.db.execute("SELECT category, AVG(feasibility_score) FROM ideas GROUP BY category")
    cat_avgs = {row[0]: round(row[1], 2) for row in await cursor.fetchall()}
    categories = [
        {"name": cat, "count": cat_counts.get(cat, 0), "avg_score": cat_avgs.get(cat, 0)} for cat in cat_counts
    ]
    return templates.TemplateResponse(
        "dashboard.html",
        {
            "request": request,
            "stats": stats,
            "top_ideas": top_ideas[:6],
            "categories": sorted(categories, key=lambda c: c["count"], reverse=True),
            "score_summary": score_summary,
        },
    )


@router.get("/explore", response_class=HTMLResponse)
async def explore(
    request: Request,
    category: str | None = None,
    status: IdeaStatus | None = None,
    q: str | None = None,
    page: int = Query(default=1, ge=1),
):
    limit = 12
    offset = (page - 1) * limit
    cat = IdeaCategory(category) if category else None
    if q:
        ideas = await db.search_ideas(q, limit=limit, offset=offset)
        total = len(await db.search_ideas(q, limit=10000))
    else:
        ideas = await db.list_ideas(status=status, category=cat, limit=limit, offset=offset)
        total = await db.count_ideas(status=status)
    return templates.TemplateResponse(
        "explore.html",
        {
            "request": request,
            "ideas": ideas,
            "total": total,
            "page": page,
            "pages": max(1, (total + limit - 1) // limit),
            "status_filter": status,
            "category_filter": category,
            "search_query": q or "",
            "categories": list(IdeaCategory),
            "score_summary": score_summary,
        },
    )


@router.get("/ideas", response_class=HTMLResponse)
async def ideas_list(
    request: Request,
    status: IdeaStatus | None = None,
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
    return templates.TemplateResponse(
        "idea_detail.html",
        {"request": request, "idea": idea, "related": related, "score_summary": score_summary},
    )


@router.post("/ideas/{idea_id}/approve")
async def approve_idea(idea_id: str):
    idea = await db.update_idea_status(idea_id, "approved")
    if not idea:
        raise HTTPException(status_code=404, detail="Idea not found")
    return {"status": "approved", "id": idea_id}


@router.post("/ideas/{idea_id}/reject")
async def reject_idea(idea_id: str):
    idea = await db.update_idea_status(idea_id, "rejected")
    if not idea:
        raise HTTPException(status_code=404, detail="Idea not found")
    return {"status": "rejected", "id": idea_id}


@router.post("/ideas/{idea_id}/scaffold")
async def scaffold_idea(
    idea_id: str,
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
        logger.error("Scaffold failed for %s: %s", idea.name, e)
        raise HTTPException(status_code=500, detail=str(e)) from e


@router.get("/projects", response_class=HTMLResponse)
async def projects_list(request: Request):
    ideas = await db.list_ideas(status="scaffolded")
    return templates.TemplateResponse(
        "projects.html",
        {"request": request, "projects": ideas},
    )


# === API ROUTES ===


@router.get("/health")
async def health():
    return {"status": "ok", "service": "project-forge"}


@router.get("/api/stats")
async def api_stats():
    return await db.get_stats()


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


@router.get("/api/search")
async def api_search(q: str = Query(min_length=1), limit: int = Query(default=20, ge=1, le=100)):
    ideas = await db.search_ideas(q, limit=limit)
    return {"ideas": [i.model_dump() for i in ideas], "total": len(ideas)}
