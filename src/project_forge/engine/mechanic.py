"""Forge Mechanic (#99/#100) — the autonomous self-improvement engine.

Picks the highest-priority Think Tank item, implements it with an ISOLATED
`claude -p` agent run (the operator's Pro/Max subscription), gates on the
full test suite + ruff, and opens a PR for the operator to review + merge
via the review panel. PR-gated by design — nothing auto-merges.

Pieces:
  - work selection : rank active self-improvement items, security-debt first
  - orchestrator   : select -> worktree -> agent -> gate -> PR -> cleanup

Every run is isolated in a throwaway `git worktree`, so a bad agent run can
never corrupt the live tree or the running server. The agent is scoped with
`--allowedTools` (NEVER --dangerously-skip-permissions, per the project's
permission policy), and a denylist blocks it from touching its own
guardrails, CI, or the permission config.
"""

from __future__ import annotations

import logging
import os
import re
import shutil
import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path

from project_forge.models import Idea, IdeaCategory
from project_forge.storage.db import Database

logger = logging.getLogger(__name__)

# .../src/project_forge/engine/mechanic.py -> repo root
_PROJECT_ROOT = Path(__file__).resolve().parents[3]

# Per-run wall-clock cap for the agent (seconds) — bounds time and, by proxy,
# subscription spend.
AGENT_TIMEOUT = int(os.environ.get("FORGE_MECHANIC_AGENT_TIMEOUT", "1800"))

# Tools the headless agent may use — read/edit/write code + run tests and ruff.
# NOT arbitrary bash, NOT git/gh (the orchestrator owns commits + PRs), and
# NEVER --dangerously-skip-permissions.
AGENT_ALLOWED_TOOLS = [
    "Read",
    "Edit",
    "Write",
    "Glob",
    "Grep",
    "Bash(python -m pytest:*)",
    "Bash(pytest:*)",
    "Bash(python -m ruff:*)",
    "Bash(ruff:*)",
]

# The agent must never rewrite the mechanic, the runner, their guardrails,
# CI, or the permission config — a self-modifier that edits its own leash
# voids every other control.
_FORBIDDEN_FILES = frozenset(
    {
        "src/project_forge/engine/mechanic.py",
        "src/project_forge/cron/self_improve_runner.py",
        "src/project_forge/config.py",
        "src/project_forge/web/auth.py",
        "src/project_forge/web/app.py",
        "src/project_forge/storage/db.py",
    }
)
_FORBIDDEN_PREFIXES = (".github/", ".claude/", ".env", "scripts/")


# --------------------------------------------------------------------------- #
# Work selection (also implements the "Think Tank Priority Ranking" item)     #
# --------------------------------------------------------------------------- #

_SECURITY_RE = re.compile(
    r"\b(ssrf|rebind|token|secret|leak|redact|sanitiz\w*|inject\w*|traversal|"
    r"validate|validation|rate.?limit|auth\w*|escap\w*|ssl|tls|cve|vuln\w*|"
    r"private|permission|exfiltrat\w*|write.?lock)\b",
    re.IGNORECASE,
)


def priority_score(idea: Idea) -> float:
    """Mechanic work-queue rank; higher = work first.

    Base = the introspect engine's own feasibility confidence; security debt
    gets a bonus (the July audit found the whole June-30 security batch
    unshipped — highest-value work); operator-approved outranks a raw
    proposal.
    """
    score = idea.feasibility_score or 0.0
    blob = f"{idea.name} {idea.tagline} {idea.description or ''}"
    if _SECURITY_RE.search(blob):
        score += 0.25
    if idea.status == "approved":
        score += 0.15
    return score


async def rank_work(db: Database, limit: int = 20) -> list[Idea]:
    """Active self-improvement items, highest priority first."""
    cur = await db.db.execute(
        "SELECT id FROM ideas WHERE category = ? AND status IN ('new', 'approved')",
        (IdeaCategory.SELF_IMPROVEMENT.value,),
    )
    rows = await cur.fetchall()
    ideas: list[Idea] = []
    for r in rows:
        idea = await db.get_idea(r["id"])
        if idea is not None:
            ideas.append(idea)
    ideas.sort(key=lambda i: (priority_score(i), i.generated_at.timestamp()), reverse=True)
    return ideas[:limit]


