"""Self-introspection engine for Project Forge.

Gathers context about the project's own codebase, tests, and open issues,
then builds a prompt that asks Claude to suggest ONE self-improvement idea.
"""

import json
import logging
import subprocess
from pathlib import Path

logger = logging.getLogger(__name__)

# Root of the project relative to this file: src/project_forge/engine/ → ../../..
_PROJECT_ROOT = Path(__file__).parent.parent.parent.parent


def _run(cmd: list[str]) -> subprocess.CompletedProcess:
    """Run a subprocess command, capturing stdout."""
    return subprocess.run(
        cmd,
        capture_output=True,
        text=True,
        timeout=30,
    )


def _count_lines(directory: Path) -> int:
    """Count total lines across all .py files in a directory."""
    total = 0
    if not directory.exists():
        return total
    for path in directory.rglob("*.py"):
        try:
            total += len(path.read_text(encoding="utf-8", errors="replace").splitlines())
        except OSError:
            pass
    return total


def gather_self_context() -> dict:
    """Gather context about Project Forge's own codebase and health.

    Returns a dict with:
    - open_issues: list of open GitHub issues (title, number, labels, url)
    - recent_commits: last 10 commit messages as strings
    - test_count: number of test files matching tests/test_*.py
    - lint_status: ruff statistics summary string
    - code_stats: dict of line counts per key directory
    """
    # --- Open GitHub issues ---
    open_issues: list[dict] = []
    try:
        result = _run(
            [
                "gh",
                "issue",
                "list",
                "--state",
                "open",
                "--json",
                "title,number,labels,url",
            ]
        )
        if result.returncode == 0 and result.stdout.strip():
            open_issues = json.loads(result.stdout)
    except (FileNotFoundError, subprocess.TimeoutExpired, json.JSONDecodeError) as exc:
        logger.warning("Could not fetch GitHub issues: %s", exc)

    # --- Recent commits ---
    recent_commits: list[str] = []
    try:
        result = _run(["git", "log", "--oneline", "-10"])
        if result.returncode == 0 and result.stdout.strip():
            recent_commits = [line for line in result.stdout.splitlines() if line.strip()]
    except (FileNotFoundError, subprocess.TimeoutExpired) as exc:
        logger.warning("Could not fetch git log: %s", exc)

    # --- Test file count ---
    test_dir = _PROJECT_ROOT / "tests"
    test_count = len(list(test_dir.glob("test_*.py")))

    # --- Lint status ---
    lint_status = "unknown"
    try:
        result = _run(["ruff", "check", str(_PROJECT_ROOT / "src"), str(_PROJECT_ROOT / "tests"), "--statistics"])
        # ruff exits non-zero when violations exist; we want the output either way
        lint_status = result.stdout.strip() or result.stderr.strip() or "clean"
    except (FileNotFoundError, subprocess.TimeoutExpired) as exc:
        logger.warning("Could not run ruff: %s", exc)
        lint_status = f"ruff unavailable: {exc}"

    # --- Code stats ---
    code_stats = {
        "src": _count_lines(_PROJECT_ROOT / "src"),
        "tests": _count_lines(_PROJECT_ROOT / "tests"),
    }

    # --- File tree (key .py files) ---
    file_tree: list[str] = []
    src_dir = _PROJECT_ROOT / "src"
    if src_dir.exists():
        file_tree = sorted(
            str(p.relative_to(_PROJECT_ROOT)) for p in src_dir.rglob("*.py") if "__pycache__" not in str(p)
        )

    return {
        "open_issues": open_issues,
        "recent_commits": recent_commits,
        "test_count": test_count,
        "lint_status": lint_status,
        "code_stats": code_stats,
        "file_tree": file_tree,
    }


