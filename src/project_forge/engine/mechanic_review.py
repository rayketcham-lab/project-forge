"""Review-panel backend (#100) — list / merge / close the Mechanic's PRs.

The operator's gate. The mechanic opens PRs on `mechanic/<item-id>` branches;
a human approves (squash-merge) or rejects (close) them here. NOTHING merges
without a button click — thin, testable wrappers over `gh`.
"""

from __future__ import annotations

import json
import logging
import subprocess

logger = logging.getLogger(__name__)

MECHANIC_BRANCH_PREFIX = "mechanic/"


def _gh(args: list[str], *, timeout: int = 30) -> subprocess.CompletedProcess:
    return subprocess.run(["gh", *args], capture_output=True, text=True, timeout=timeout)


def _ci_state(rollup: list | None) -> str:
    """Summarize a PR's statusCheckRollup into passing / failing / pending /
    none. A human should not merge a PR whose CI is failing or still running,
    so the panel surfaces this next to the button."""
    if not rollup:
        return "none"
    states = set()
    for check in rollup:
        states.add((check.get("conclusion") or check.get("state") or check.get("status") or "").upper())
    if states & {"FAILURE", "ERROR", "CANCELLED", "TIMED_OUT"}:
        return "failing"
    if states & {"IN_PROGRESS", "QUEUED", "PENDING", ""}:
        return "pending"
    if states & {"SUCCESS", "COMPLETED", "NEUTRAL", "SKIPPED"}:
        return "passing"
    return "unknown"


def list_open_prs() -> list[dict]:
    """Open PRs the mechanic opened (branch prefix `mechanic/`)."""
    proc = _gh(
        [
            "pr",
            "list",
            "--state",
            "open",
            "--limit",
            "50",
            "--json",
            "number,title,url,headRefName,additions,deletions,statusCheckRollup",
        ]
    )
    if proc.returncode != 0:
        logger.warning("gh pr list failed: %s", (proc.stderr or "")[:200])
        return []
    try:
        rows = json.loads(proc.stdout or "[]")
    except json.JSONDecodeError:
        return []
    out: list[dict] = []
    for r in rows:
        head = r.get("headRefName", "")
        if not head.startswith(MECHANIC_BRANCH_PREFIX):
            continue
        out.append(
            {
                "number": r["number"],
                "title": r["title"],
                "url": r["url"],
                "item_id": head[len(MECHANIC_BRANCH_PREFIX) :],
                "additions": r.get("additions", 0),
                "deletions": r.get("deletions", 0),
                "ci": _ci_state(r.get("statusCheckRollup")),
            }
        )
    return out


def merge_pr(number: int) -> dict:
    """Squash-merge a mechanic PR (human-gated) and delete its branch."""
    proc = _gh(["pr", "merge", str(number), "--squash", "--delete-branch"], timeout=120)
    return {"ok": proc.returncode == 0, "detail": (proc.stdout or proc.stderr or "").strip()[:400]}


def close_pr(number: int) -> dict:
    """Close (reject) a mechanic PR and delete its branch."""
    proc = _gh(["pr", "close", str(number), "--delete-branch"], timeout=60)
    return {"ok": proc.returncode == 0, "detail": (proc.stdout or proc.stderr or "").strip()[:400]}
