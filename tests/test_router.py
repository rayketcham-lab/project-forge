"""Tests for the portfolio router."""

from unittest.mock import MagicMock

import pytest

from project_forge.models import Idea, IdeaCategory, RepoEntry


def _make_idea(**kwargs) -> Idea:
    defaults = {
        "name": "Test Idea",
        "tagline": "A novel security idea",
        "description": "This is a test idea for portfolio routing.",
        "category": IdeaCategory.SECURITY_TOOL,
        "market_analysis": "Strong market demand.",
        "feasibility_score": 0.8,
        "mvp_scope": "CLI tool that does X.",
        "tech_stack": ["python"],
    }
    defaults.update(kwargs)
    return Idea(**defaults)


def _make_repo(repo_full_name: str, description: str, topics: list[str] | None = None) -> RepoEntry:
    return RepoEntry(
        repo_full_name=repo_full_name,
        description=description,
        topics=topics or [],
    )


def _mock_client_returning(payload: dict) -> MagicMock:
    """Build a mock Anthropic client that returns the given dict as text."""
    import json

    mock_client = MagicMock()
    mock_response = MagicMock()
    mock_response.content = [MagicMock(text=json.dumps(payload))]
    mock_client.messages.create.return_value = mock_response
    return mock_client


# ─── Tests ────────────────────────────────────────────────────────────────────


def test_route_returns_new_project_when_registry_empty():
    from project_forge.engine.router import PortfolioRouter

    client = MagicMock()
    router = PortfolioRouter(client, "claude-test")
    idea = _make_idea()

    decision = router.route(idea, repos=[])

    assert decision.action == "new_project"
    assert decision.target_repo is None
    assert "empty" in decision.reason.lower()
    # Should NOT call the API when registry is empty
    client.messages.create.assert_not_called()


def test_route_contribute_decision():
    from project_forge.engine.router import PortfolioRouter, RouteDecision

    client = _mock_client_returning(
        {
            "action": "contribute",
            "target_repo": "owner/repo",
            "reason": "fits existing work",
            "confidence": 0.9,
        }
    )
    router = PortfolioRouter(client, "claude-test")
    idea = _make_idea()
    repos = [_make_repo("owner/repo", "An existing security tool")]

    decision = router.route(idea, repos=repos)

    assert isinstance(decision, RouteDecision)
    assert decision.action == "contribute"
    assert decision.target_repo == "owner/repo"
    assert decision.reason == "fits existing work"
    assert decision.confidence == pytest.approx(0.9)


def test_route_discard_decision():
    from project_forge.engine.router import PortfolioRouter

    client = _mock_client_returning(
        {
            "action": "discard",
            "target_repo": None,
            "reason": "already covered by existing repo",
            "confidence": 0.95,
        }
    )
    router = PortfolioRouter(client, "claude-test")
    idea = _make_idea()
    repos = [_make_repo("owner/existing-tool", "Does exactly this already")]

    decision = router.route(idea, repos=repos)

    assert decision.action == "discard"
    assert decision.target_repo is None
    assert decision.confidence == pytest.approx(0.95)


def test_route_new_project_decision():
    from project_forge.engine.router import PortfolioRouter

    client = _mock_client_returning(
        {
            "action": "new_project",
            "target_repo": None,
            "reason": "genuinely novel territory",
            "confidence": 0.85,
        }
    )
    router = PortfolioRouter(client, "claude-test")
    idea = _make_idea()
    repos = [_make_repo("owner/other-tool", "Completely different scope")]

    decision = router.route(idea, repos=repos)

    assert decision.action == "new_project"
    assert decision.target_repo is None


def test_parse_handles_json_fences():
    """Router must strip ```json...``` fences from Claude response."""
    from project_forge.engine.router import PortfolioRouter

    mock_client = MagicMock()
    mock_response = MagicMock()
    json_block = (
        '```json\n{"action": "new_project", "target_repo": null, "reason": "novel idea", "confidence": 0.7}\n```'
    )
    mock_response.content = [MagicMock(text=json_block)]
    mock_client.messages.create.return_value = mock_response

    router = PortfolioRouter(mock_client, "claude-test")
    idea = _make_idea()
    repos = [_make_repo("owner/repo", "Some repo")]

    decision = router.route(idea, repos=repos)

    assert decision.action == "new_project"
    assert decision.confidence == pytest.approx(0.7)


def test_parse_handles_invalid_json_gracefully():
    """Malformed JSON → defaults to new_project with confidence 0.0."""
    from project_forge.engine.router import PortfolioRouter

    mock_client = MagicMock()
    mock_response = MagicMock()
    mock_response.content = [MagicMock(text="this is not valid json at all")]
    mock_client.messages.create.return_value = mock_response

    router = PortfolioRouter(mock_client, "claude-test")
    idea = _make_idea()
    repos = [_make_repo("owner/repo", "Some repo")]

    decision = router.route(idea, repos=repos)

    assert decision.action == "new_project"
    assert decision.confidence == pytest.approx(0.0)


def test_parse_handles_invalid_action_gracefully():
    """Valid JSON but unknown action → defaults to new_project."""
    from project_forge.engine.router import PortfolioRouter

    client = _mock_client_returning(
        {
            "action": "invalid_unknown_action",
            "target_repo": None,
            "reason": "some reason",
            "confidence": 0.5,
        }
    )
    router = PortfolioRouter(client, "claude-test")
    idea = _make_idea()
    repos = [_make_repo("owner/repo", "Some repo")]

    decision = router.route(idea, repos=repos)

    assert decision.action == "new_project"


def test_route_builds_correct_repo_lines():
    """Verify the prompt sent to the API contains all repo descriptions."""
    from project_forge.engine.router import PortfolioRouter

    mock_client = MagicMock()
    mock_response = MagicMock()
    import json

    mock_response.content = [
        MagicMock(text=json.dumps({"action": "new_project", "target_repo": None, "reason": "novel", "confidence": 0.8}))
    ]
    mock_client.messages.create.return_value = mock_response

    router = PortfolioRouter(mock_client, "claude-test")
    idea = _make_idea()
    repos = [
        _make_repo("owner/alpha-tool", "Alpha tool for PKI management"),
        _make_repo("owner/beta-scanner", "Beta vulnerability scanner"),
    ]

    router.route(idea, repos=repos)

    call_kwargs = mock_client.messages.create.call_args
    # Extract the messages list from call kwargs
    messages = call_kwargs.kwargs.get("messages") or call_kwargs.args[0] if call_kwargs.args else None
    if messages is None:
        messages = call_kwargs.kwargs["messages"]

    # The prompt content should contain all repo identifiers/descriptions
    prompt_text = messages[0]["content"]
    assert "owner/alpha-tool" in prompt_text
    assert "Alpha tool for PKI management" in prompt_text
    assert "owner/beta-scanner" in prompt_text
    assert "Beta vulnerability scanner" in prompt_text
