"""Tests for repo registry sync."""

import json
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
import pytest_asyncio

from project_forge.storage.db import Database


@pytest_asyncio.fixture
async def db(tmp_path: Path):
    database = Database(tmp_path / "test_registry.db")
    await database.connect()
    yield database
    await database.close()


GH_TWO_REPOS = json.dumps(
    [
        {
            "name": "alpha-tool",
            "description": "Alpha tool for PKI management",
            "repositoryTopics": [{"node": {"name": "pki"}}, {"node": {"name": "security"}}],
        },
        {
            "name": "beta-scanner",
            "description": "Beta vulnerability scanner",
            "repositoryTopics": [],
        },
    ]
)

GH_WITH_EMPTY_DESC = json.dumps(
    [
        {
            "name": "alpha-tool",
            "description": "Alpha tool for PKI management",
            "repositoryTopics": [],
        },
        {
            "name": "no-desc-repo",
            "description": "",
            "repositoryTopics": [],
        },
    ]
)


def _make_completed_process(stdout: str, returncode: int = 0, stderr: str = "") -> MagicMock:
    proc = MagicMock()
    proc.stdout = stdout
    proc.returncode = returncode
    proc.stderr = stderr
    return proc


@pytest.mark.asyncio
async def test_sync_parses_gh_output(db: Database):
    from project_forge.engine.repo_registry import sync_org_repos

    with patch("subprocess.run", return_value=_make_completed_process(GH_TWO_REPOS)):
        entries = await sync_org_repos(db, org="test-org")

    assert len(entries) == 2
    names = {e.repo_full_name for e in entries}
    assert "test-org/alpha-tool" in names
    assert "test-org/beta-scanner" in names


@pytest.mark.asyncio
async def test_sync_skips_repos_without_description(db: Database):
    from project_forge.engine.repo_registry import sync_org_repos

    with patch("subprocess.run", return_value=_make_completed_process(GH_WITH_EMPTY_DESC)):
        entries = await sync_org_repos(db, org="test-org")

    assert len(entries) == 1
    assert entries[0].repo_full_name == "test-org/alpha-tool"


@pytest.mark.asyncio
async def test_sync_uses_correct_org(db: Database):
    from project_forge.engine.repo_registry import sync_org_repos

    with patch("subprocess.run", return_value=_make_completed_process(GH_TWO_REPOS)) as mock_run:
        await sync_org_repos(db, org="my-custom-org")

    call_args = mock_run.call_args
    cmd = call_args.args[0]
    assert "my-custom-org" in cmd


@pytest.mark.asyncio
async def test_sync_raises_on_gh_failure(db: Database):
    from project_forge.engine.repo_registry import sync_org_repos

    with patch(
        "subprocess.run",
        return_value=_make_completed_process("", returncode=1, stderr="authentication required"),
    ):
        with pytest.raises(RuntimeError, match="gh repo list failed"):
            await sync_org_repos(db, org="test-org")


@pytest.mark.asyncio
async def test_sync_persists_entries_to_db(db: Database):
    """Synced repos should be queryable from the database afterward."""
    from project_forge.engine.repo_registry import sync_org_repos

    with patch("subprocess.run", return_value=_make_completed_process(GH_TWO_REPOS)):
        await sync_org_repos(db, org="test-org")

    stored = await db.list_repo_registry()
    assert len(stored) == 2


@pytest.mark.asyncio
async def test_sync_extracts_topics(db: Database):
    """Topics from repositoryTopics should be extracted into RepoEntry."""
    from project_forge.engine.repo_registry import sync_org_repos

    with patch("subprocess.run", return_value=_make_completed_process(GH_TWO_REPOS)):
        entries = await sync_org_repos(db, org="test-org")

    alpha = next(e for e in entries if "alpha-tool" in e.repo_full_name)
    assert "pki" in alpha.topics
    assert "security" in alpha.topics
