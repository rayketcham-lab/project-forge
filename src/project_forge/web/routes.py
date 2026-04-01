"""API and page routes for the Project Forge dashboard."""

from fastapi import APIRouter, HTTPException, Query, Request
from fastapi.responses import HTMLResponse

from project_forge.engine.scorer import score_summary
from project_forge.models import IdeaCategory, IdeaStatus, Resource, UrlIngestRequest
from project_forge.web.app import db, templates

router = APIRouter()


# === PAGE ROUTES ===


@router.get("/", response_class=HTMLResponse)
async def dashboard(request: Request):
    stats = await db.get_stats()
    all_top = await db.list_ideas(limit=20)
    all_top.sort(key=lambda i: i.feasibility_score, reverse=True)
    top_ideas = [i for i in all_top if not i.name.startswith("[SUPER]")][:6]
    # Dedicated query for super ideas — ensures they show even if older than top 20
    super_ideas = await db.list_super_ideas(limit=6)
    # SQL-optimized category counts + avg scores (no in-memory loading)
    cat_counts = await db.count_ideas_by_category()
    cursor = await db.db.execute("SELECT category, AVG(feasibility_score) FROM ideas GROUP BY category")
    cat_avgs = {row[0]: round(row[1], 2) for row in await cursor.fetchall()}
    categories = [
        {"name": cat, "count": cat_counts.get(cat, 0), "avg_score": cat_avgs.get(cat, 0)} for cat in cat_counts
    ]
    return templates.TemplateResponse(
        request,
        "dashboard.html",
        {
            "stats": stats,
            "top_ideas": top_ideas,
            "super_ideas": super_ideas,
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
    if category:
        try:
            cat = IdeaCategory(category)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=f"Unknown category: {category!r}") from exc
    else:
        cat = None
    if q:
        ideas = await db.search_ideas(q, limit=limit, offset=offset)
        total = len(await db.search_ideas(q, limit=10000))
    else:
        ideas = await db.list_ideas(status=status, category=cat, limit=limit, offset=offset)
        total = await db.count_ideas(status=status)
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
        request,
        "idea_detail.html",
        {"idea": idea, "related": related, "score_summary": score_summary},
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


@router.get("/thinktank", response_class=HTMLResponse)
async def thinktank_page(request: Request):
    """Think Tank — Forge Lab (AI proposals) + Roadmap (GitHub issues)."""
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


@router.get("/api/thinktank")
async def api_thinktank():
    """Think Tank API — returns Project Forge's own improvement issues and proposals."""
    from project_forge.scaffold.github import list_self_issues

    try:
        all_issues = list_self_issues()
    except RuntimeError as e:
        raise HTTPException(status_code=502, detail=str(e)) from e
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
async def promote_proposal(idea_id: str):
    """Promote a self-improvement proposal to a GitHub issue."""
    from project_forge.config import settings
    from project_forge.scaffold.github import create_issue

    idea = await db.get_idea(idea_id)
    if not idea:
        raise HTTPException(status_code=404, detail="Idea not found")

    repo = f"{settings.github_owner}/{settings.github_repo}"
    body = (
        f"## {idea.tagline}\n\n"
        f"{idea.description}\n\n"
        f"**Feasibility:** {idea.feasibility_score:.2f}\n"
        f"**MVP Scope:** {idea.mvp_scope}"
    )
    url = create_issue(repo, f"[Think Tank] {idea.name}", body)
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


@router.get("/api/repos")
async def api_repos(org: str | None = None):
    """List org repos for the compare dropdown."""
    from project_forge.scaffold.github import list_org_repos

    try:
        repos = list_org_repos(org)
        return {"repos": repos}
    except RuntimeError as e:
        raise HTTPException(status_code=502, detail=str(e)) from e


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
        raise HTTPException(status_code=502, detail=f"Failed to fetch repo: {e}") from e

    result = compare_idea_to_repo(idea, repo_details)
    result["repo_name"] = repo
    return result


@router.get("/api/search")
async def api_search(q: str = Query(min_length=1), limit: int = Query(default=20, ge=1, le=100)):
    ideas = await db.search_ideas(q, limit=limit)
    return {"ideas": [i.model_dump() for i in ideas], "total": len(ideas)}


# === URL INGESTION & RESOURCE ROUTES ===


async def ingest_idea_from_url(request_body: UrlIngestRequest):
    """Fetch URL, extract content, and generate an idea. Module-level for patching in tests."""
    from project_forge.engine.url_ingest import fetch_url_content, generate_idea_from_url

    content = await fetch_url_content(request_body.url)
    idea = await generate_idea_from_url(content, category_hint=request_body.category)
    return idea


@router.post("/api/ideas/from-url")
async def ingest_url(request_body: UrlIngestRequest):
    """Generate a project idea from a URL."""
    idea = await ingest_idea_from_url(request_body)
    await db.save_idea(idea)
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
