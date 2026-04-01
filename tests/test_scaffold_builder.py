"""Tests for project scaffold builder."""

import re

from project_forge.models import Idea, IdeaCategory
from project_forge.scaffold.builder import build_scaffold_spec, render_scaffold, sanitize_repo_name


def _make_idea(**kwargs) -> Idea:
    defaults = {
        "name": "Ghost Keys",
        "tagline": "Detect orphaned API keys",
        "description": "A tool for finding unused API keys.",
        "category": IdeaCategory.SECURITY_TOOL,
        "market_analysis": "Growing market.",
        "feasibility_score": 0.8,
        "mvp_scope": "CLI scanner.",
        "tech_stack": ["python", "fastapi", "sqlite"],
    }
    defaults.update(kwargs)
    return Idea(**defaults)


class TestSanitizeRepoName:
    def test_basic(self):
        assert sanitize_repo_name("Ghost Keys") == "ghost-keys"

    def test_special_chars(self):
        assert sanitize_repo_name("My Cool Tool!@#") == "my-cool-tool"

    def test_multiple_dashes(self):
        assert sanitize_repo_name("a - - b") == "a-b"

    def test_truncate_long_name(self):
        result = sanitize_repo_name("x" * 200)
        assert len(result) <= 100


    def test_sanitize_repo_name_all_special_chars(self):
        result = sanitize_repo_name("!@#$%^&*()")
        assert result != "", "All-special-char input must not produce an empty repo name"
        assert re.match(r"^[a-z0-9][a-z0-9\-]*$", result), f"Result '{result}' contains invalid chars"

    def test_sanitize_repo_name_unicode(self):
        result = sanitize_repo_name("Idée de Sécurité")
        assert re.match(r"^[a-z0-9\-]+$", result), f"Result '{result}' contains non-[a-z0-9-] chars"
        assert result != "", "Unicode input must not produce an empty repo name"

    def test_sanitize_repo_name_numeric(self):
        result = sanitize_repo_name("12345")
        assert result == "12345"

    def test_sanitize_repo_name_empty_string(self):
        result = sanitize_repo_name("")
        assert result != "", "Empty string input must not produce an empty repo name"
        assert re.match(r"^[a-z0-9][a-z0-9\-]*$", result), f"Result '{result}' contains invalid chars"


class TestBuildScaffoldSpec:
    def test_python_default(self):
        idea = _make_idea()
        spec = build_scaffold_spec(idea)
        assert spec.language == "python"
        assert spec.repo_name == "ghost-keys"
        assert len(spec.initial_issues) == 4

    def test_node_detection(self):
        idea = _make_idea(tech_stack=["node", "express", "mongodb"])
        spec = build_scaffold_spec(idea)
        assert spec.language == "node"
        assert spec.framework == "express"

    def test_rust_detection(self):
        idea = _make_idea(tech_stack=["rust", "tokio", "actix"])
        spec = build_scaffold_spec(idea)
        assert spec.language == "rust"
        assert spec.framework == "actix"

    def test_go_detection(self):
        idea = _make_idea(tech_stack=["golang", "gin"])
        spec = build_scaffold_spec(idea)
        assert spec.language == "go"
        assert spec.framework == "gin"


class TestRenderScaffold:
    def test_render_python_project(self, tmp_path):
        idea = _make_idea()
        spec = build_scaffold_spec(idea)
        project_dir = render_scaffold(spec, idea, tmp_path)

        assert (project_dir / "README.md").exists()
        assert "Ghost Keys" in (project_dir / "README.md").read_text()
        assert (project_dir / ".github" / "workflows" / "ci.yml").exists()
        assert (project_dir / "pyproject.toml").exists()
        assert (project_dir / "src" / "ghost_keys" / "__init__.py").exists()
        assert (project_dir / "tests" / "__init__.py").exists()
        assert (project_dir / ".gitignore").exists()

    def test_render_node_project(self, tmp_path):
        idea = _make_idea(tech_stack=["node", "express"])
        spec = build_scaffold_spec(idea)
        project_dir = render_scaffold(spec, idea, tmp_path)

        assert (project_dir / "package.json").exists()
        assert "ghost-keys" in (project_dir / "package.json").read_text()
        assert (project_dir / "src" / "index.js").exists()

    def test_ci_contains_language_config(self, tmp_path):
        idea = _make_idea()
        spec = build_scaffold_spec(idea)
        project_dir = render_scaffold(spec, idea, tmp_path)

        ci = (project_dir / ".github" / "workflows" / "ci.yml").read_text()
        assert "ruff" in ci  # python-specific
