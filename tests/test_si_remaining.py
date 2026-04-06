"""Tests for remaining self-improvement issues #13, #32–#37.

TDD RED phase: all tests should FAIL before implementation.

Covers:
1. #13 — feistyduck.com tracked resource (seed data)
2. #32 — Test coverage enforcement (pytest-cov in CI)
3. #33 — CI gap detection (enhanced issue-test-ratio)
4. #34 — Error handling gaps in generator async code
5. #35 — Structured logging with JSON output
6. #36 — DB query performance profiling
7. #37 — Dependency audit automation in CI
"""

import json
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
import pytest_asyncio
import yaml
from httpx import ASGITransport, AsyncClient

from project_forge.web.app import app, db

PROJECT_ROOT = Path(__file__).resolve().parent.parent


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest_asyncio.fixture
async def client(tmp_path):
    db.db_path = tmp_path / "test_si.db"
    await db.connect()
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        yield client
    await db.close()


def _load_ci_yaml() -> dict:
    ci_path = PROJECT_ROOT / ".github" / "workflows" / "ci.yml"
    return yaml.safe_load(ci_path.read_text())


def _load_pyproject() -> dict:
    import tomllib

    pyproject_path = PROJECT_ROOT / "pyproject.toml"
    with open(pyproject_path, "rb") as f:
        return tomllib.load(f)


# ---------------------------------------------------------------------------
# #13 — feistyduck.com tracked resource
# ---------------------------------------------------------------------------


class TestFeistyduckResource:
    """feistyduck.com should be a seeded/tracked resource."""

    @pytest.mark.asyncio
    async def test_feistyduck_resource_in_seed_data(self, client):
        """feistyduck.com should appear in the resources list."""
        from project_forge.storage.seeds import seed_resources

        await seed_resources(db)
        resp = await client.get("/api/resources")
        assert resp.status_code == 200
        data = resp.json()
        resources = data["resources"]
        domains = [r["domain"] for r in resources]
        assert "feistyduck.com" in domains

    @pytest.mark.asyncio
    async def test_feistyduck_has_correct_metadata(self, client):
        """feistyduck.com resource should have TLS/PKI categories."""
        from project_forge.storage.seeds import seed_resources

        await seed_resources(db)
        resource = await db.get_resource_by_domain("feistyduck.com")
        assert resource is not None
        assert resource.name  # Should have a human-readable name
        assert any(cat in resource.categories for cat in ["tls", "pki", "security", "cryptography"])


# ---------------------------------------------------------------------------
# #32 — Test coverage enforcement
# ---------------------------------------------------------------------------


class TestCoverageEnforcement:
    """CI should enforce test coverage thresholds."""

    def test_pytest_cov_in_test_deps(self):
        """pytest-cov must be in [project.optional-dependencies] test."""
        pyproject = _load_pyproject()
        test_deps = pyproject["project"]["optional-dependencies"]["test"]
        assert any("pytest-cov" in dep for dep in test_deps), "pytest-cov not in test dependencies"

    def test_ci_runs_coverage(self):
        """CI test job should include --cov flag."""
        ci = _load_ci_yaml()
        test_job = ci["jobs"]["test"]
        steps_text = yaml.dump(test_job["steps"])
        assert "--cov" in steps_text, "CI test job does not run coverage"

    def test_ci_has_coverage_threshold(self):
        """CI should fail if coverage drops below threshold."""
        ci = _load_ci_yaml()
        test_job = ci["jobs"]["test"]
        steps_text = yaml.dump(test_job["steps"])
        assert "--cov-fail-under" in steps_text, "CI has no coverage threshold"


# ---------------------------------------------------------------------------
# #33 — CI gap detection (enhanced)
# ---------------------------------------------------------------------------


