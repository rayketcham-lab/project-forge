"""Sync GitHub org repos into the local repo registry."""

from __future__ import annotations

import json
import logging
import subprocess
from datetime import UTC, datetime

from project_forge.config import settings
from project_forge.models import RepoEntry
from project_forge.storage.db import Database

logger = logging.getLogger(__name__)


async def sync_org_repos(db: Database, org: str | None = None) -> list[RepoEntry]:
    """Pull all repos from the org and upsert into repo_registry.

    Skips repos with no description (forks, empty repos, etc.).
    Returns the list of upserted RepoEntry objects.

    Raises RuntimeError if the gh CLI call fails.
    """
    target_org = org or settings.github_org

    result = subprocess.run(
        [
            "gh",
            "repo",
            "list",
            target_org,
            "--limit",
            "100",
            "--json",
            "name,description,repositoryTopics",
        ],
        capture_output=True,
        text=True,
        timeout=60,
    )

    if result.returncode != 0:
        raise RuntimeError(f"gh repo list failed: {result.stderr.strip()}")

    try:
        raw_repos = json.loads(result.stdout)
    except json.JSONDecodeError as exc:
        raise RuntimeError(f"Failed to parse gh output: {exc}") from exc

    now_iso = datetime.now(UTC).isoformat()
    upserted: list[RepoEntry] = []

    for repo in raw_repos:
        description = (repo.get("description") or "").strip()
        if not description:
            logger.debug("Skipping repo %s/%s — no description", target_org, repo["name"])
            continue

        topics = [
            t["node"]["name"]
            for t in (repo.get("repositoryTopics") or [])
            if t.get("node", {}).get("name")
        ]

        entry = RepoEntry(
            repo_full_name=f"{target_org}/{repo['name']}",
            description=description,
            topics=topics,
            last_synced=now_iso,
        )
        await db.upsert_repo_entry(entry)
        upserted.append(entry)
        logger.debug("Upserted repo: %s", entry.repo_full_name)

    logger.info("Synced %d repos from org %s", len(upserted), target_org)
    return upserted