async def select_work(db: Database, *, exclude_ids: set[str] | None = None) -> Idea | None:
    """The single highest-priority item to work next (or None)."""
    exclude = exclude_ids or set()
    for idea in await rank_work(db):
        if idea.id not in exclude:
            return idea
    return None


def build_task_prompt(idea: Idea) -> str:
    """The scoped brief handed to the headless agent."""
    return (
        "You are the Forge Mechanic, implementing ONE self-improvement item in "
        "the Project Forge repo. Work ONLY on the item below.\n\n"
        f"## Item: {idea.name}\n{idea.tagline}\n\n{idea.description or ''}\n\n"
        "## Rules\n"
        "- TDD: write or extend a test that fails for the gap, then implement "
        "until it passes.\n"
        "- Keep the change tightly scoped to this item — no drive-by refactors.\n"
        "- `python -m pytest tests/ -q`, `python -m ruff check src/ tests/`, and "
        "`python -m ruff format src/ tests/` MUST all pass when you finish.\n"
        "- Do NOT edit: .github/, .claude/, scripts/, engine/mechanic.py, "
        "cron/self_improve_runner.py, config.py, web/auth.py, web/app.py, or "
        "storage/db.py.\n"
        "- Do NOT run git or gh — just leave the working tree changed.\n"
    )


# --------------------------------------------------------------------------- #
# Worktree + agent + gate + PR                                                #
# --------------------------------------------------------------------------- #


@dataclass
class MechanicResult:
    idea_id: str
    idea_name: str
    status: str  # pr_opened | gate_failed | agent_failed | no_change | no_work
    pr_url: str | None = None
    detail: str = ""


def _run(cmd: list[str], *, cwd: str | None = None, timeout: int = 120) -> subprocess.CompletedProcess:
    return subprocess.run(cmd, capture_output=True, text=True, cwd=cwd, timeout=timeout)


def _create_workspace(branch: str) -> Path:
    """Isolated workspace = a fresh local clone in a temp dir, on `branch`.

    git worktrees are unavailable here — `.git/worktrees` is a read-only
    mount — and a clone is stronger isolation anyway: it's a CLEAN checkout
    of committed main (not the messy live tree the running server sits in),
    so a bad agent run can't touch the live checkout at all. Origin is
    repointed to the real GitHub remote so push + PR work.
    """
    ws = Path(tempfile.mkdtemp(prefix="mechanic-"))
    _run(["git", "clone", "--quiet", str(_PROJECT_ROOT), str(ws)], timeout=300)
    _run(["git", "checkout", "-B", branch], cwd=str(ws))
    remote = _run(["git", "remote", "get-url", "origin"], cwd=str(_PROJECT_ROOT)).stdout.strip()
    if remote:
        _run(["git", "remote", "set-url", "origin", remote], cwd=str(ws))
    return ws


def _remove_workspace(ws: Path) -> None:
    """Delete the throwaway clone. The mechanic branch lives only inside it
    (plus, once pushed, on GitHub) — nothing to clean in the live repo."""
    shutil.rmtree(ws, ignore_errors=True)


def run_agent(worktree: Path, prompt: str, *, timeout: int = AGENT_TIMEOUT) -> subprocess.CompletedProcess:
    """Invoke `claude -p` as a scoped agent inside the worktree, on the
    Pro/Max SUBSCRIPTION (the logged-in CLI). Injectable for tests."""
    from project_forge.engine.llm_backend import _claude_cli_path

    claude = _claude_cli_path() or "claude"
    return _run(
        [claude, "--print", "--permission-mode", "acceptEdits", "--allowedTools", *AGENT_ALLOWED_TOOLS, prompt],
        cwd=str(worktree),
        timeout=timeout,
    )