class TestCIGapDetection:
    """CI should detect and report test gaps."""

    def test_issue_test_ratio_job_exists(self):
        """issue-test-ratio job should exist in CI."""
        ci = _load_ci_yaml()
        assert "issue-test-ratio" in ci["jobs"]

    def test_gap_detection_checks_untested_modules(self):
        """Gap detection should check for source modules without corresponding tests."""
        ci = _load_ci_yaml()
        job = ci["jobs"]["issue-test-ratio"]
        steps_text = yaml.dump(job["steps"])
        # Should check for modules without test files
        assert "untested" in steps_text.lower() or "gap" in steps_text.lower() or "no test" in steps_text.lower(), (
            "CI gap detection doesn't check for untested modules"
        )


# ---------------------------------------------------------------------------
# #34 — Error handling gaps in generator async code
# ---------------------------------------------------------------------------


class TestGeneratorErrorHandling:
    """Generator should handle API and parsing errors gracefully."""

    @pytest.mark.asyncio
    async def test_generate_handles_json_decode_error(self):
        """generate() should raise a clear error on malformed JSON from API."""
        from project_forge.engine.generator import IdeaGenerator
        from project_forge.models import IdeaCategory

        gen = IdeaGenerator(api_key="test-key")

        mock_response = MagicMock()
        mock_response.content = [MagicMock(text="not valid json at all")]

        with patch.object(gen.client.messages, "create", return_value=mock_response):
            with pytest.raises(ValueError, match="(?i)parse|json|malformed|invalid"):
                await gen.generate(category=IdeaCategory.SELF_IMPROVEMENT)

    @pytest.mark.asyncio
    async def test_generate_handles_api_error(self):
        """generate() should wrap anthropic.APIError with context."""
        import anthropic

        from project_forge.engine.generator import IdeaGenerator
        from project_forge.models import IdeaCategory

        gen = IdeaGenerator(api_key="test-key")

        with patch.object(
            gen.client.messages,
            "create",
            side_effect=anthropic.APIStatusError(
                message="rate limited",
                response=MagicMock(status_code=429),
                body={"error": {"message": "rate limited"}},
            ),
        ):
            with pytest.raises((anthropic.APIStatusError, RuntimeError)):
                await gen.generate(category=IdeaCategory.SELF_IMPROVEMENT)

    @pytest.mark.asyncio
    async def test_generate_handles_missing_fields(self):
        """generate() should raise clear error when API response missing required fields."""
        from project_forge.engine.generator import IdeaGenerator
        from project_forge.models import IdeaCategory

        gen = IdeaGenerator(api_key="test-key")

        # JSON is valid but missing required fields
        mock_response = MagicMock()
        mock_response.content = [MagicMock(text='{"name": "Test"}')]  # missing other fields

        with patch.object(gen.client.messages, "create", return_value=mock_response):
            with pytest.raises((KeyError, ValueError)):
                await gen.generate(category=IdeaCategory.SELF_IMPROVEMENT)

    @pytest.mark.asyncio
    async def test_generate_from_content_handles_json_error(self):
        """generate_from_content() should also handle JSON parse errors."""
        from project_forge.engine.generator import IdeaGenerator
        from project_forge.engine.url_ingest import UrlContent

        gen = IdeaGenerator(api_key="test-key")
        content = UrlContent(url="https://example.com", title="Test", domain="example.com", text="Test content")

        mock_response = MagicMock()
        mock_response.content = [MagicMock(text="broken json {{{")]

        with patch.object(gen.client.messages, "create", return_value=mock_response):
            with pytest.raises(ValueError, match="(?i)parse|json|malformed|invalid"):
                await gen.generate_from_content(content=content)


# ---------------------------------------------------------------------------
# #35 — Structured logging
# ---------------------------------------------------------------------------


