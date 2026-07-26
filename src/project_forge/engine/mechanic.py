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
AGENT_TIMEOUT = int(os.environ.get("FORGE_MECHANIC_AGENT_TIMEOUT", "2400"))

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

# The agent may never rewrite its OWN LEASH — the mechanic, the runner,
# their review gate, CI, or the permission config. A self-modifier that can
# edit those escapes review BEFORE the operator sees the PR, voiding every
# other control. Everything else — including app.py / db.py / auth.py /
# config.py — IS editable: those are legitimate targets for the security
# backlog, and the PR review panel is the human gate on them.
_FORBIDDEN_FILES = frozenset(
    {
        "src/project_forge/engine/mechanic.py",
        "src/project_forge/engine/mechanic_review.py",
        "src/project_forge/cron/mechanic_runner.py",
        "src/project_forge/cron/self_improve_runner.py",
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
        "You are the Forge Mechanic. Implement ONE self-improvement item in this "
        "Project Forge repo END TO END and leave the working tree with the fix "
        "COMPLETE and every test passing.\n\n"
        f"## Item: {idea.name}\n{idea.tagline}\n\n{idea.description or ''}\n\n"
        "## DONE means ALL of these — do not stop until they hold\n"
        "1. You wrote or extended a test that pins the fix.\n"
        "2. You IMPLEMENTED the fix in the source code — not just the test.\n"
        "3. `python -m pytest tests/ -q` passes with ZERO failures. If a test is "
        "red, keep working until it is green — never finish on a failing test.\n"
        "4. `python -m ruff check src/ tests/` and `python -m ruff format src/ tests/` "
        "are clean.\n\n"
        "## Work efficiently\n"
        "- While iterating, run just the relevant test file "
        "(`python -m pytest tests/test_<x>.py -q`) — it is much faster. Run the "
        "FULL suite once at the very end to confirm nothing else broke.\n"
        "- Keep the change tightly scoped to this item. No drive-by refactors.\n\n"
        "## Do NOT touch\n"
        ".github/, .claude/, scripts/, or the mechanic's own files "
        "(engine/mechanic.py, engine/mechanic_review.py, cron/mechanic_runner.py, "
        "cron/self_improve_runner.py). Everything else — including app.py, db.py, "
        "auth.py — is fair game. Do NOT run git or gh; just leave the tree changed.\n"
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


def _run(
    cmd: list[str], *, cwd: str | None = None, timeout: int = 120, env: dict[str, str] | None = None
) -> subprocess.CompletedProcess:
    return subprocess.run(cmd, capture_output=True, text=True, cwd=cwd, timeout=timeout, env=env)


def _clone_env(workspace: Path) -> dict[str, str]:
    """Env for subprocesses run against the CLONE. Puts the clone's `src` at
    the FRONT of PYTHONPATH so `import project_forge` resolves to the agent's
    edits — not the editable-installed MAIN repo. Without this the gate (and
    the agent's own test runs) import stale main-repo code, so every source
    change is invisible and even a correct fix fails the gate."""
    env = dict(os.environ)
    src = str(workspace / "src")
    existing = env.get("PYTHONPATH", "")
    env["PYTHONPATH"] = f"{src}{os.pathsep}{existing}" if existing else src
    return env


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


def run_agent(workspace: Path, prompt: str, *, timeout: int = AGENT_TIMEOUT) -> subprocess.CompletedProcess:
    """Invoke `claude -p` as a scoped agent inside the workspace, on the
    Pro/Max SUBSCRIPTION (the logged-in CLI). Injectable for tests.

    The prompt goes via STDIN, not as a positional arg: `--allowedTools`
    takes N values and would otherwise swallow a trailing prompt argument
    (claude then errors 'Input must be provided … when using --print')."""
    from project_forge.engine.llm_backend import _claude_cli_path

    claude = _claude_cli_path() or "claude"
    return subprocess.run(
        [claude, "--print", "--permission-mode", "acceptEdits", "--allowedTools", *AGENT_ALLOWED_TOOLS],
        input=prompt,
        capture_output=True,
        text=True,
        cwd=str(workspace),
        env=_clone_env(workspace),
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


# The wheel build/install test is about packaging, not the code change under
# review, and it can't run inside a throwaway clone (no network/venv). CI
# validates packaging separately, so deselect it here — otherwise it blocks
# every otherwise-green mechanic PR.
_GATE_DESELECT = "tests/test_packaging.py::TestInstallAndRun::test_wheel_installs_in_venv"


def _quality_gate(worktree: Path) -> tuple[bool, str]:
    """Full suite + ruff check + ruff format-check inside the worktree — the
    same bar a human PR must clear. Runs with the clone's src on PYTHONPATH so
    the agent's edits are what's actually tested."""
    env = _clone_env(worktree)
    tests = _run(
        ["python3", "-m", "pytest", "tests/", "-q", "--deselect", _GATE_DESELECT],
        cwd=str(worktree),
        env=env,
        timeout=1200,
    )
    if tests.returncode != 0:
        return False, f"pytest failed:\n{tests.stdout[-2000:]}"
    check = _run(["python3", "-m", "ruff", "check", "src/", "tests/"], cwd=str(worktree), env=env, timeout=180)
    if check.returncode != 0:
        return False, f"ruff check failed:\n{check.stdout[-1000:]}"
    fmt = _run(
        ["python3", "-m", "ruff", "format", "--check", "src/", "tests/"],
        cwd=str(worktree),
        env=env,
        timeout=180,
    )
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
    from project_forge.engine.mechanic_status import write_status

    write_status("selecting")
    idea = await select_work(db, exclude_ids=exclude_ids)
    if idea is None:
        write_status("no_work")
        return MechanicResult("", "", "no_work", detail="Think Tank queue empty")

    write_status("cloning", item=idea.name)
    branch = f"mechanic/{idea.id}"
    wt = _create_workspace(branch)
    try:
        write_status("implementing", item=idea.name)
        proc = run_agent(wt, build_task_prompt(idea))
        if proc.returncode != 0:
            write_status("agent_failed", item=idea.name, detail=(proc.stderr or "")[-300:])
            return MechanicResult(idea.id, idea.name, "agent_failed", detail=(proc.stderr or "")[-500:])

        paths = _changed_paths(wt)
        if not paths:
            write_status("no_change", item=idea.name)
            return MechanicResult(idea.id, idea.name, "no_change", detail="agent made no changes")

        bad = _forbidden_touched(paths)
        if bad is not None:
            write_status("gate_failed", item=idea.name, detail=f"touched forbidden path: {bad}")
            return MechanicResult(idea.id, idea.name, "gate_failed", detail=f"touched forbidden path: {bad}")

        write_status("gating", item=idea.name)
        ok, why = _quality_gate(wt)
        if not ok:
            write_status("gate_failed", item=idea.name, detail=why[-300:])
            return MechanicResult(idea.id, idea.name, "gate_failed", detail=why)

        write_status("opening_pr", item=idea.name)
        pr_url = _open_pr(wt, branch, idea)
        write_status("pr_opened", item=idea.name, detail=pr_url)
        logger.info("Mechanic opened PR for %s: %s", idea.name, pr_url)
        return MechanicResult(idea.id, idea.name, "pr_opened", pr_url=pr_url)
    finally:
        _remove_workspace(wt)
