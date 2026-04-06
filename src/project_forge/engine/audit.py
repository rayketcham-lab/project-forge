"""Audit promoted ideas — check if they were actually implemented in the codebase."""

import logging
import re
import subprocess
from pathlib import Path

from project_forge.models import Idea, PromotedIdeaAudit

logger = logging.getLogger(__name__)

# Keywords to search for per idea name pattern.
# Maps normalized name fragments to search terms that indicate implementation.
_KEYWORD_MAP: dict[str, list[str]] = {
    "dedup": ["tagline_similarity", "content_hash", "should_accept", "filter_and_save"],
    "deduplication": ["tagline_similarity", "content_hash", "should_accept"],
    "rate limit": ["_RATE_LIMIT_WINDOW", "_RATE_LIMIT_MAX", "_check_rate_limit"],
    "security hardening": ["Content-Security-Policy", "X-Frame-Options", "CSPMiddleware"],
    "error handling": ["error_detail", "status_code=502", "HTTPException(status_code=5"],
    "structured logging": ["structlog", "json_logging", "correlation_id"],
    "observability": ["structlog", "json_logging", "correlation_id"],
    "test coverage": ["--cov", "pytest-cov", "coverage_threshold"],
    "coverage enforcement": ["--cov", "pytest-cov", "coverage_threshold"],
    "runner health": ["/health", "health_check", "db_ok"],
    "scaffold quality": ["sanitize_repo_name", "validate_scaffold", "quality_review"],
    "ci pipeline": ["issue-test-ratio", "self-improvement-queue"],
    "ci gap": ["issue-test-ratio", "gap_detection"],
    "database query": ["query_profiling", "slow_query"],
    "db profiling": ["query_profiling", "slow_query"],
    "dependency audit": ["pip-audit", "pip_audit"],
    "dashboard ux": ["hero-title", "hero-subtitle", "tab-btn"],
    "input validation": ["field_validator", "min_length", "Query(min_length"],
}

PROMOTED_STATUSES = {"approved", "promoted", "scaffolded", "contributed", "implemented"}


def _extract_issue_number(url: str | None) -> int | None:
    """Extract issue number from a GitHub issue URL."""
    if not url:
        return None
    match = re.search(r"/issues/(\d+)$", url)
    return int(match.group(1)) if match else None


def _search_codebase(term: str, project_root: Path) -> list[str]:
    """Search the src/ directory for a term, return matching file:line summaries."""
    src_dir = project_root / "src"
    if not src_dir.exists():
        return []
    try:
        result = subprocess.run(
            [
                "grep",
                "-rl",
                "--include=*.py",
                "--exclude=audit.py",
                term,
                str(src_dir),
            ],
            capture_output=True,
            text=True,
            timeout=10,
        )
        if result.returncode == 0 and result.stdout.strip():
            files = result.stdout.strip().split("\n")
            return [f.replace(str(project_root) + "/", "") for f in files[:5]]
    except (subprocess.TimeoutExpired, FileNotFoundError):
        pass
    return []


def _get_keywords_for_idea(idea: Idea) -> list[str]:
    """Derive search keywords from the idea name."""
    name_lower = idea.name.lower()
    keywords: list[str] = []
    for fragment, terms in _KEYWORD_MAP.items():
        if fragment in name_lower:
            keywords.extend(terms)
    # Fallback: extract significant words from the name itself
    if not keywords:
        stop_words = {
            "and",
            "of",
            "the",
            "for",
            "in",
            "on",
            "to",
            "a",
            "an",
            "is",
            "it",
            "hub",
            "suite",
            "test",
            "tool",
        }
        words = re.findall(r"[a-z]+", name_lower)
        keywords = [w for w in words if w not in stop_words and len(w) > 3]
    return keywords


def audit_promoted_idea(idea: Idea, project_root: Path) -> PromotedIdeaAudit:
    """Check if a promoted idea was implemented by searching the codebase."""
    issue_number = _extract_issue_number(idea.github_issue_url)
    keywords = _get_keywords_for_idea(idea)

    evidence: list[str] = []
    for keyword in keywords:
        matches = _search_codebase(keyword, project_root)
        for match in matches:
            entry = f"'{keyword}' found in {match}"
            if entry not in evidence:
                evidence.append(entry)

    # Determine status based on evidence
    if len(evidence) >= 3:
        status = "implemented"
    elif len(evidence) >= 1:
        status = "partial"
    else:
        status = "not_implemented"

    # Recommendation
    recommendation = None
    if status == "implemented" and issue_number:
        recommendation = "close_issue"

    return PromotedIdeaAudit(
        idea_id=idea.id,
        idea_name=idea.name,
        status=status,
        evidence=evidence,
        github_issue_number=issue_number,
        github_issue_state="open" if issue_number else None,
        recommendation=recommendation,
    )


async def run_promoted_audit(db, project_root: Path) -> list[PromotedIdeaAudit]:
    """Audit all promoted/approved ideas in the database."""
    promoted: list = []
    for status in PROMOTED_STATUSES:
        ideas = await db.list_ideas(status=status, limit=200)
        promoted.extend(ideas)

    results = []
    for idea in promoted:
        audit = audit_promoted_idea(idea, project_root=project_root)
        results.append(audit)

    return results


def audit_summary(audits: list[PromotedIdeaAudit]) -> dict:
    """Summarize audit results by status."""
    summary = {
        "total": len(audits),
        "implemented": 0,
        "partial": 0,
        "not_implemented": 0,
        "unknown": 0,
    }
    for a in audits:
        summary[a.status] = summary.get(a.status, 0) + 1
    return summary


def close_github_issue(repo: str, issue_number: int) -> None:
    """Close a GitHub issue via gh CLI."""
    from project_forge.scaffold.github import _run_gh

    _run_gh(["issue", "close", str(issue_number), "-R", repo, "-c", "Implemented — verified by audit."])


async def reconcile_audit_results(
    db,
    audits: list[PromotedIdeaAudit],
    close_issues: bool = False,
) -> None:
    """Update idea statuses and optionally close completed GitHub issues."""
    for audit in audits:
        if audit.status == "implemented":
            await db.update_idea_status(audit.idea_id, "implemented")
            if close_issues and audit.recommendation == "close_issue" and audit.github_issue_number:
                try:
                    close_github_issue(
                        repo="rayketcham-lab/project-forge",
                        issue_number=audit.github_issue_number,
                    )
                except RuntimeError:
                    logger.warning("Failed to close issue #%d", audit.github_issue_number)
