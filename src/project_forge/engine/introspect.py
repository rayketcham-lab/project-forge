"""Self-introspection engine for Project Forge.

Gathers context about the project's own codebase, tests, and open issues,
then builds a prompt that asks Claude to suggest ONE self-improvement idea.

Modes:
- 'code-fix' (default): patches lint/test/UX bugs in any file.
- 'generation': patches idea-generation logic only, must declare a target
  metric. Powered by engine/telemetry.py signals.
"""

from __future__ import annotations

import json
import logging
import re
import subprocess
from pathlib import Path
from typing import TYPE_CHECKING, Literal

if TYPE_CHECKING:
    from project_forge.models import Idea
    from project_forge.storage.db import Database

logger = logging.getLogger(__name__)

GENERATION_FILES = (
    "engine/prompts.py",
    "engine/categories.py",
    "engine/super_ideas.py",
    "engine/router.py",
    "engine/dedup.py",
)

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

    # --- Untested modules (evidence pack, #92) ---
    untested_modules: list[str] = []
    try:
        from project_forge.engine.static_introspect import find_untested_modules

        untested_modules = [f["path"] for f in find_untested_modules(_PROJECT_ROOT)][:10]
    except Exception as exc:
        logger.warning("Could not scan untested modules: %s", exc)

    return {
        "open_issues": open_issues,
        "recent_commits": recent_commits,
        "test_count": test_count,
        "lint_status": lint_status,
        "code_stats": code_stats,
        "file_tree": file_tree,
        "untested_modules": untested_modules,
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
- Your market_analysis MUST include a line "Target metric: <what measurably improves and how it is observed>"

## Project Health Snapshot

### Source Files in src/project_forge/
{file_tree_section}

### Open GitHub Issues ({issue_count} open)
{issues_section}

### Recent Commits (last 10)
{commits_section}

### Test Suite
- Test files: {test_count}

### Untested Modules (no matching test file — strong candidates)
{untested_section}

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
    "market_analysis": "Why this matters. Target metric: <the number that improves>",
    "feasibility_score": 0.85,
    "mvp_scope": "Exact files to change: src/project_forge/... and tests/...",
    "tech_stack": ["python", "pytest"],
    "affected_files": ["src/project_forge/web/routes.py", "tests/test_routes.py"]
}}

The feasibility_score should reflect how quickly this can be implemented (0.7–1.0 for small fixes, \
0.4–0.7 for larger refactors). The category MUST be "self-improvement".
"""


async def gather_generation_signals(db: Database) -> dict:
    """Pull telemetry into a structured dict for the generation-mode prompt.

    Each value is the raw output from engine/telemetry; the prompt builder
    is responsible for formatting.
    """
    from project_forge.engine import telemetry

    return {
        "filter_rate_by_category": await telemetry.filter_rate_by_category(db, days=7),
        "saturation_per_concept": await telemetry.saturation_per_concept(db, days=30, top_n=10),
        "novelty_trend": await telemetry.novelty_trend(db, days=14),
        "diversity_lever_usage": await telemetry.diversity_lever_usage(db, days=7),
        "coverage_gaps": await telemetry.coverage_gaps(db, threshold=20),
        "db_query_stats": db.get_query_stats(),
    }


def _format_generation_signals(signals: dict) -> str:
    lines = []

    rates = signals.get("filter_rate_by_category", {})
    if rates:
        sorted_rates = sorted(rates.items(), key=lambda x: -x[1])
        lines.append("### Filter rate by category (last 7d)")
        for cat, rate in sorted_rates:
            cat_val = cat.value if hasattr(cat, "value") else str(cat)
            lines.append(f"- {cat_val}: {rate:.2%}")
        lines.append("")

    sat = signals.get("saturation_per_concept", [])
    if sat:
        lines.append("### Saturated concepts (last 30d, top 10)")
        for word, count in sat:
            lines.append(f"- {word}: {count} rejections")
        lines.append("")

    trend = signals.get("novelty_trend", [])
    if trend:
        lines.append("### Novelty trend — avg tagline-similarity per day (rising = worse)")
        for day, score in trend[-7:]:
            lines.append(f"- {day}: {score:.3f}")
        lines.append("")

    levers = signals.get("diversity_lever_usage", {})
    if levers:
        lines.append("### Diversity lever usage (last 7d)")
        for lever, pct in levers.items():
            lines.append(f"- {lever}: {pct:.0%}")
        lines.append("")

    gaps = signals.get("coverage_gaps", [])
    if gaps:
        lines.append("### Coverage gaps (categories with <20 active ideas)")
        for cat in gaps:
            cat_val = cat.value if hasattr(cat, "value") else str(cat)
            lines.append(f"- {cat_val}")
        lines.append("")

    qstats = signals.get("db_query_stats", {})
    if qstats:
        lines.append("### DB query health (this process)")
        lines.append(
            f"- queries={qstats.get('total_queries', 0)}  avg_ms={qstats.get('avg_ms', 0.0)}  "
            f"max_ms={qstats.get('max_ms', 0.0)}  slow_count={qstats.get('slow_count', 0)}"
        )
        lines.append("")

    return "\n".join(lines) if lines else "(no signals yet)"


_GENERATION_MODE_PROMPT_TEMPLATE = """\
You are analyzing the Project Forge idea-generation engine to propose ONE \
surgical patch that improves idea quality. You are NOT proposing a new project. \
You are NOT fixing lint or unrelated bugs. You ARE editing the generation \
pipeline so the next batch of ideas is better.