def _changed_paths(workspace: Path) -> list[str]:
    # Uncommitted working-tree changes the agent made (branch starts at main).
    diff = _run(["git", "diff", "--name-only", "HEAD"], cwd=str(workspace))
    return [ln.strip() for ln in diff.stdout.splitlines() if ln.strip()]


def _forbidden_touched(paths: list[str]) -> str | None:
    for p in paths:
        if p in _FORBIDDEN_FILES or any(p.startswith(pre) for pre in _FORBIDDEN_PREFIXES):
            return p
    return None


def _quality_gate(worktree: Path) -> tuple[bool, str]:
    """Full suite + ruff check + ruff format-check inside the worktree — the
    same bar a human PR must clear."""
    tests = _run(["python3", "-m", "pytest", "tests/", "-q"], cwd=str(worktree), timeout=1200)
    if tests.returncode != 0:
        return False, f"pytest failed:\n{tests.stdout[-2000:]}"
    check = _run(["python3", "-m", "ruff", "check", "src/", "tests/"], cwd=str(worktree), timeout=180)
    if check.returncode != 0:
        return False, f"ruff check failed:\n{check.stdout[-1000:]}"
    fmt = _run(["python3", "-m", "ruff", "format", "--check", "src/", "tests/"], cwd=str(worktree), timeout=180)
    if fmt.returncode != 0:
        return False, f"ruff format failed:\n{fmt.stdout[-1000:]}"
    return True, "ok"


def _open_pr(worktree: Path, branch: str, idea: Idea) -> str:
    """Commit + push the branch + open a PR. Returns the PR URL."""
    _run(["git", "add", "-A"], cwd=str(worktree))
    msg = (
        f"mechanic: {idea.name}\n\n"
        f"Autonomous self-improvement for Think Tank item {idea.id}.\n\n"
        "Co-Authored-By: Claude <noreply@anthropic.com>"
    )
    _run(["git", "commit", "-m", msg], cwd=str(worktree))
    _run(["git", "push", "-u", "--force-with-lease", "origin", branch], cwd=str(worktree))
    pr = _run(
        [
            "gh",
            "pr",
            "create",
            "--title",
            f"[Mechanic] {idea.name}",
            "--body",
            (f"Autonomous implementation of Think Tank item `{idea.id}`.\n\n{idea.tagline}\n\nReview + merge to ship."),
            "--head",
            branch,
        ],
        cwd=str(worktree),
    )
    return pr.stdout.strip()


async def run_mechanic_cycle(db: Database, *, exclude_ids: set[str] | None = None) -> MechanicResult:
    """One mechanic cycle: pick the top item, implement it in isolation, gate,
    and open a PR for the operator to review. Never merges. Never leaves a
    worktree behind."""
    idea = await select_work(db, exclude_ids=exclude_ids)
    if idea is None:
        return MechanicResult("", "", "no_work", detail="Think Tank queue empty")

    branch = f"mechanic/{idea.id}"
    wt = _create_workspace(branch)
    try:
        proc = run_agent(wt, build_task_prompt(idea))
        if proc.returncode != 0:
            return MechanicResult(idea.id, idea.name, "agent_failed", detail=(proc.stderr or "")[-500:])

        paths = _changed_paths(wt)
        if not paths:
            return MechanicResult(idea.id, idea.name, "no_change", detail="agent made no changes")

        bad = _forbidden_touched(paths)
        if bad is not None:
            return MechanicResult(idea.id, idea.name, "gate_failed", detail=f"touched forbidden path: {bad}")

        ok, why = _quality_gate(wt)
        if not ok:
            return MechanicResult(idea.id, idea.name, "gate_failed", detail=why)

        pr_url = _open_pr(wt, branch, idea)
        logger.info("Mechanic opened PR for %s: %s", idea.name, pr_url)
        return MechanicResult(idea.id, idea.name, "pr_opened", pr_url=pr_url)
    finally:
        _remove_workspace(wt)
