"""TDD: review_runner._review_idea_with_api must use AsyncAnthropic (#69).

Bug: the function is declared `async def` but uses the synchronous
`anthropic.Anthropic` client and calls `client.messages.create()`
without `await`. HTTP request blocks the event loop for the duration
of the Anthropic call. Sonnet flagged this in two consecutive
autonomous introspect cycles — convergence is the quality signal.

Two layers of regression coverage:
1. Static check: the function source does NOT reference the sync client
   class and DOES await the response.
2. Behavior check: mocked AsyncAnthropic is invoked; sync class is not.
"""

from __future__ import annotations

import inspect
import re
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from project_forge.cron import review_runner
from project_forge.models import Idea, IdeaCategory

# ── Static source check ─────────────────────────────────────────────


class TestSourceUsesAsyncClient:
    def test_function_is_async(self):
        assert inspect.iscoroutinefunction(review_runner._review_idea_with_api), (
            "_review_idea_with_api must remain async"
        )

    def test_does_not_use_sync_client(self):
        src = inspect.getsource(review_runner._review_idea_with_api)
        # Sync client looks like: anthropic.Anthropic(  (NOT AsyncAnthropic)
        # The negative lookbehind avoids matching AsyncAnthropic itself.
        sync_call = re.search(r"(?<!Async)\banthropic\.Anthropic\s*\(", src)
        assert sync_call is None, (
            "Function must not instantiate the sync anthropic.Anthropic "
            "client — it blocks the event loop. Use AsyncAnthropic."
        )

    def test_uses_async_anthropic(self):
        src = inspect.getsource(review_runner._review_idea_with_api)
        assert re.search(r"anthropic\.AsyncAnthropic\s*\(", src), (
            "Function must use anthropic.AsyncAnthropic in an async context."
        )

    def test_awaits_messages_create(self):
        src = inspect.getsource(review_runner._review_idea_with_api)
        # Must have `await client.messages.create` (or `await self.client.…`)
        assert "await client.messages.create" in src or "await self.client.messages.create" in src, (
            "messages.create() must be awaited."
        )
        # And no bare unsuffixed call
        assert not re.search(r"^\s*resp\s*=\s*client\.messages\.create", src, re.MULTILINE), (
            "Found a non-awaited messages.create — would block the event loop."
        )


# ── Behavior check (mocked) ─────────────────────────────────────────


def _stub_idea() -> Idea:
    return Idea(
        name="Test Idea",
        tagline="t",
        description="d",
        category=IdeaCategory.SECURITY_TOOL,
        market_analysis="m",
        feasibility_score=0.7,
        mvp_scope="s",
        tech_stack=["python"],
    )


@pytest.mark.asyncio
async def test_review_calls_async_client_not_sync():
    """Calling _review_idea_with_api must instantiate AsyncAnthropic
    (not Anthropic) and await its messages.create."""
    fake_resp = MagicMock()
    fake_resp.content = [MagicMock(text='{"verdict": "keep", "confidence": 0.7, "reasoning": "ok", "suggestions": []}')]

    fake_async_client = MagicMock()
    fake_async_client.messages.create = AsyncMock(return_value=fake_resp)
    fake_async_anthropic_class = MagicMock(return_value=fake_async_client)

    fake_sync_anthropic_class = MagicMock(
        side_effect=AssertionError(
            "_review_idea_with_api called the SYNC anthropic.Anthropic class. It must use AsyncAnthropic.",
        ),
    )

    fake_module = MagicMock()
    fake_module.AsyncAnthropic = fake_async_anthropic_class
    fake_module.Anthropic = fake_sync_anthropic_class

    with patch.dict("sys.modules", {"anthropic": fake_module}):
        result = await review_runner._review_idea_with_api(
            _stub_idea(),
            api_key="sk-test",
            model="claude-sonnet-4-6",
        )

    assert fake_async_anthropic_class.called, "AsyncAnthropic was never called"
    assert fake_async_client.messages.create.await_count == 1, "messages.create must be awaited exactly once"
    assert result["verdict"] == "keep"
