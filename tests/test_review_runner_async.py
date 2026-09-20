"""TDD: review_runner._review_idea_with_api must not block the event loop.

Bug (#69): the function used a synchronous Anthropic client and called
`client.messages.create()` without awaiting — the HTTP call blocked the
event loop for the duration. Fixed by moving to the generic BYO-LLM backend
and running the (synchronous) `backend.call(prompt)` off the event loop via
`asyncio.to_thread`.

Two layers of regression coverage:
1. Static check: the function source DOES offload the blocking call with
   `asyncio.to_thread` and does not reference any synchronous vendor client.
2. Behavior check: a fake backend's `.call()` is invoked (via to_thread) and
   its JSON parsed into a verdict.
"""

from __future__ import annotations

import inspect
import json
from unittest.mock import MagicMock

import pytest

from project_forge.cron import review_runner
from project_forge.models import Idea, IdeaCategory

# ── Static source check ─────────────────────────────────────────────


class TestSourceOffloadsBlockingCall:
    def test_function_is_async(self):
        assert inspect.iscoroutinefunction(review_runner._review_idea_with_api), (
            "_review_idea_with_api must remain async"
        )

    def test_does_not_reference_vendor_sync_client(self):
        src = inspect.getsource(review_runner._review_idea_with_api)
        assert "anthropic" not in src and "openai" not in src, "Function must not reference a vendor-specific client."

    def test_offloads_backend_call_with_to_thread(self):
        src = inspect.getsource(review_runner._review_idea_with_api)
        assert "asyncio.to_thread" in src, (
            "The (synchronous) backend.call() must be off-loaded via "
            "asyncio.to_thread so it doesn't block the event loop."
        )
        assert "backend.call" in src, "The generic BYO-LLM backend's .call() must be used."


# ── Behavior check (mocked backend) ─────────────────────────────────


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
async def test_review_calls_backend_and_parses_verdict():
    """Calling _review_idea_with_api with a fake backend must invoke its
    .call() (off the event loop) and parse the returned JSON verdict."""
    fake_backend = MagicMock()
    fake_backend.name = "openai-compatible:qwen-local-m"
    fake_backend.call.return_value = '{"verdict": "keep", "confidence": 0.7, "reasoning": "ok", "suggestions": []}'

    result = await review_runner._review_idea_with_api(_stub_idea(), backend=fake_backend)

    fake_backend.call.assert_called_once()
    assert result["verdict"] == "keep"
    assert result["confidence"] == 0.7


@pytest.mark.asyncio
async def test_review_propagates_empty_response_as_json_error():
    """A backend returning None produces empty raw -> json.loads('') raises.
    Callers catching the exception (run_review_cycle) record an error row."""
    fake_backend = MagicMock()
    fake_backend.call.return_value = None

    with pytest.raises(json.JSONDecodeError):
        await review_runner._review_idea_with_api(_stub_idea(), backend=fake_backend)