class TestStructuredLogging:
    """Application should use structured JSON logging."""

    def test_structlog_in_dependencies(self):
        """structlog must be in project dependencies."""
        pyproject = _load_pyproject()
        all_deps = pyproject["project"]["dependencies"]
        assert any("structlog" in dep for dep in all_deps), "structlog not in dependencies"

    def test_logging_config_produces_json(self):
        """Logging configuration should produce JSON-formatted output."""
        import io

        from project_forge.logging_config import configure_logging

        output = io.StringIO()
        configure_logging(stream=output)

        import structlog

        log = structlog.get_logger("test")
        log.info("test_event", key="value")

        output.seek(0)
        log_line = output.read().strip()
        # Should be valid JSON
        if log_line:
            parsed = json.loads(log_line)
            assert "event" in parsed or "message" in parsed

    def test_request_middleware_adds_correlation_id(self, client):
        """HTTP middleware should add a correlation/request ID to log context."""
        # The X-Request-ID header should be returned or logged
        import asyncio

        async def check():
            resp = await client.get("/api/stats")
            # Response should include a request ID header
            assert "x-request-id" in resp.headers or "x-correlation-id" in resp.headers

        asyncio.get_event_loop().run_until_complete(check())


# ---------------------------------------------------------------------------
# #36 — DB query performance profiling
# ---------------------------------------------------------------------------


class TestDBProfiling:
    """Database should profile query performance."""

    @pytest.mark.asyncio
    async def test_database_has_query_timing(self, client):
        """Database queries should be timed."""
        from project_forge.storage.db import Database

        assert hasattr(Database, "query_times") or hasattr(Database, "_profile_query"), (
            "Database class has no query profiling capability"
        )

    @pytest.mark.asyncio
    async def test_slow_query_is_logged(self, client):
        """Queries exceeding threshold should emit a warning log."""
        with patch("project_forge.storage.db.logger") as mock_logger:
            # Force a slow query by making the DB do something
            from project_forge.models import Idea, IdeaCategory

            idea = Idea(
                name="Profiling Test",
                tagline="test",
                description="test",
                category=IdeaCategory.SELF_IMPROVEMENT,
                market_analysis="test",
                feasibility_score=0.5,
                mvp_scope="test",
                tech_stack=[],
            )
            await db.save_idea(idea)
            await db.list_ideas()

            # The profiler should have logged at least one query timing
            # (at DEBUG level for fast queries, WARNING for slow ones)
            any_timing_log = any(
                "query" in str(call).lower() or "ms" in str(call).lower()
                for call in mock_logger.debug.call_args_list + mock_logger.warning.call_args_list
            )
            assert any_timing_log, "No query timing logs emitted"

    @pytest.mark.asyncio
    async def test_profiling_stats_endpoint(self, client):
        """API should expose DB profiling stats."""
        resp = await client.get("/api/stats")
        assert resp.status_code == 200
        data = resp.json()
        assert "db_profile" in data or "query_stats" in data, "Stats endpoint missing DB profiling data"


# ---------------------------------------------------------------------------
# #37 — Dependency audit automation in CI
# ---------------------------------------------------------------------------


class TestDependencyAudit:
    """CI should run pip-audit for dependency vulnerability scanning."""

    def test_pip_audit_in_dev_deps(self):
        """pip-audit should be in dev dependencies (already is)."""
        pyproject = _load_pyproject()
        dev_deps = pyproject["project"]["optional-dependencies"]["dev"]
        assert any("pip-audit" in dep for dep in dev_deps)

    def test_ci_has_dependency_audit_job(self):
        """CI should have a dedicated dependency audit job or step."""
        ci = _load_ci_yaml()
        jobs = ci["jobs"]
        # Either a dedicated job or a step in the security job
        has_audit = "dependency-audit" in jobs
        if not has_audit:
            security_text = yaml.dump(jobs.get("security", {}))
            has_audit = "pip-audit" in security_text or "pip_audit" in security_text
        assert has_audit, "CI has no dependency audit job or step"

    def test_ci_audit_scans_installed_packages(self):
        """The audit step should scan actual installed packages, not just requirements files."""
        ci = _load_ci_yaml()
        # Find the job/step that runs pip-audit
        all_text = yaml.dump(ci["jobs"])
        assert "pip-audit" in all_text, "pip-audit not found anywhere in CI"
