"""Forge Mechanic (#99/#100) — the autonomous self-improvement engine.

Picks the highest-priority Think Tank item, implements it with an ISOLATED
headless agent run, gates on the full test suite + ruff, and opens a PR for
the operator to review + merge via the review panel. PR-gated by design —
nothing auto-merges.

Pieces:
  - work selection : rank active self-improvement items, security-debt first
  - orchestrator   : select -> worktree -> agent -> gate -> PR -> cleanup

Every run is isolated in a throwaway workspace clone, so a bad agent run can
never corrupt the live tree or the running server. The agent is scoped with
an explicit allowed-tool list (NEVER --dangerously-skip-permissions, per the
project's permission policy), and a denylist blocks it from touching its own
guardrails, CI, or the permission config.
"""

from __future__ import annotations

import json
import logging
import os
import re
import shutil
import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path

from project_forge.config import settings
from project_forge.models import Idea, IdeaCategory
from project_forge.storage.db import Database

logger = logging.getLogger(__name__)

# .../src/project_forge/engine/mechanic.py -> repo root
_PROJECT_ROOT = Path(__file__).resolve().parents[3]

# Per-run wall-clock cap for the agent (seconds) — bounds time and, by proxy,
# subscription spend.
AGENT_TIMEOUT = int(os.environ.get("FORGE_MECHANIC_AGENT_TIMEOUT", "2400"))

# Model for the headless agent. The BYO-LLM backend resolves the model itself
# from FORGE_LLM_MODEL / settings.llm_model; FORGE_MECHANIC_MODEL remains as
# an optional per-mechanic override passed to resolve_backend(model_override=).
def _resolve_agent_model() -> str:
    """Agent model the mechanic runs on.

    Precedence: FORGE_MECHANIC_MODEL env override, then the configured
    BYO-LLM model (settings.llm_model / FORGE_LLM_MODEL), else '' (endpoint
    default). Resolved lazily so env/settings changes take effect per call.
    """
    override = os.environ.get("FORGE_MECHANIC_MODEL", "").strip()
    if override:
        return override
    return (settings.llm_model or "").strip()
AGENT_EFFORT = os.environ.get("FORGE_MECHANIC_EFFORT", "medium").strip()

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
        "Project Forge repo END TO END and specify the edits that leave the "
        "working tree with the fix COMPLETE.\n\n"
        f"## Item: {idea.name}\n{idea.tagline}\n\n{idea.description or ''}\n\n"
        "## DONE means ALL of these — do not stop until they hold\n"
        "1. You wrote or extended a test that pins the fix.\n"
        "2. You IMPLEMENTED the fix in the source code — not just the test.\n"
        "3. `python -m pytest tests/ -q` passes with ZERO failures. If a test is "
        "red, keep working until it is green — never finish on a failing test.\n"
        "4. `python -m ruff check src/ tests/` and `python -m ruff format src/ tests/` "
        "are clean.\n\n"
        "## Respond with ONLY valid JSON (no markdown wrapping) in this exact format:\n"
        "{\n"
        "    \"summary\": \"One-line description of what you changed\",\n"
        "    \"changes\": [\n"
        "        {\n"
        "            \"path\": \"relative/path/to/file.py\",\n"
        "            \"action\": \"edit\",\n"
        "            \"search\": \"exact string to find in the file\",\n"
        "            \"replace\": \"replacement string\"\n"
        "        },\n"
        "        {\n"
        "            \"path\": \"relative/path/to/new_file.py\",\n"
        "            \"action\": \"create\",\n"
        "            \"content\": \"full file content\"\n"
        "        }\n"
        "    ]\n"
        "}\n\n"
        "Rules:\n"
        "- action is \"edit\" (modify existing file) or \"create\" (new file)\n"
        "- For edits, \"search\" must be an exact substring of the current file content\n"
        "- Keep changes tightly scoped to this item. Include test changes if appropriate.\n"
        "- All paths are relative to the repo root.\n\n"
        "## Do NOT touch\n"
        ".github/, .claude/, scripts/, or the mechanic's own files "
        "(engine/mechanic.py, engine/mechanic_review.py, cron/mechanic_runner.py, "
        "cron/self_improve_runner.py). Everything else — including app.py, db.py, "
        "auth.py — is fair game.\n"
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


