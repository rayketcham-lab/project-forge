"""Tests for URL-based idea ingestion and resource tracking.

TDD RED phase: All tests written before implementation.
Feature: Generate project ideas from URLs + track source domains as resources.
"""

import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
import pytest_asyncio

from project_forge.models import Idea, IdeaCategory

# === MODEL TESTS ===


class TestResourceModel:
    """Resource model for tracking idea sources."""

    def test_resource_has_required_fields(self):
        from project_forge.models import Resource

        r = Resource(
            domain="feistyduck.com",
            name="Feisty Duck",
            description="PKI, TLS, and web security newsletter",
        )
        assert r.domain == "feistyduck.com"
        assert r.name == "Feisty Duck"
        assert r.description == "PKI, TLS, and web security newsletter"
        assert r.id  # auto-generated
        assert r.added_at  # auto-generated

    def test_resource_has_optional_fields(self):
        from project_forge.models import Resource

        r = Resource(
            domain="feistyduck.com",
            name="Feisty Duck",
            description="Security newsletter",
            url="https://www.feistyduck.com",
            categories=["crypto-infrastructure", "rfc-security"],
        )
        assert r.url == "https://www.feistyduck.com"
        assert r.categories == ["crypto-infrastructure", "rfc-security"]

    def test_resource_defaults(self):
        from project_forge.models import Resource

        r = Resource(
            domain="example.com",
            name="Example",
            description="Test resource",
        )
        assert r.url is None
        assert r.categories == []
        assert r.idea_count == 0


class TestUrlIngestRequest:
    """Request model for URL ingestion."""

    def test_ingest_request_has_url(self):
        from project_forge.models import UrlIngestRequest

        req = UrlIngestRequest(url="https://www.feistyduck.com/newsletter/issue_135")
        assert req.url == "https://www.feistyduck.com/newsletter/issue_135"

    def test_ingest_request_optional_category(self):
        from project_forge.models import UrlIngestRequest

        req = UrlIngestRequest(
            url="https://example.com/article",
            category="crypto-infrastructure",
        )
        assert req.category == "crypto-infrastructure"

    def test_ingest_request_optional_notes(self):
        from project_forge.models import UrlIngestRequest

        req = UrlIngestRequest(
            url="https://example.com/article",
            notes="Merkle tree certs - could extend PKI-Client",
        )
        assert req.notes == "Merkle tree certs - could extend PKI-Client"


# === URL CONTENT EXTRACTION TESTS ===


