"""TDD: Super ideas must have content-driven names and taglines.

Problem: Super ideas all use generic names like "Compliance Automation Engine"
and taglines like "Unified platform combining 6 ideas across automation, compliance".
Names don't reflect actual component content — they just describe category labels.

Fix:
1. Extract keywords from component idea names/taglines
2. Build content-driven names using those keywords
3. Build specific taglines from component concept terms
"""

from project_forge.engine.super_ideas import (
    _build_super_tagline,
    _dynamic_cluster_name,
    _extract_cluster_keywords,
    find_idea_clusters,
    synthesize_super_idea,
)
from project_forge.models import Idea, IdeaCategory


def _make_idea(
    name: str,
    tagline: str,
    category: IdeaCategory = IdeaCategory.SECURITY_TOOL,
    score: float = 0.75,
) -> Idea:
    return Idea(
        name=name,
        tagline=tagline,
        description="Test description",
        category=category,
        market_analysis="Test market",
        feasibility_score=score,
        mvp_scope="Test scope",
        tech_stack=["python"],
    )


_SECURITY_IDEAS = [
    _make_idea("Supply Chain Attack Detection", "supply chain attack detection: healthcare"),
    _make_idea("Certificate Transparency Monitor", "certificate transparency log monitor: finance"),
    _make_idea("OAuth Consent Scope Advisor", "oauth consent scope minimization: enterprise"),
    _make_idea("Shadow IT Discovery Tool", "shadow it discovery and risk assessment: retail"),
    _make_idea("SSH Key Lifecycle Manager", "ssh key lifecycle governance: government"),
]


class TestKeywordExtraction:
    def test_returns_meaningful_terms(self):
        keywords = _extract_cluster_keywords(_SECURITY_IDEAS)
        assert len(keywords) >= 3
        assert all(len(k) > 3 for k in keywords)

    def test_excludes_stop_words(self):
        ideas = [
            _make_idea("Tool for Security Platform", "unified system for the platform: domain"),
        ]
        keywords = _extract_cluster_keywords(ideas)
        stop = {"tool", "platform", "system", "unified"}
        for kw in keywords:
            assert kw.lower() not in stop, f"Stop word {kw!r} found in keywords"

    def test_returns_domain_specific_terms(self):
        keywords = _extract_cluster_keywords(_SECURITY_IDEAS)
        keyword_set = set(keywords)
        domain_terms = {"supply", "chain", "certificate", "transparency", "oauth", "shadow", "lifecycle"}
        assert len(keyword_set & domain_terms) >= 2, (
            f"Expected domain terms in keywords, got: {keywords}"
        )


class TestDynamicClusterName:
    def test_never_produces_generic_unified_platform(self):
        ideas = _SECURITY_IDEAS[:3]
        for _ in range(20):
            name = _dynamic_cluster_name(ideas, frozenset({IdeaCategory.SECURITY_TOOL}))
            assert "Unified Platform" not in name, f"Got generic name: {name!r}"

    def test_name_reflects_component_keywords(self):
        ideas = [
            _make_idea("Certificate Lifecycle Manager", "certificate lifecycle management: pki"),
            _make_idea("OCSP Response Validator", "ocsp response validation: banking"),
            _make_idea("HSM Key Import Tool", "hsm key import management: finance"),
        ]
        name = _dynamic_cluster_name(ideas, frozenset({IdeaCategory.CRYPTO_INFRASTRUCTURE}))
        name_lower = name.lower()
        content_terms = {"certificate", "lifecycle", "ocsp", "response", "hsm", "import"}
        assert any(t in name_lower for t in content_terms), f"No content keywords in: {name!r}"

    def test_cross_category_name_reflects_content(self):
        ideas = [
            _make_idea(
                "Post-Quantum Migration",
                "post-quantum migration: enterprise",
                IdeaCategory.PQC_CRYPTOGRAPHY,
            ),
            _make_idea(
                "Certificate Store Upgrade",
                "certificate store upgrade: banking",
                IdeaCategory.CRYPTO_INFRASTRUCTURE,
            ),
        ]
        name = _dynamic_cluster_name(
            ideas,
            frozenset({IdeaCategory.PQC_CRYPTOGRAPHY, IdeaCategory.CRYPTO_INFRASTRUCTURE}),
        )
        assert "Unified Platform" not in name
        name_lower = name.lower()
        assert any(t in name_lower for t in {"quantum", "migration", "certificate", "store"})

    def test_all_single_categories_produce_non_generic_names(self):
        """Every category must produce content-driven names, not 'X Unified Platform'."""
        for cat in IdeaCategory:
            if cat == IdeaCategory.SELF_IMPROVEMENT:
                continue
            ideas = [
                _make_idea("Token Binding Policy Enforcer", "token binding policy: healthcare", cat),
                _make_idea("Session Fixation Detector", "session fixation detection: finance", cat),
                _make_idea("Key Escrow Risk Analyzer", "key escrow risk analysis: government", cat),
            ]
            name = _dynamic_cluster_name(ideas, frozenset({cat}))
            assert "Unified Platform" not in name, (
                f"{cat.value}: got generic name {name!r}"
            )