## STRICT RULES
1. Your patch MUST modify at least one file in:
   - src/project_forge/engine/prompts.py
   - src/project_forge/engine/categories.py
   - src/project_forge/engine/super_ideas.py
   - src/project_forge/engine/router.py
   - src/project_forge/engine/dedup.py
2. Your market_analysis MUST contain the phrase "Target metric:" followed by \
   the specific metric you expect to move (e.g. \
   "Target metric: filter_rate[security-tool] should drop").
3. ONE hypothesis per patch. No shotgun changes across unrelated concerns.
4. Use these files as reference for what's currently saturated/broken — see \
   the telemetry signals below.

## Generation Telemetry Signals
{signals_section}

## Project File Tree (focus on engine/)
{file_tree_section}

## Recently Suggested Improvements (avoid duplicates)
{recent_improvements_section}

## Recent Commits
{commits_section}

## Your Task

Propose ONE concrete patch to the generation pipeline. Reference the \
saturation, novelty, or coverage signal that motivates it.

Respond with ONLY valid JSON in this exact format:
{{
    "name": "Short Patch Name (2-4 words)",
    "tagline": "What metric moves and why (under 100 chars)",
    "description": "What's broken in the current generation logic, which file(s) to edit, and the specific change",
    "category": "self-improvement",
    "market_analysis": "Target metric: <metric>. Current value: <x>. Expected after patch: <y>. Why.",
    "feasibility_score": 0.85,
    "mvp_scope": "Exact files to change in src/project_forge/engine/ and tests/",
    "tech_stack": ["python", "pytest"],
    "affected_files": ["src/project_forge/engine/prompts.py", "tests/test_prompts.py"]
}}
"""


def build_introspection_prompt(
    context: dict,
    recent_improvements: list[str],
    *,
    mode: Literal["code-fix", "generation"] = "code-fix",
    generation_signals: dict | None = None,
) -> str:
    """Build a prompt string for Claude to suggest one self-improvement idea.

    Args:
        context: Dict returned by gather_self_context().
        recent_improvements: Names of recently suggested improvements to avoid duplicates.
        mode: 'code-fix' (default) for the existing lint/test prompt, or
            'generation' for the surgical idea-quality patch prompt.
        generation_signals: Required when mode='generation'. Output of
            gather_generation_signals(db).

    Returns:
        A formatted prompt string ready to send to Claude.
    """
    if mode == "generation":
        if generation_signals is None:
            raise ValueError("mode='generation' requires generation_signals")
        commits = context.get("recent_commits", [])
        commits_section = "\n".join(f"- {c}" for c in commits) if commits else "(none)"
        recent_section = "\n".join(f"- {n}" for n in recent_improvements) if recent_improvements else "(none yet)"
        file_tree = context.get("file_tree", [])
        engine_files = [f for f in file_tree if "engine/" in f]
        file_tree_section = "\n".join(f"- {f}" for f in engine_files) if engine_files else "(not available)"
        return _GENERATION_MODE_PROMPT_TEMPLATE.format(
            signals_section=_format_generation_signals(generation_signals),
            file_tree_section=file_tree_section,
            recent_improvements_section=recent_section,
            commits_section=commits_section,
        )

    # Default: code-fix mode (unchanged)
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

    # Untested modules section (evidence pack, #92)
    untested = context.get("untested_modules", [])
    untested_section = "\n".join(f"- {m}" for m in untested) if untested else "(all modules have test files)"

    return _INTROSPECTION_PROMPT_TEMPLATE.format(
        issue_count=len(issues),
        issues_section=issues_lines,
        commits_section=commits_section,
        test_count=context.get("test_count", 0),
        untested_section=untested_section,
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


_TARGET_METRIC_RE = re.compile(r"target\s*metric\s*:", re.IGNORECASE)


def has_target_metric(idea: Idea) -> bool:
    """True when the idea declares a 'Target metric:' line (#92).

    Every LLM-generated self-improvement must say what measurably improves —
    an improvement you can't observe isn't one.
    """
    return bool(_TARGET_METRIC_RE.search(idea.market_analysis or ""))


def validate_generation_patch(idea: Idea) -> bool:
    """Validate a generation-mode SI patch.

    Requirements:
    - market_analysis names a Target metric.
    - description or mvp_scope mentions a file in GENERATION_FILES.

    Returns True if valid; False otherwise (with a logged reason).
    """
    text = f"{idea.description}\n{idea.mvp_scope}\n{idea.market_analysis}"

    if not _TARGET_METRIC_RE.search(idea.market_analysis or ""):
        logger.info("Generation patch '%s' rejected: missing 'Target metric:' declaration", idea.name)
        return False

    if not any(path_hint in text for path_hint in GENERATION_FILES):
        logger.info(
            "Generation patch '%s' rejected: no generation file referenced (need one of %s)",
            idea.name,
            GENERATION_FILES,
        )
        return False

    return True
