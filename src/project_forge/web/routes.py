"""API and page routes for the Project Forge dashboard."""

import asyncio
import logging
import time
from datetime import UTC, datetime
from typing import Literal

from fastapi import APIRouter, HTTPException, Query, Request
from fastapi.responses import HTMLResponse
from pydantic import BaseModel, Field

from project_forge.config import settings
from project_forge.engine.dedup import filter_and_save
from project_forge.engine.scorer import score_summary
from project_forge.models import (
    Challenge,
    IdeaCategory,
    IdeaDenial,
    IdeaStatus,
    Resource,
    SelectionRound,
    UrlIngestRequest,
)
from project_forge.scaffold.github import create_issue
from project_forge.web.app import db, templates

logger = logging.getLogger(__name__)
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
        return {"status": "approved", "id": idea_id, "issue_url": issue_url}

    await db.update_idea_status(idea_id, "approved")
    return {"status": "approved", "id": idea_id}


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
        logger.error("Scaffold failed for %s: %s", idea_id, e)
        raise HTTPException(status_code=500, detail="Scaffolding failed. Check server logs.") from e


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


_RATE_LIMIT_WINDOW = 60
_RATE_LIMIT_MAX = 5
_rate_limit_store: dict[str, list[float]] = {}


def _check_rate_limit(client_key: str) -> None:
    """Raise 429 if the client has exceeded the issue creation rate limit."""
    now = time.monotonic()
    timestamps = _rate_limit_store.get(client_key, [])
    timestamps = [t for t in timestamps if now - t < _RATE_LIMIT_WINDOW]
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
    """Send the idea + question to Claude and return structured response with changes."""
    import anthropic

    key = settings.anthropic_api_key
    if not key:
        import os

        key = os.environ.get("ANTHROPIC_API_KEY", "")
    if not key:
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
        f'"action": "added|removed|modified", "text": "what changed"}}\n'
        f"  ]\n"
        f"}}\n\n"
        f"verdict meanings: strengthen=idea is solid, reinforce it; pivot=change direction; "
        f"narrow=reduce scope; expand=scope too small; kill=abandon; no_change=question answered, no changes needed.\n"
        f"changes array can be empty if no changes are warranted."
    )

    client = anthropic.Anthropic(api_key=key)
    resp = client.messages.create(
        model=settings.anthropic_model,
        max_tokens=4096,
        system="You are a senior technical reviewer. Respond ONLY with valid JSON.",
        messages=[{"role": "user", "content": prompt}],
    )

    import json as _json

    raw = resp.content[0].text.strip()
    if "```json" in raw:
        raw = raw.split("```json")[1].split("```")[0].strip()
    elif "```" in raw:
        raw = raw.split("```")[1].split("```")[0].strip()

    try:
        data = _json.loads(raw)
    except _json.JSONDecodeError:
        data = {"response": raw, "changes": []}

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
