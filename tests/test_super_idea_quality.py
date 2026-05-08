"""TDD: Super idea keyword extraction and name quality.

Problem: _extract_cluster_keywords picks up low-signal words like "well", "known",
"insecure", "direct", "multi", "control", "object", "broken" from OWASP jargon and
generic phrases in idea names/taglines. These produce terrible super idea names like:
  - "Well Known Defense Suite"
  - "Insecure Direct Observatory"
  - "Multi Control Command Center"

Fix targets:
- _NAME_STOP_WORDS: add OWASP jargon + generic security terms
- _extract_cluster_keywords: increase min word length >= 5 to filter 4-char filler
"""

from project_forge.engine.super_ideas import (
    _NAME_STOP_WORDS,
    _dynamic_cluster_name,
    _extract_cluster_keywords,
)
from project_forge.models import Idea, IdeaCategory


def _idea(name: str, tagline: str, cat: IdeaCategory = IdeaCategory.SECURITY_TOOL) -> Idea:
    return Idea(
        name=name,
        tagline=tagline,
        description="Desc.",
        category=cat,
        market_analysis="Market.",
        feasibility_score=0.8,
        mvp_scope="MVP.",
        tech_stack=["python"],
    )


class TestNameStopWords:
    """_NAME_STOP_WORDS must include OWASP jargon and generic security terms."""

    def test_owasp_adjective_insecure_in_stop_words(self):
        assert "insecure" in _NAME_STOP_WORDS, "'insecure' makes bad names: 'Insecure Direct Observatory'"

    def test_owasp_preposition_direct_in_stop_words(self):
        assert "direct" in _NAME_STOP_WORDS, "'direct' from OWASP IDOR makes bad names"

    def test_owasp_generic_object_in_stop_words(self):
        assert "object" in _NAME_STOP_WORDS, "'object' from OWASP object reference is too generic"

    def test_owasp_adjective_broken_in_stop_words(self):
        assert "broken" in _NAME_STOP_WORDS, "'broken' from OWASP Broken Auth makes bad names"

    def test_owasp_sensitive_in_stop_words(self):
        assert "sensitive" in _NAME_STOP_WORDS

    def test_generic_multi_in_stop_words(self):
        assert "multi" in _NAME_STOP_WORDS, "'multi' is too generic: 'Multi Control Command Center'"

    def test_generic_control_in_stop_words(self):
        assert "control" in _NAME_STOP_WORDS, "'control' as in access-control is too generic"

    def test_qualifier_well_in_stop_words(self):
        assert "well" in _NAME_STOP_WORDS, "'well' from 'well-known URIs' makes nonsense names"

    def test_qualifier_known_in_stop_words(self):
        assert "known" in _NAME_STOP_WORDS, "'known' from 'well-known' makes nonsense names"

    def test_generic_access_in_stop_words(self):
        assert "access" in _NAME_STOP_WORDS, "'access' alone conveys nothing in a name"


class TestExtractClusterKeywords:
    """_extract_cluster_keywords must not return generic jargon words."""

    def test_filters_owasp_jargon_words(self):
        ideas = [
            _idea("Insecure Direct Object Reference Scanner", "insecure direct object detection: security"),
            _idea("Broken Access Control Detector", "broken access control: authentication"),
            _idea("Sensitive Data Exposure Finder", "sensitive data exposure: privacy"),
        ]
        keywords = _extract_cluster_keywords(ideas)
        junk = {"insecure", "direct", "object", "broken", "access", "control", "sensitive", "well", "known"}
        found_junk = set(keywords) & junk
        assert not found_junk, f"Generic OWASP jargon leaked into keywords: {found_junk}"

    def test_filters_four_char_filler_words(self):
        ideas = [
            _idea("Well Known URI Discovery Tool", "well known uris: security"),
            _idea("Multi Post Handler", "multi post http: rest"),
        ]
        keywords = _extract_cluster_keywords(ideas)
        four_char_words = {w for w in keywords if len(w) <= 4}
        assert not four_char_words, f"Short filler words in keywords: {four_char_words}"

    def test_preserves_meaningful_long_keywords(self):
        ideas = [
            _idea("Certificate Pinning Validator", "certificate pinning: tls security"),
            _idea("Certificate Authority Monitor", "certificate authority: pki monitoring"),
        ]
        keywords = _extract_cluster_keywords(ideas)
        assert "certificate" in keywords, "Meaningful keyword 'certificate' should be extracted"
        assert "pinning" in keywords or "authority" in keywords

    def test_preserves_domain_keywords(self):
        ideas = [
            _idea("Quantum Key Distribution System", "quantum key distribution: cryptography"),
            _idea("Post-Quantum Algorithm Benchmarker", "quantum algorithm benchmarking: performance"),
        ]
        keywords = _extract_cluster_keywords(ideas)
        assert "quantum" in keywords, "'quantum' is a meaningful domain keyword"

    def test_no_generic_adjectives(self):
        ideas = [
            _idea("Advanced Smart Detection Framework", "advanced smart automated detection: security"),
            _idea("Integrated Secure Platform Builder", "integrated secure solution: tool"),
        ]
        keywords = _extract_cluster_keywords(ideas)
        generic = {"advanced", "smart", "automated", "integrated", "secure"}
        found = set(keywords) & generic
        assert not found, f"Generic adjectives in keywords: {found}"


class TestDynamicClusterNameQuality:
    """_dynamic_cluster_name must produce meaningful names from real-world data patterns."""

    def test_owasp_cluster_avoids_jargon_in_name(self):
        ideas = [
            _idea("Insecure Direct Object Reference Scanner", "idor detection: web security"),
            _idea("Broken Access Control Finder", "access control audit: authorization"),
            _idea("SQL Injection Detector", "sql injection prevention: database security"),
        ]
        cats = frozenset({IdeaCategory.SECURITY_TOOL, IdeaCategory.VULNERABILITY_RESEARCH})
        for _ in range(20):
            name = _dynamic_cluster_name(ideas, cats)
            core = name.replace("[SUPER] ", "")
            junk_words = ["Insecure", "Direct", "Object", "Broken", "Multi", "Well", "Known"]
            for word in junk_words:
                assert word not in core.split(), f"Junk word '{word}' in generated name: {name}"

    def test_wellknown_cluster_produces_specific_name(self):
        """A cluster about well-known URIs should name the concept, not the adjectives."""
        ideas = [
            _idea("Well-Known URI Discovery Service", "well-known uri discovery: rfc compliance"),
            _idea("Well-Known Endpoint Validator", "well-known endpoint validation: security"),
        ]
        cats = frozenset({IdeaCategory.RFC_SECURITY})
        for _ in range(20):
            name = _dynamic_cluster_name(ideas, cats)
            core = name.replace("[SUPER] ", "")
            # "Well" or "Known" as standalone words in the name are the problem
            parts = core.split()
            assert "Well" not in parts, f"'Well' leaked into name: {name}"
            assert "Known" not in parts, f"'Known' leaked into name: {name}"