class TestUrlFetcher:
    """Fetch and extract meaningful content from URLs."""

    @pytest.mark.asyncio
    async def test_fetch_url_returns_content(self):
        from project_forge.engine.url_ingest import fetch_url_content

        mock_html = """
        <html><head><title>Merkle Tree Certificates</title></head>
        <body><article>
        <h1>Web PKI Reimagined</h1>
        <p>Google proposes replacing X.509 with Merkle tree certificates.</p>
        </article></body></html>
        """
        with patch("project_forge.engine.url_ingest.httpx.AsyncClient") as mock_client:
            mock_response = MagicMock()
            mock_response.status_code = 200
            mock_response.text = mock_html
            mock_response.headers = {"content-type": "text/html"}
            mock_instance = AsyncMock()
            mock_instance.get = AsyncMock(return_value=mock_response)
            mock_instance.__aenter__ = AsyncMock(return_value=mock_instance)
            mock_instance.__aexit__ = AsyncMock(return_value=False)
            mock_client.return_value = mock_instance

            result = await fetch_url_content("https://example.com/article")
            assert result.title == "Merkle Tree Certificates"
            assert "Merkle tree" in result.text
            assert result.url == "https://example.com/article"

    @pytest.mark.asyncio
    async def test_fetch_url_extracts_domain(self):
        from project_forge.engine.url_ingest import fetch_url_content

        mock_html = "<html><head><title>Test</title></head><body><p>Content</p></body></html>"
        with patch("project_forge.engine.url_ingest.httpx.AsyncClient") as mock_client:
            mock_response = MagicMock()
            mock_response.status_code = 200
            mock_response.text = mock_html
            mock_response.headers = {"content-type": "text/html"}
            mock_instance = AsyncMock()
            mock_instance.get = AsyncMock(return_value=mock_response)
            mock_instance.__aenter__ = AsyncMock(return_value=mock_instance)
            mock_instance.__aexit__ = AsyncMock(return_value=False)
            mock_client.return_value = mock_instance

            result = await fetch_url_content("https://www.feistyduck.com/newsletter/issue_135")
            assert result.domain == "feistyduck.com"

    @pytest.mark.asyncio
    async def test_fetch_url_handles_non_html(self):
        from project_forge.engine.url_ingest import fetch_url_content

        with patch("project_forge.engine.url_ingest.httpx.AsyncClient") as mock_client:
            mock_response = MagicMock()
            mock_response.status_code = 200
            mock_response.text = '{"key": "value"}'
            mock_response.headers = {"content-type": "application/json"}
            mock_instance = AsyncMock()
            mock_instance.get = AsyncMock(return_value=mock_response)
            mock_instance.__aenter__ = AsyncMock(return_value=mock_instance)
            mock_instance.__aexit__ = AsyncMock(return_value=False)
            mock_client.return_value = mock_instance

            # Should still return content (extract what we can)
            result = await fetch_url_content("https://example.com/api")
            assert result.text  # Should have some content

    @pytest.mark.asyncio
    async def test_fetch_url_raises_on_http_error(self):
        from project_forge.engine.url_ingest import UrlFetchError, fetch_url_content

        with patch("project_forge.engine.url_ingest.httpx.AsyncClient") as mock_client:
            mock_response = MagicMock()
            mock_response.status_code = 404
            mock_response.text = "Not Found"
            mock_instance = AsyncMock()
            mock_instance.get = AsyncMock(return_value=mock_response)
            mock_instance.__aenter__ = AsyncMock(return_value=mock_instance)
            mock_instance.__aexit__ = AsyncMock(return_value=False)
            mock_client.return_value = mock_instance

            with pytest.raises(UrlFetchError, match="404"):
                await fetch_url_content("https://example.com/missing")

    @pytest.mark.asyncio
    async def test_fetch_strips_utm_params(self):
        from project_forge.engine.url_ingest import clean_url

        dirty = "https://www.feistyduck.com/newsletter/issue_135?utm_source=fd&utm_medium=email&utm_campaign=newsletter"
        clean = clean_url(dirty)
        assert "utm_" not in clean
        assert "issue_135" in clean


# === IDEA GENERATION FROM URL TESTS ===


class TestIdeaFromUrl:
    """Generate a project idea from extracted URL content."""

    @pytest.mark.asyncio
    async def test_generate_idea_from_content(self):
        from project_forge.engine.url_ingest import UrlContent, generate_idea_from_url

        content = UrlContent(
            url="https://feistyduck.com/merkle-tree-certs",
            domain="feistyduck.com",
            title="Merkle Tree Certificates",
            text=(
                "Google proposes replacing X.509 certificates with Merkle tree based certificates"
                " for Web PKI. This could dramatically reduce certificate sizes and improve transparency."
            ),
        )

        mock_idea = Idea(
            name="Merkle Tree Certificate Validator",
            tagline="Validate and verify Merkle tree certificates for Web PKI migration",
            description="A tool for validating Merkle tree certificates...",
            category=IdeaCategory.CRYPTO_INFRASTRUCTURE,
            market_analysis="Significant opportunity...",
            feasibility_score=0.85,
            mvp_scope="CLI validator...",
            tech_stack=["rust", "x509"],
        )

        with patch("project_forge.engine.generator.IdeaGenerator") as mock_gen:
            mock_instance = AsyncMock()
            mock_instance.generate_from_content = AsyncMock(return_value=mock_idea)
            mock_gen.return_value = mock_instance

            idea = await generate_idea_from_url(content)
            assert idea.name
            assert idea.category
            assert idea.feasibility_score >= 0.0
            assert idea.feasibility_score <= 1.0

    @pytest.mark.asyncio
    async def test_generate_idea_respects_category_hint(self):
        from project_forge.engine.url_ingest import UrlContent, generate_idea_from_url

        content = UrlContent(
            url="https://example.com/ech",
            domain="example.com",
            title="Encrypted Client Hello",
            text="ECH is an extension to TLS that encrypts the ClientHello message.",
        )

        mock_idea = Idea(
            name="ECH Implementation",
            tagline="Encrypted Client Hello for TLS 1.3",
            description="...",
            category=IdeaCategory.RFC_SECURITY,
            market_analysis="...",
            feasibility_score=0.78,
            mvp_scope="...",
            tech_stack=["rust", "tls"],
        )

        with patch("project_forge.engine.generator.IdeaGenerator") as mock_gen:
            mock_instance = AsyncMock()
            mock_instance.generate_from_content = AsyncMock(return_value=mock_idea)
            mock_gen.return_value = mock_instance

            idea = await generate_idea_from_url(content, category_hint=IdeaCategory.RFC_SECURITY)
            assert idea.category == IdeaCategory.RFC_SECURITY

    @pytest.mark.asyncio
    async def test_generate_idea_stores_source_url(self):
        """The generated idea should track where it came from."""
        from project_forge.engine.url_ingest import UrlContent, generate_idea_from_url

        content = UrlContent(
            url="https://feistyduck.com/article",
            domain="feistyduck.com",
            title="Test Article",
            text="Some content about PKI and certificates.",
        )

        mock_idea = Idea(
            name="Test Idea",
            tagline="Test",
            description="From URL",
            category=IdeaCategory.CRYPTO_INFRASTRUCTURE,
            market_analysis="...",
            feasibility_score=0.7,
            mvp_scope="...",
            source_url="https://feistyduck.com/article",
        )

        with patch("project_forge.engine.generator.IdeaGenerator") as mock_gen:
            mock_instance = AsyncMock()
            mock_instance.generate_from_content = AsyncMock(return_value=mock_idea)
            mock_gen.return_value = mock_instance

            idea = await generate_idea_from_url(content)
            assert idea.source_url == "https://feistyduck.com/article"