def _apply_change_set(workspace: Path, changes: list[dict]) -> bool:
    """Apply a JSON change set to the workspace. Returns True when the tree changed."""
    applied = False
    for change in changes:
        rel = str(change.get("path", "")).lstrip("/")
        if not rel:
            continue
        target = workspace / rel
        try:
            target.resolve().relative_to(workspace.resolve())
        except ValueError:
            logger.warning("Mechanic agent tried to write outside workspace: %s", rel)
            continue
        action = change.get("action")
        if action == "create":
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(change.get("content", ""))
            applied = True
        elif action == "edit":
            if not target.exists():
                logger.warning("Mechanic agent edit target missing: %s", rel)
                continue
            content = target.read_text()
            search = change.get("search", "")
            replace = change.get("replace", "")
            if search not in content:
                logger.warning(
                    "Mechanic agent search string not found in %s: %r…",
                    rel,
                    search[:80],
                )
                continue
            target.write_text(content.replace(search, replace, 1))
            applied = True
    return applied


def _parse_agent_response(text: str) -> list[dict]:
    """Parse the agent's JSON change set; [] on any failure so the caller no-ops."""
    text = text.strip()
    if "```json" in text:
        text = text.split("```json", 1)[1].split("```", 1)[0]
    elif "```" in text:
        text = text.split("```", 1)[1].split("```", 1)[0]
    try:
        data = json.loads(text)
    except (json.JSONDecodeError, TypeError):
        logger.warning("Mechanic agent response was not JSON — no changes applied")
        return []
    changes = data.get("changes", []) if isinstance(data, dict) else []
    return [c for c in changes if isinstance(c, dict)]


def run_agent(workspace: Path, prompt: str, *, timeout: int = AGENT_TIMEOUT) -> subprocess.CompletedProcess:
    """Ask the BYO-LLM backend to implement the task inside the workspace.

    The backend receives the task brief and returns a JSON change set that
    we apply directly to the workspace clone. Returns a
    :class:`subprocess.CompletedProcess`-shaped object (the orchestration
    and tests treat this as a CLI run): returncode 0 with stdout when the
    tree changed, returncode 1 with stderr otherwise.
    """
    from project_forge.engine.llm_backend import resolve_backend

    backend = resolve_backend()
    if backend is None:
        return subprocess.CompletedProcess(
            args=["llm-backend"],
            returncode=1,
            stderr="No LLM backend configured (FORGE_LLM_BASE_URL empty)",
        )
    text = backend.call(prompt) or ""
    if not text.strip():
        return subprocess.CompletedProcess(args=["llm-backend"], returncode=1, stderr="LLM backend returned empty")
    changes = _parse_agent_response(text)
    if not changes:
        return subprocess.CompletedProcess(
            args=["llm-backend"],
            returncode=1,
            stderr="LLM returned no usable change set",
        )
    if not _apply_change_set(workspace, changes):
        return subprocess.CompletedProcess(
            args=["llm-backend"],
            returncode=1,
            stderr="LLM change set produced no file changes",
        )
    return subprocess.CompletedProcess(args=["llm-backend"], returncode=0, stdout=text)


def _changed_paths(workspace: Path) -> list[str]:
    """Every path the agent touched, created ones included.

    This used to run `git diff --name-only HEAD`, which only reports *tracked*
    files — so anything the agent newly created was invisible to
    `_forbidden_touched`, while `_open_pr`'s `git add -A` happily committed it.
    A created `.github/workflows/*.yml` walked straight through the guard, and
    a created `conftest.py` is worse than that: `_quality_gate` runs pytest
    over the clone before any human sees the PR, so the file executes on the
    host on its way past.

    `--porcelain -uall` lists tracked modifications and untracked files alike,
    one line per file rather than collapsing new directories.
    """
    status = _run(
        ["git", "-c", "core.quotePath=false", "status", "--porcelain", "-uall"],
        cwd=str(workspace),
    )
    paths: list[str] = []
    for line in status.stdout.splitlines():
        if not line.strip():
            continue
        # Format is 2 status columns, a space, then the path.
        path = line[3:].strip()
        # Renames/copies read "old -> new"; both sides matter for the guard.
        if " -> " in path:
            before, _, after = path.partition(" -> ")
            paths.extend(p.strip().strip('"') for p in (before, after) if p.strip())
        elif path:
            paths.append(path.strip('"'))
    return paths


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
        f"Autonomous self-improvement for Think Tank item {idea.id}."
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
    # Never re-work an item that already has an open PR awaiting review — an
    # OPEN (unmerged) PR leaves the item at status='new', so without this the
    # mechanic redoes work that's already in the panel and opens a duplicate.
    exclude = set(exclude_ids or ())
    try:
        from project_forge.engine.mechanic_review import list_open_prs

        exclude |= {pr["item_id"] for pr in list_open_prs() if pr.get("item_id")}
    except Exception:
        logger.warning("could not list open mechanic PRs; proceeding without PR exclusion", exc_info=True)

    idea = await select_work(db, exclude_ids=exclude)
    if idea is None:
        write_status("no_work")
        return MechanicResult("", "", "no_work", detail="No unworked Think Tank items (all done or pending review)")

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