_INTROSPECTION_PROMPT_TEMPLATE = """\
You are analyzing the Project Forge codebase to suggest ONE targeted improvement \
to the EXISTING code in THIS repository. You are NOT proposing a new project, product, \
or tool. You are proposing a specific code change to improve project-forge itself.

CRITICAL RULES:
- This is about modifying existing code in src/project_forge/ or tests/
- Do NOT propose building new external tools, CLI apps, SaaS products, or services
- Do NOT use language like "Phase 1", "Phase 2", "ship to customers", "market demand"
- Your description MUST reference specific files in src/project_forge/ that need changing
- Your mvp_scope MUST name the exact files to modify or create within this repo
- Your affected_files MUST list real paths that exist (or will be created) in this project

## Project Health Snapshot

### Source Files in src/project_forge/
{file_tree_section}

### Open GitHub Issues ({issue_count} open)
{issues_section}

### Recent Commits (last 10)
{commits_section}

### Test Suite
- Test files: {test_count}

### Lint Status
{lint_status}

### Code Volume
{code_stats_section}

## Recently Suggested Self-Improvements (avoid duplicates)
{recent_improvements_section}

## Your Task

Look at the actual source files listed above. Identify ONE concrete improvement — \
a bug fix, a missing test, a security hardening, a refactor, or a UX tweak to the \
existing dashboard/API. Reference specific files by path.

Respond with ONLY valid JSON in this exact format:
{{
    "name": "Short Improvement Name (2-4 words)",
    "tagline": "One-sentence description (under 100 chars)",
    "description": "What the problem is, which files are affected, what the fix is",
    "category": "self-improvement",
    "market_analysis": "Why this matters for Project Forge reliability or usability",
    "feasibility_score": 0.85,
    "mvp_scope": "Exact files to change: src/project_forge/... and tests/...",
    "tech_stack": ["python", "pytest"],
    "affected_files": ["src/project_forge/web/routes.py", "tests/test_routes.py"]
}}

The feasibility_score should reflect how quickly this can be implemented (0.7–1.0 for small fixes, \
0.4–0.7 for larger refactors). The category MUST be "self-improvement".
"""


def build_introspection_prompt(context: dict, recent_improvements: list[str]) -> str:
    """Build a prompt string for Claude to suggest one self-improvement idea.

    Args:
        context: Dict returned by gather_self_context().
        recent_improvements: Names of recently suggested improvements to avoid duplicates.

    Returns:
        A formatted prompt string ready to send to Claude.
    """
    # Issues section
    issues = context.get("open_issues", [])
    if issues:
        issues_lines = "\n".join(
            f"- #{i.get('number', '?')}: {i.get('title', '(no title)')} — {i.get('url', '')}" for i in issues
        )
    else:
        issues_lines = "(no open issues)"

    # Commits section
    commits = context.get("recent_commits", [])
    commits_section = "\n".join(f"- {c}" for c in commits) if commits else "(no commits available)"

    # Code stats section
    code_stats = context.get("code_stats", {})
    code_stats_section = "\n".join(f"- {k}: {v} lines" for k, v in code_stats.items())

    # Recent improvements section
    if recent_improvements:
        recent_section = "\n".join(f"- {name}" for name in recent_improvements)
    else:
        recent_section = "(none yet)"

    # File tree section
    file_tree = context.get("file_tree", [])
    file_tree_section = "\n".join(f"- {f}" for f in file_tree) if file_tree else "(not available)"

    return _INTROSPECTION_PROMPT_TEMPLATE.format(
        issue_count=len(issues),
        issues_section=issues_lines,
        commits_section=commits_section,
        test_count=context.get("test_count", 0),
        lint_status=context.get("lint_status", "unknown"),
        code_stats_section=code_stats_section,
        recent_improvements_section=recent_section,
        file_tree_section=file_tree_section,
    )


# ---------------------------------------------------------------------------
# Validation: reject ideas that are really new-project proposals
# ---------------------------------------------------------------------------

_NEW_PROJECT_SIGNALS = [
    "phase 1",
    "phase 2",
    "ship to",
    "early adopters",
    "multi-tenant",
    "enterprise sso",
    "saas",
    "willing to pay",
    "competitive landscape",
    "market demand",
    "go-to-market",
    "pricing model",
    "weeks 1-2",
    "weeks 3-4",
]


def validate_self_improvement(idea) -> bool:
    """Check if a self-improvement idea is actually about improving project-forge.

    Returns True if the idea looks like a genuine code improvement.
    Returns False if it reads like a new external project proposal.
    """
    text = f"{idea.description} {idea.mvp_scope} {idea.market_analysis}".lower()

    # Check for new-project language
    for signal in _NEW_PROJECT_SIGNALS:
        if signal in text:
            logger.info("SI idea '%s' rejected: contains new-project signal %r", idea.name, signal)
            return False

    return True