# === DATABASE: RESOURCE CRUD ===


class TestResourceStorage:
    """Database operations for resources."""

    @pytest.mark.asyncio
    async def test_save_and_get_resource(self, db):
        from project_forge.models import Resource

        resource = Resource(
            domain="feistyduck.com",
            name="Feisty Duck",
            description="PKI and TLS security newsletter by Ivan Ristic",
            url="https://www.feistyduck.com",
            categories=["crypto-infrastructure", "rfc-security"],
        )
        saved = await db.save_resource(resource)
        assert saved.id == resource.id

        fetched = await db.get_resource(resource.id)
        assert fetched is not None
        assert fetched.domain == "feistyduck.com"
        assert fetched.name == "Feisty Duck"

    @pytest.mark.asyncio
    async def test_list_resources(self, db):
        from project_forge.models import Resource

        r1 = Resource(domain="feistyduck.com", name="Feisty Duck", description="PKI newsletter")
        r2 = Resource(domain="rfc-editor.org", name="RFC Editor", description="IETF RFC repository")
        await db.save_resource(r1)
        await db.save_resource(r2)

        resources = await db.list_resources()
        assert len(resources) >= 2
        domains = [r.domain for r in resources]
        assert "feistyduck.com" in domains
        assert "rfc-editor.org" in domains

    @pytest.mark.asyncio
    async def test_get_resource_by_domain(self, db):
        from project_forge.models import Resource

        resource = Resource(
            domain="feistyduck.com",
            name="Feisty Duck",
            description="Security newsletter",
        )
        await db.save_resource(resource)

        fetched = await db.get_resource_by_domain("feistyduck.com")
        assert fetched is not None
        assert fetched.domain == "feistyduck.com"

    @pytest.mark.asyncio
    async def test_resource_dedup_by_domain(self, db):
        from project_forge.models import Resource

        r1 = Resource(domain="feistyduck.com", name="Feisty Duck v1", description="First entry")
        r2 = Resource(domain="feistyduck.com", name="Feisty Duck v2", description="Duplicate domain")
        await db.save_resource(r1)
        await db.save_resource(r2)

        # Should only have one entry for this domain
        resources = await db.list_resources()
        fd_resources = [r for r in resources if r.domain == "feistyduck.com"]
        assert len(fd_resources) == 1

    @pytest.mark.asyncio
    async def test_increment_resource_idea_count(self, db):
        from project_forge.models import Resource

        resource = Resource(domain="feistyduck.com", name="Feisty Duck", description="Newsletter")
        await db.save_resource(resource)

        await db.increment_resource_idea_count("feistyduck.com")
        fetched = await db.get_resource_by_domain("feistyduck.com")
        assert fetched.idea_count == 1

        await db.increment_resource_idea_count("feistyduck.com")
        fetched = await db.get_resource_by_domain("feistyduck.com")
        assert fetched.idea_count == 2


