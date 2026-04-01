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
        result = _run([
            "gh", "issue", "list",
            "--state", "open",
            "--json", "title,number,labels,url",
        ])
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
    test_dir = Path("tests")
    test_count = len(list(test_dir.glob("test_*.py")))

    # --- Lint status ---
    lint_status = "unknown"
    try:
        result = _run(["ruff", "check", "src/", "tests/", "--statistics"])
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

    return {
        "open_issues": open_issues,
        "recent_commits": recent_commits,
        "test_count": test_count,
        "lint_status": lint_status,
        "code_stats": code_stats,
    }


_INTROSPECTION_PROMPT_TEMPLATE = """\
You are analyzing the Project Forge codebase to suggest ONE targeted self-improvement idea.

## Project Health Snapshot

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

Analyze the above and identify the SINGLE most impactful improvement for Project Forge itself. \
Focus on areas like:
- Missing tests or low coverage for existing modules
- CI pipeline gaps or missing checks
- Security issues in the web layer or data handling
- UX improvements in the dashboard
- Missing features that would make the engine smarter
- Performance bottlenecks or reliability issues

Respond with ONLY valid JSON in this exact format:
{{
    "name": "Short Improvement Name (2-4 words)",
    "tagline": "One-sentence description (under 100 chars)",
    "description": "2-3 paragraphs: what problem exists, what the fix is, why it matters",
    "category": "self-improvement",
    "market_analysis": "Why this improvement matters for Project Forge's reliability/usefulness",
    "feasibility_score": 0.85,
    "mvp_scope": "Exact scope: what to build/fix, what to skip",
    "tech_stack": ["python", "pytest"]
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
            f"- #{i.get('number', '?')}: {i.get('title', '(no title)')} — {i.get('url', '')}"
            for i in issues
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

    return _INTROSPECTION_PROMPT_TEMPLATE.format(
        issue_count=len(issues),
        issues_section=issues_lines,
        commits_section=commits_section,
        test_count=context.get("test_count", 0),
        lint_status=context.get("lint_status", "unknown"),
        code_stats_section=code_stats_section,
        recent_improvements_section=recent_section,
    )
