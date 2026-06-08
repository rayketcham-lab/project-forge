"""Sync auto-promoted ideas' DB state with their GitHub issue state.

Closes the gap that surfaced 2026-06-08: an idea promoted by
`/api/promote/{id}` got `status='approved'` + `auto_promoted_at` +
`github_issue_url`. The operator then closed the GitHub issue manually
(either COMPLETED = shipped it, or NOT_PLANNED = rejected it). The DB
had no idea, so the dashboard kept showing "✓ promoted" with a live
"issue ↗" link to a closed issue.

This cadence pulls live GH issue state for every `approved + promoted`
idea once an hour and updates the DB:

  state=OPEN                 → leave alone (still in flight)
  state=CLOSED reason=COMPLETED   → status='contributed'
  state=CLOSED reason=NOT_PLANNED → status='archived', reason='gh_closed_not_planned'

The auto_promoted_at + github_issue_url stamps stay regardless — they're
historical record of the promotion attempt.
"""

from __future__ import annotations

import json
import logging
import re
import subprocess
from datetime import UTC, datetime
from typing import Any

from project_forge.storage.db import Database

logger = logging.getLogger(__name__)


_ISSUE_URL_RE = re.compile(
    r"^https?://github\.com/(?P<owner>[\w.\-]+)/(?P<repo>[\w.\-]+)/issues/(?P<n>\d+)/?$"
)


def parse_issue_ref(url: str | None) -> tuple[str, int] | None:
    """`https://github.com/o/r/issues/42` → `("o/r", 42)`.

    Returns None on malformed URLs so the sync can skip-not-crash.
    """
    if not url:
        return None
    m = _ISSUE_URL_RE.match(url.strip())
    if not m:
        return None
    return f"{m.group('owner')}/{m.group('repo')}", int(m.group("n"))


def fetch_issue_state(repo: str, issue_number: int) -> dict[str, Any] | None:
    """One `gh issue view ... --json state,stateReason` call.

    Returns {"state": "OPEN"|"CLOSED", "reason": "COMPLETED"|"NOT_PLANNED"|None}
    or None on gh failure. Kept module-level so the sync runner can
    monkeypatch it in tests without going anywhere near the network.
    """
    try:
        out = subprocess.check_output(
            [
                "gh", "issue", "view", str(issue_number), "-R", repo,
                "--json", "state,stateReason",
            ],
            text=True,
            timeout=15,
        )
    except (subprocess.SubprocessError, OSError) as exc:
        logger.warning("gh issue view %s#%s failed: %s", repo, issue_number, exc)
        return None
    try:
        data = json.loads(out)
    except json.JSONDecodeError:
        logger.warning("gh issue view %s#%s returned non-JSON", repo, issue_number)
        return None
    return {
        "state": data.get("state"),
        "reason": data.get("stateReason"),
    }


async def run_issue_sync_cycle(db: Database) -> dict[str, Any]:
    """One sync pass over every approved + promoted idea.

    Returns a small report:
      {"checked": N, "updated": M, "results": [...]}
    """
    cur = await db.db.execute(
        """
        SELECT id, github_issue_url FROM ideas
        WHERE status = 'approved'
          AND auto_promoted_at IS NOT NULL
          AND github_issue_url IS NOT NULL
        """
    )
    rows = await cur.fetchall()
    checked = 0
    updated = 0
    results: list[dict[str, Any]] = []

    for row in rows:
        ref = parse_issue_ref(row["github_issue_url"])
        if ref is None:
            logger.info("skip idea=%s: unparseable issue url", row["id"])
            continue
        repo, num = ref
        state = fetch_issue_state(repo, num)
        if state is None:
            results.append({"id": row["id"], "skipped": "gh_failure"})
            continue
        checked += 1
        if state["state"] != "CLOSED":
            results.append({"id": row["id"], "state": state["state"], "action": "no_change"})
            continue

        reason = (state.get("reason") or "").upper()
        if reason == "COMPLETED":
            await _flip_to(db, row["id"], "contributed")
            updated += 1
            results.append({"id": row["id"], "action": "contributed"})
        elif reason == "NOT_PLANNED":
            await _archive(db, row["id"], "gh_closed_not_planned")
            updated += 1
            results.append({"id": row["id"], "action": "archived"})
        else:
            # Closed without a reason — be conservative and leave it alone.
            results.append({"id": row["id"], "action": "ambiguous_close"})

    logger.info("issue_sync: checked=%d updated=%d", checked, updated)
    return {"checked": checked, "updated": updated, "results": results}


async def _flip_to(db: Database, idea_id: str, status: str) -> None:
    idea = await db.get_idea(idea_id)
    if idea is None:
        return
    idea.status = status
    await db.save_idea(idea)


async def _archive(db: Database, idea_id: str, reason: str) -> None:
    """Same shape as the siphon's archives: status + archived_reason +
    archived_at. Uses raw SQL because db.save_idea doesn't carry the
    archived_* columns and we don't want to read-modify-write here.
    """
    async with db._write_lock:
        await db.db.execute(
            "UPDATE ideas SET status='archived', "
            "archived_reason=?, archived_at=? WHERE id=?",
            (reason, datetime.now(UTC).isoformat(), idea_id),
        )
        await db.db.commit()