# === DATABASE: IDEA source_url FIELD ===


class TestIdeaSourceUrl:
    """Ideas can track their source URL."""

    @pytest.mark.asyncio
    async def test_idea_with_source_url(self, db):
        idea = Idea(
            name="Merkle Tree Cert Validator",
            tagline="Validate Merkle tree certs",
            description="A validator for Google's Merkle tree certificate proposal",
            category=IdeaCategory.CRYPTO_INFRASTRUCTURE,
            market_analysis="Growing interest in PKI reform",
            feasibility_score=0.85,
            mvp_scope="CLI validator",
            source_url="https://feistyduck.com/newsletter/issue_135",
        )
        await db.save_idea(idea)

        fetched = await db.get_idea(idea.id)
        assert fetched is not None
        assert fetched.source_url == "https://feistyduck.com/newsletter/issue_135"

    @pytest.mark.asyncio
    async def test_idea_source_url_defaults_none(self, db):
        idea = Idea(
            name="Generic Idea",
            tagline="Test",
            description="No source URL",
            category=IdeaCategory.SECURITY_TOOL,
            market_analysis="Test",
            feasibility_score=0.5,
            mvp_scope="Test",
        )
        await db.save_idea(idea)

        fetched = await db.get_idea(idea.id)
        assert fetched is not None
        assert fetched.source_url is None


# === API ROUTE TESTS ===


class TestUrlIngestApi:
    """POST /api/ideas/from-url endpoint."""

    @pytest_asyncio.fixture
    async def client(self, tmp_path):
        from httpx import ASGITransport, AsyncClient

        from project_forge.web.app import app, db

        db.db_path = tmp_path / "test_ingest.db"
        await db.connect()
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as c:
            yield c
        await db.close()

    @pytest.mark.asyncio
    async def test_ingest_url_endpoint(self, client):
        with patch("project_forge.web.routes.ingest_idea_from_url") as mock_ingest:
            mock_ingest.return_value = Idea(
                name="Merkle Tree Validator",
                tagline="Validate Merkle tree certs",
                description="From URL",
                category=IdeaCategory.CRYPTO_INFRASTRUCTURE,
                market_analysis="...",
                feasibility_score=0.85,
                mvp_scope="...",
                source_url="https://feistyduck.com/article",
            )

            resp = await client.post(
                "/api/ideas/from-url",
                json={"url": "https://feistyduck.com/article"},
            )
            assert resp.status_code == 200
            data = resp.json()
            assert data["name"] == "Merkle Tree Validator"
            assert data["source_url"] == "https://feistyduck.com/article"

    @pytest.mark.asyncio
    async def test_ingest_url_with_category_hint(self, client):
        with patch("project_forge.web.routes.ingest_idea_from_url") as mock_ingest:
            mock_ingest.return_value = Idea(
                name="ECH Tool",
                tagline="Encrypted Client Hello",
                description="...",
                category=IdeaCategory.RFC_SECURITY,
                market_analysis="...",
                feasibility_score=0.78,
                mvp_scope="...",
            )

            resp = await client.post(
                "/api/ideas/from-url",
                json={
                    "url": "https://example.com/ech",
                    "category": "rfc-security",
                },
            )
            assert resp.status_code == 200

    @pytest.mark.asyncio
    async def test_ingest_url_rejects_invalid_url(self, client):
        resp = await client.post(
            "/api/ideas/from-url",
            json={"url": "not-a-url"},
        )
        assert resp.status_code == 422


