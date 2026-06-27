"""TDD: external_seeds plumbed into build_generation_prompt + IdeaGenerator.

Wire the fetched NVD/arXiv/IETF items into Claude's prompt as fresh
seed material — the third escape hatch from data-poverty (alongside
filter_summary and reasoning).
"""

from __future__ import annotations

import asyncio

from project_forge.engine.prompts import build_generation_prompt
from project_forge.models import IdeaCategory


def _baseline():
    return {"category": IdeaCategory.SECURITY_TOOL, "recent_ideas": []}


class TestExternalSeedsInPrompt:
    def test_default_off_no_external_seeds_section(self):
        prompt = build_generation_prompt(**_baseline())
        assert "EXTERNAL SIGNALS" not in prompt
        assert "FRESH SEEDS" not in prompt

    def test_external_seeds_none_no_section(self):
        prompt = build_generation_prompt(**_baseline(), external_seeds=None)
        assert "EXTERNAL SIGNALS" not in prompt

    def test_external_seeds_empty_list_no_section(self):
        prompt = build_generation_prompt(**_baseline(), external_seeds=[])
        assert "EXTERNAL SIGNALS" not in prompt

    def test_external_seeds_populated_section(self):
        seeds = [
            {"id": "CVE-2026-9000", "title": "RCE", "summary": "RCE in libfoo", "url": "u", "ts": "t"},
            {"id": "arxiv:1", "title": "Side Channels", "summary": "ct ops in TLS", "url": "u", "ts": "t"},
        ]
        prompt = build_generation_prompt(**_baseline(), external_seeds=seeds)
        assert "EXTERNAL SIGNALS" in prompt or "FRESH SEEDS" in prompt
        assert "CVE-2026-9000" in prompt
        assert "Side Channels" in prompt

    def test_external_seeds_capped_in_prompt(self):
        seeds = [{"id": f"x{i}", "title": "t", "summary": "s", "url": "u", "ts": "t"} for i in range(20)]
        prompt = build_generation_prompt(**_baseline(), external_seeds=seeds)
        # We bound the prompt by render limit (5 default for format_for_prompt)
        # so we don't drown the LLM in CVEs
        appearances = sum(1 for i in range(20) if f"x{i}" in prompt)
        assert appearances <= 6


class TestIdeaGeneratorForwarding:
    def test_generate_forwards_external_seeds(self, monkeypatch):
        from project_forge.engine.generator import IdeaGenerator

        captured = {}

        _FAKE_JSON = (
            '{"name":"X","tagline":"t","description":"d","category":"security-tool",'
            '"market_analysis":"m","feasibility_score":0.8,"mvp_scope":"mvp",'
            '"tech_stack":["py"]}'
        )

        class _StubMessages:
            def create(self, **kwargs):  # noqa: ARG002
                class _Resp:
                    content = [type("X", (), {"text": _FAKE_JSON})]

                return _Resp()

        class _StubClient:
            messages = _StubMessages()

        def stub_build_prompt(**kwargs):
            captured.update(kwargs)
            return "PROMPT"

        gen = IdeaGenerator.__new__(IdeaGenerator)
        gen.client = _StubClient()
        gen.model = "stub-model"
        monkeypatch.setattr(
            "project_forge.engine.generator.build_generation_prompt",
            stub_build_prompt,
        )

        seeds = [{"id": "CVE-1", "title": "x", "summary": "y", "url": "u", "ts": "t"}]
        asyncio.run(
            gen.generate(
                category=IdeaCategory.SECURITY_TOOL,
                external_seeds=seeds,
            )
        )

        assert captured.get("external_seeds") == seeds
