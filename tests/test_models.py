"""Tests for core data models."""

import pytest
from pydantic import ValidationError

from project_forge.models import GenerationRun, Idea, IdeaCategory, ScaffoldSpec


class TestIdea:
    def test_create_idea_with_defaults(self):
        idea = Idea(
            name="Test Tool",
            tagline="A test security tool",
            description="This tool does testing things.",
            category=IdeaCategory.SECURITY_TOOL,
            market_analysis="Big market gap here.",
            feasibility_score=0.8,
            mvp_scope="Build a CLI that scans for X.",
            tech_stack=["python", "fastapi"],
        )
        assert idea.id  # auto-generated
        assert idea.status == "new"
        assert idea.generated_at is not None
        assert idea.github_issue_url is None
        assert idea.project_repo_url is None

    def test_idea_feasibility_score_bounds(self):
        with pytest.raises(ValidationError):
            Idea(
                name="Bad",
                tagline="Bad",
                description="Bad",
                category=IdeaCategory.AUTOMATION,
                market_analysis="None",
                feasibility_score=1.5,
                mvp_scope="None",
            )

    def test_idea_feasibility_score_lower_bound(self):
        with pytest.raises(ValidationError):
            Idea(
                name="Bad",
                tagline="Bad",
                description="Bad",
                category=IdeaCategory.AUTOMATION,
                market_analysis="None",
                feasibility_score=-0.1,
                mvp_scope="None",
            )

    def test_all_categories_exist(self):
        assert len(IdeaCategory) == 13
        expected = {
            "security-tool",
            "market-gap",
            "vulnerability-research",
            "automation",
            "devops-tooling",
            "privacy",
            "compliance",
            "observability",
            "pqc-cryptography",
            "nist-standards",
            "rfc-security",
            "crypto-infrastructure",
            "self-improvement",
        }
        assert {c.value for c in IdeaCategory} == expected

    def test_idea_serialization_roundtrip(self):
        idea = Idea(
            name="Roundtrip",
            tagline="Test roundtrip",
            description="Testing JSON roundtrip.",
            category=IdeaCategory.PRIVACY,
            market_analysis="Solid market.",
            feasibility_score=0.65,
            mvp_scope="MVP scope here.",
            tech_stack=["rust", "wasm"],
        )
        data = idea.model_dump()
        restored = Idea(**data)
        assert restored.name == idea.name
        assert restored.feasibility_score == idea.feasibility_score
        assert restored.tech_stack == idea.tech_stack


class TestScaffoldSpec:
    def test_create_scaffold_spec(self):
        spec = ScaffoldSpec(
            idea_id="abc123",
            repo_name="cool-project",
            language="python",
            framework="fastapi",
        )
        assert spec.features == ["ci", "tests", "readme"]
        assert spec.initial_issues == []

    def test_scaffold_spec_languages(self):
        for lang in ["python", "node", "rust", "go"]:
            spec = ScaffoldSpec(idea_id="x", repo_name="y", language=lang)
            assert spec.language == lang

    def test_scaffold_spec_invalid_language(self):
        with pytest.raises(ValidationError):
            ScaffoldSpec(idea_id="x", repo_name="y", language="cobol")


class TestGenerationRun:
    def test_create_run(self):
        run = GenerationRun(category=IdeaCategory.DEVOPS_TOOLING)
        assert run.id
        assert run.success is False
        assert run.idea_id is None
        assert run.completed_at is None