class TestResourceApi:
    """Resource tracking API endpoints."""

    @pytest_asyncio.fixture
    async def client(self, tmp_path):
        from httpx import ASGITransport, AsyncClient

        from project_forge.web.app import app, db

        db.db_path = tmp_path / "test_resources.db"
        await db.connect()
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as c:
            yield c
        await db.close()

    @pytest.mark.asyncio
    async def test_list_resources_endpoint(self, client):
        resp = await client.get("/api/resources")
        assert resp.status_code == 200
        data = resp.json()
        assert "resources" in data

    @pytest.mark.asyncio
    async def test_add_resource_endpoint(self, client):
        resp = await client.post(
            "/api/resources",
            json={
                "domain": "feistyduck.com",
                "name": "Feisty Duck",
                "description": "PKI and TLS security newsletter",
                "url": "https://www.feistyduck.com",
                "categories": ["crypto-infrastructure", "rfc-security"],
            },
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["domain"] == "feistyduck.com"


# === URL UTILITY TESTS ===


class TestUrlUtilities:
    """URL parsing and cleaning utilities."""

    def test_extract_domain_from_url(self):
        from project_forge.engine.url_ingest import extract_domain

        assert extract_domain("https://www.feistyduck.com/newsletter/issue_135") == "feistyduck.com"
        assert extract_domain("https://datatracker.ietf.org/doc/rfc9180") == "datatracker.ietf.org"
        assert extract_domain("https://blog.cloudflare.com/ech") == "blog.cloudflare.com"

    def test_clean_url_removes_tracking(self):
        from project_forge.engine.url_ingest import clean_url

        dirty = "https://feistyduck.com/article?utm_source=email&utm_medium=newsletter&ref=twitter"
        clean = clean_url(dirty)
        assert "utm_source" not in clean
        assert "utm_medium" not in clean
        assert "ref" not in clean
        assert "feistyduck.com/article" in clean

    def test_clean_url_preserves_meaningful_params(self):
        from project_forge.engine.url_ingest import clean_url

        url = "https://datatracker.ietf.org/doc/rfc9180?format=html"
        clean = clean_url(url)
        # format is meaningful, not tracking
        assert "format=html" in clean

    def test_validate_url_accepts_https(self):
        from project_forge.engine.url_ingest import validate_url

        assert validate_url("https://feistyduck.com/article") is True
        assert validate_url("http://feistyduck.com/article") is True

    def test_validate_url_rejects_garbage(self):
        from project_forge.engine.url_ingest import validate_url

        assert validate_url("not-a-url") is False
        assert validate_url("ftp://files.example.com") is False
        assert validate_url("") is False


# === Generator.generate_from_content tests ===


MOCK_IDEA_JSON = json.dumps({
    "name": "Merkle Certificate Verifier",
    "tagline": "Validate Merkle Tree certificates against transparency logs",
    "description": "A tool to verify and audit Merkle Tree-based certificates.",
    "category": "pqc-cryptography",
    "market_analysis": "Web PKI is evolving toward certificate transparency.",
    "feasibility_score": 0.82,
    "mvp_scope": "CLI tool that validates certificates against Merkle proofs.",
    "tech_stack": ["python", "cryptography", "httpx"],
})


class TestGenerateFromContent:
    @pytest.mark.asyncio
    async def test_generate_from_content_returns_idea(self):
        from project_forge.engine.generator import IdeaGenerator
        from project_forge.engine.url_ingest import UrlContent

        mock_response = MagicMock()
        mock_response.content = [MagicMock(text=MOCK_IDEA_JSON)]

        with patch.object(IdeaGenerator, "__init__", lambda self, **kw: None):
            gen = IdeaGenerator()
            gen.client = MagicMock()
            gen.model = "test-model"
            gen.client.messages.create = MagicMock(return_value=mock_response)

            content = UrlContent(
                url="https://feistyduck.com/newsletter/issue_135",
                domain="feistyduck.com",
                title="Web PKI Reimagined with Merkle Tree Certificates",
                text="Chrome is experimenting with Merkle Tree Certificates...",
            )
            idea = await gen.generate_from_content(content)

        assert isinstance(idea, Idea)
        assert idea.name == "Merkle Certificate Verifier"
        assert idea.source_url == "https://feistyduck.com/newsletter/issue_135"

    @pytest.mark.asyncio
    async def test_generate_from_content_with_category_hint(self):
        from project_forge.engine.generator import IdeaGenerator
        from project_forge.engine.url_ingest import UrlContent

        mock_response = MagicMock()
        mock_response.content = [MagicMock(text=MOCK_IDEA_JSON)]

        with patch.object(IdeaGenerator, "__init__", lambda self, **kw: None):
            gen = IdeaGenerator()
            gen.client = MagicMock()
            gen.model = "test-model"
            gen.client.messages.create = MagicMock(return_value=mock_response)

            content = UrlContent(
                url="https://example.com/article",
                domain="example.com",
                title="Test Article",
                text="Some content about security.",
            )
            idea = await gen.generate_from_content(content, category_hint="security-tool")

        assert isinstance(idea, Idea)
        # Verify the prompt included the category hint
        call_args = gen.client.messages.create.call_args
        prompt_text = call_args.kwargs["messages"][0]["content"]
        assert "security-tool" in prompt_text