class TestSuperTagline:
    def test_not_generic_unified_combining(self):
        for _ in range(10):
            tagline = _build_super_tagline(_SECURITY_IDEAS[:2])
            assert "Unified platform combining" not in tagline
            assert "unified platform combining" not in tagline.lower()

    def test_contains_component_concepts(self):
        ideas = [
            _make_idea("Supply Chain Attack Detection", "supply chain attack detection: healthcare"),
            _make_idea("Certificate Transparency Monitor", "certificate transparency monitoring: finance"),
        ]
        tagline = _build_super_tagline(ideas)
        tagline_lower = tagline.lower()
        content_terms = {"supply chain", "certificate", "attack", "transparency"}
        assert any(t in tagline_lower for t in content_terms), (
            f"No component concepts in tagline: {tagline!r}"
        )

    def test_max_120_chars(self):
        assert len(_build_super_tagline(_SECURITY_IDEAS)) <= 120

    def test_handles_ideas_without_colon_tagline(self):
        ideas = [
            _make_idea("Supply Chain Detector", "old style tagline without colon"),
            _make_idea("Certificate Monitor", "another tagline without colon"),
        ]
        tagline = _build_super_tagline(ideas)
        assert len(tagline) > 0
        assert len(tagline) <= 120


class TestFindIdeaClustersNaming:
    def test_cluster_themes_not_generic(self):
        """Cluster themes must be content-driven, not 'X Unified Platform'."""
        ideas = [
            _make_idea(
                "Post-Quantum Key Exchange",
                "post-quantum key exchange: enterprise",
                IdeaCategory.PQC_CRYPTOGRAPHY,
            ),
            _make_idea(
                "Dilithium Signature Validator",
                "dilithium signature validation: government",
                IdeaCategory.PQC_CRYPTOGRAPHY,
            ),
            _make_idea(
                "CRYSTALS-Kyber Implementation",
                "crystals kyber implementation: banking",
                IdeaCategory.PQC_CRYPTOGRAPHY,
            ),
            _make_idea(
                "Certificate Store Migrator",
                "certificate store migration: enterprise",
                IdeaCategory.CRYPTO_INFRASTRUCTURE,
            ),
            _make_idea(
                "HSM Key Import Tool",
                "hsm key import management: finance",
                IdeaCategory.CRYPTO_INFRASTRUCTURE,
            ),
        ]
        clusters = find_idea_clusters(ideas)
        assert len(clusters) > 0
        for cluster in clusters:
            assert "Unified Platform" not in cluster["theme"], (
                f"Generic name in cluster: {cluster['theme']!r}"
            )

    def test_single_category_cluster_not_generic(self):
        """Single-category clusters must not use 'X Unified Platform' fallback."""
        ideas = [
            _make_idea("Supply Chain Scanner", "supply chain attack detection: retail", IdeaCategory.SECURITY_TOOL),
            _make_idea("Shadow IT Detector", "shadow it risk assessment: enterprise", IdeaCategory.SECURITY_TOOL),
            _make_idea("OAuth Scope Advisor", "oauth consent scope minimization: saas", IdeaCategory.SECURITY_TOOL),
            _make_idea("SSH Key Manager", "ssh key lifecycle governance: cloud", IdeaCategory.SECURITY_TOOL),
        ]
        clusters = find_idea_clusters(ideas)
        for cluster in clusters:
            assert "Unified Platform" not in cluster["theme"], (
                f"Generic name in single-cat cluster: {cluster['theme']!r}"
            )


class TestSynthesizeEnd2End:
    def test_synthesize_tagline_not_generic(self):
        ideas = _SECURITY_IDEAS[:3]
        cluster = {
            "theme": _dynamic_cluster_name(ideas, frozenset({IdeaCategory.SECURITY_TOOL})),
            "ideas": ideas,
            "categories": frozenset({IdeaCategory.SECURITY_TOOL}),
        }
        si = synthesize_super_idea(cluster)
        assert "Unified platform combining" not in si.tagline
        assert len(si.tagline) <= 120

    def test_synthesize_tagline_references_content(self):
        ideas = _SECURITY_IDEAS[:2]
        cluster = {
            "theme": _dynamic_cluster_name(ideas, frozenset({IdeaCategory.SECURITY_TOOL})),
            "ideas": ideas,
            "categories": frozenset({IdeaCategory.SECURITY_TOOL}),
        }
        si = synthesize_super_idea(cluster)
        tagline_lower = si.tagline.lower()
        content_terms = {"supply chain", "certificate", "attack", "transparency", "oauth"}
        assert any(t in tagline_lower for t in content_terms), (
            f"No content in super idea tagline: {si.tagline!r}"
        )

    def test_synthesize_uses_cluster_theme_as_name(self):
        ideas = _SECURITY_IDEAS[:2]
        dynamic_theme = _dynamic_cluster_name(ideas, frozenset({IdeaCategory.SECURITY_TOOL}))
        cluster = {
            "theme": dynamic_theme,
            "ideas": ideas,
            "categories": frozenset({IdeaCategory.SECURITY_TOOL}),
        }
        si = synthesize_super_idea(cluster)
        assert si.name == dynamic_theme
