"""Tests for RFC watcher module."""

from project_forge.rfc.filters import SECURITY_WORKING_GROUPS, is_security_relevant
from project_forge.rfc.models import IETFDraft, RFCEntry
from project_forge.rfc.watcher import RFCWatcher


class TestRFCModels:
    def test_rfc_entry_creation(self):
        entry = RFCEntry(
            number=9629,
            title="Post-Quantum Certificates",
            authors=["Author One"],
            status="proposed-standard",
            abstract="About PQC certs.",
            working_group="lamps",
            keywords=["pqc", "certificates"],
            url="https://www.rfc-editor.org/rfc/rfc9629",
        )
        assert entry.number == 9629
        assert entry.is_security_relevant()

    def test_ietf_draft_creation(self):
        draft = IETFDraft(
            name="draft-ietf-lamps-pq-composite-sigs-03",
            title="Composite Signatures for PQC",
            working_group="lamps",
            status="active",
            abstract="Composite signature algorithms.",
            url="https://datatracker.ietf.org/doc/draft-ietf-lamps-pq-composite-sigs/",
        )
        assert "lamps" in draft.name
        assert draft.is_security_relevant()

    def test_rfc_entry_serialization(self):
        entry = RFCEntry(
            number=8446,
            title="TLS 1.3",
            authors=["E. Rescorla"],
            status="proposed-standard",
            abstract="TLS protocol version 1.3.",
            working_group="tls",
            keywords=["tls"],
            url="https://www.rfc-editor.org/rfc/rfc8446",
        )
        data = entry.model_dump()
        restored = RFCEntry(**data)
        assert restored.number == 8446


class TestSecurityFilters:
    def test_security_working_groups_defined(self):
        assert "lamps" in SECURITY_WORKING_GROUPS
        assert "tls" in SECURITY_WORKING_GROUPS
        assert "cfrg" in SECURITY_WORKING_GROUPS
        assert len(SECURITY_WORKING_GROUPS) >= 8

    def test_is_security_relevant_by_working_group(self):
        entry = RFCEntry(
            number=1,
            title="Test",
            authors=[],
            status="info",
            abstract="",
            working_group="lamps",
            keywords=[],
            url="",
        )
        assert is_security_relevant(entry) is True

    def test_is_security_relevant_by_keyword(self):
        entry = RFCEntry(
            number=2,
            title="Test",
            authors=[],
            status="info",
            abstract="",
            working_group="other",
            keywords=["cryptography", "pqc"],
            url="",
        )
        assert is_security_relevant(entry) is True

    def test_not_security_relevant(self):
        entry = RFCEntry(
            number=3,
            title="HTTP Caching",
            authors=[],
            status="info",
            abstract="About caching.",
            working_group="httpbis",
            keywords=["http"],
            url="",
        )
        assert is_security_relevant(entry) is False

    def test_security_relevant_by_title(self):
        entry = RFCEntry(
            number=4,
            title="Post-Quantum Key Exchange",
            authors=[],
            status="info",
            abstract="",
            working_group="unknown",
            keywords=[],
            url="",
        )
        assert is_security_relevant(entry) is True


class TestRFCWatcher:
    def test_watcher_init(self):
        watcher = RFCWatcher()
        assert watcher.base_url is not None
        assert watcher.security_groups == SECURITY_WORKING_GROUPS

    def test_watcher_parse_rfc_xml(self):
        """Test parsing of RFC index XML format."""
        w = RFCWatcher()
        sample_xml = """<?xml version="1.0" encoding="UTF-8"?>
        <rfc-index>
            <rfc-entry>
                <doc-id>RFC9629</doc-id>
                <title>Using Key Encapsulation Mechanism (KEM) Algorithms in CMS</title>
                <author><name>J. Housley</name></author>
                <current-status>PROPOSED STANDARD</current-status>
                <abstract><p>About KEM in CMS.</p></abstract>
                <keywords><kw>kem</kw><kw>cms</kw></keywords>
            </rfc-entry>
        </rfc-index>"""
        entries = w.parse_rfc_xml(sample_xml)
        assert len(entries) >= 1
        assert entries[0].number == 9629

    def test_watcher_parse_draft_json(self):
        """Test parsing of IETF datatracker draft JSON."""
        w = RFCWatcher()
        sample_json = {
            "objects": [
                {
                    "name": "draft-ietf-lamps-pq-composite-sigs",
                    "title": "Composite ML-DSA Signatures",
                    "group": {"acronym": "lamps"},
                    "states": [{"slug": "active"}],
                    "abstract": "PQC composite signatures.",
                    "resource_uri": "/api/v1/doc/document/draft-ietf-lamps-pq-composite-sigs/",
                }
            ]
        }
        drafts = w.parse_draft_json(sample_json)
        assert len(drafts) >= 1
        assert "lamps" in drafts[0].name
