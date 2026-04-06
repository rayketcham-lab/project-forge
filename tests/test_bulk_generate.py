"""Tests for bulk idea generation."""

import json
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
import pytest_asyncio

from project_forge.engine.bulk import SECURITY_FOCUS_AREAS, BulkConfig, BulkGenerator
from project_forge.storage.db import Database


@pytest_asyncio.fixture
async def db(tmp_path: Path):
    database = Database(tmp_path / "test_bulk.db")
    await database.connect()
    yield database
    await database.close()


class TestBulkConfig:
    def test_default_config(self):
        config = BulkConfig()
        assert config.target_count == 100
        assert config.batch_size == 5
        assert len(config.focus_areas) > 0

    def test_custom_config(self):
        config = BulkConfig(target_count=50, batch_size=10)
        assert config.target_count == 50
        assert config.batch_size == 10

    def test_focus_areas_include_pqc(self):
        config = BulkConfig()
        area_names = [a["name"] for a in config.focus_areas]
        assert any("PQC" in name or "pqc" in name.lower() or "quantum" in name.lower() for name in area_names)

    def test_focus_areas_include_nist(self):
        config = BulkConfig()
        area_names = [a["name"] for a in config.focus_areas]
        assert any("NIST" in name or "nist" in name.lower() for name in area_names)

    def test_focus_areas_include_rfc(self):
        config = BulkConfig()
        area_names = [a["name"] for a in config.focus_areas]
        assert any("RFC" in name or "rfc" in name.lower() for name in area_names)


class TestSecurityFocusAreas:
    def test_focus_areas_defined(self):
        assert len(SECURITY_FOCUS_AREAS) >= 10

    def test_each_area_has_required_fields(self):
        for area in SECURITY_FOCUS_AREAS:
            assert "name" in area
            assert "description" in area
            assert "categories" in area
            assert "seed_topics" in area
            assert len(area["seed_topics"]) >= 3

    def test_pqc_crl_focus_area(self):
        pqc_areas = [a for a in SECURITY_FOCUS_AREAS if "CRL" in a["name"] or "crl" in str(a.get("seed_topics", []))]
        assert len(pqc_areas) >= 1, "Must have a PQC CRL-focused area"


MOCK_IDEA_JSON = json.dumps(
    {
        "name": "CRL Quantum Guard",
        "tagline": "PQC-safe CRL management",
        "description": "Manages CRLs with PQC signatures.",
        "category": "pqc-cryptography",
        "market_analysis": "PQC transition is happening now.",
        "feasibility_score": 0.8,
        "mvp_scope": "CRL signing tool with ML-DSA.",
        "tech_stack": ["python", "openssl", "cryptography"],
    }
)


class TestBulkGenerator:
    @patch("project_forge.engine.generator.anthropic.Anthropic")
    @pytest.mark.asyncio
    async def test_generate_batch(self, mock_anthropic_cls, db):
        mock_client = MagicMock()
        mock_anthropic_cls.return_value = mock_client

        # Return genuinely distinct ideas per call so dedup doesn't filter them
        taglines = [
            "quantum-safe certificate revocation list signing tool",
            "lattice-based key encapsulation benchmark suite",
            "hybrid TLS handshake protocol analyzer for migration",
        ]
        call_count = 0

        def _make_response(*args, **kwargs):
            nonlocal call_count
            call_count += 1
            idea_json = json.dumps(
                {
                    "name": f"PQC Tool {call_count}",
                    "tagline": taglines[call_count - 1],
                    "description": "PQC tooling for post-quantum migration.",
                    "category": "pqc-cryptography",
                    "market_analysis": "PQC transition is happening now.",
                    "feasibility_score": 0.8,
                    "mvp_scope": "Build the core tool.",
                    "tech_stack": ["python", "openssl", "cryptography"],
                }
            )
            mock_content = MagicMock()
            mock_content.text = idea_json
            mock_response = MagicMock()
            mock_response.content = [mock_content]
            return mock_response

        mock_client.messages.create.side_effect = _make_response

        config = BulkConfig(target_count=3, batch_size=3)
        bulk = BulkGenerator(db=db, api_key="test-key", config=config)
        ideas = await bulk.generate_batch(count=3)

        assert len(ideas) == 3

    @patch("project_forge.engine.generator.anthropic.Anthropic")
    @pytest.mark.asyncio
    async def test_generate_batch_stores_ideas(self, mock_anthropic_cls, db):
        mock_client = MagicMock()
        mock_anthropic_cls.return_value = mock_client

        # Return unique taglines per call so universal dedup doesn't block
        call_count = 0

        def _make_response(*args, **kwargs):
            nonlocal call_count
            call_count += 1
            idea_json = json.dumps(
                {
                    "name": f"CRL Quantum Guard {call_count}",
                    "tagline": f"PQC-safe CRL management variant {call_count}",
                    "description": "Manages CRLs with PQC signatures.",
                    "category": "pqc-cryptography",
                    "market_analysis": "PQC transition is happening now.",
                    "feasibility_score": 0.8,
                    "mvp_scope": "CRL signing tool with ML-DSA.",
                    "tech_stack": ["python", "openssl", "cryptography"],
                }
            )
            mock_content = MagicMock()
            mock_content.text = idea_json
            mock_response = MagicMock()
            mock_response.content = [mock_content]
            return mock_response

        mock_client.messages.create.side_effect = _make_response

        config = BulkConfig(target_count=2, batch_size=2)
        bulk = BulkGenerator(db=db, api_key="test-key", config=config)
        await bulk.generate_batch(count=2)

        count = await db.count_ideas()
        assert count == 2

    def test_distribute_across_focus_areas(self):
        config = BulkConfig(target_count=100)
        bulk = BulkGenerator.__new__(BulkGenerator)
        bulk.config = config
        distribution = bulk.plan_distribution()

        assert sum(distribution.values()) == 100
        # Should cover multiple focus areas
        assert len(distribution) >= 5

    @patch("project_forge.engine.generator.anthropic.Anthropic")
    @pytest.mark.asyncio
    async def test_generate_handles_errors_gracefully(self, mock_anthropic_cls, db):
        mock_client = MagicMock()
        mock_anthropic_cls.return_value = mock_client

        # Return distinct ideas so dedup doesn't filter the second success
        def _make_resp(tagline):
            idea_json = json.dumps(
                {
                    "name": f"Tool for {tagline[:20]}",
                    "tagline": tagline,
                    "description": "Manages CRLs with PQC signatures.",
                    "category": "pqc-cryptography",
                    "market_analysis": "PQC transition is happening now.",
                    "feasibility_score": 0.8,
                    "mvp_scope": "CRL signing tool with ML-DSA.",
                    "tech_stack": ["python", "openssl", "cryptography"],
                }
            )
            content = MagicMock()
            content.text = idea_json
            resp = MagicMock()
            resp.content = [content]
            return resp

        mock_client.messages.create.side_effect = [
            _make_resp("quantum-safe certificate revocation list signing"),
            RuntimeError("API error"),
            _make_resp("lattice-based key encapsulation benchmark suite"),
        ]

        config = BulkConfig(target_count=3, batch_size=3)
        bulk = BulkGenerator(db=db, api_key="test-key", config=config)
        ideas = await bulk.generate_batch(count=3)

        # Should get 2 successful, 1 failed
        assert len(ideas) == 2
