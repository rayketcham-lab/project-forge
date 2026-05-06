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


class TestNoSuperIdeasInPool:
    """[SUPER] ideas must not be included in the clustering pool.

    When [SUPER] ideas are clustered, the word 'super' dominates keyword
    extraction and old super idea names appear in new taglines.
    """

    def test_keywords_never_contain_super_word(self):
        """Keyword extractor must not produce 'super' even from [SUPER]-named ideas."""
        ideas = [
            _make_idea("[SUPER] Old Platform Name", "old platform: some domain"),
            _make_idea("Supply Chain Detector", "supply chain attack detection: healthcare"),
        ]
        kws = _extract_cluster_keywords(ideas)
        assert "super" not in kws, f"'super' found in keywords: {kws}"

    def test_find_idea_clusters_filters_super_ideas(self):
        """find_idea_clusters must exclude [SUPER] ideas from the pool."""
        regular_ideas = [
            _make_idea("Supply Chain Scanner", "supply chain attack detection: retail", IdeaCategory.SECURITY_TOOL),
            _make_idea("Shadow IT Detector", "shadow it risk assessment: enterprise", IdeaCategory.SECURITY_TOOL),
            _make_idea("OAuth Scope Advisor", "oauth consent scope minimization: saas", IdeaCategory.SECURITY_TOOL),
            _make_idea("SSH Key Manager", "ssh key lifecycle governance: cloud", IdeaCategory.SECURITY_TOOL),
        ]
        super_ideas_in_pool = [
            _make_idea("[SUPER] Old Generic Platform", "old platform: combined things", IdeaCategory.SECURITY_TOOL),
            _make_idea("[SUPER] Super Infrastructure Center", "super infrastructure: old", IdeaCategory.SECURITY_TOOL),
        ]
        all_ideas = regular_ideas + super_ideas_in_pool
        clusters = find_idea_clusters(all_ideas)

        for cluster in clusters:
            for idea in cluster["ideas"]:
                assert not idea.name.startswith("[SUPER]"), (
                    f"[SUPER] idea {idea.name!r} ended up in cluster {cluster['theme']!r}"
                )

    def test_cluster_names_dont_contain_super_when_pool_has_supers(self):
        """Cluster names must not reference 'super' even when [SUPER] ideas are in input."""
        ci = IdeaCategory.CRYPTO_INFRASTRUCTURE
        regular = [
            _make_idea("Certificate Lifecycle Manager", "certificate lifecycle: pki", ci),
            _make_idea("HSM Key Import Tool", "hsm key import: banking", ci),
            _make_idea("OCSP Validator", "ocsp validation: finance", ci),
            _make_idea("Key Escrow Auditor", "key escrow audit: government", ci),
        ]
        supers = [
            _make_idea("[SUPER] Super Platform Legacy", "super platform: old combined", ci),
        ]
        clusters = find_idea_clusters(regular + supers)
        for cluster in clusters:
            assert "Super" not in cluster["theme"] and "super" not in cluster["theme"].lower(), (
                f"'super' leaked into cluster name: {cluster['theme']!r}"
            )


class TestStatCardIntegrity:
    """Dashboard stat cards must use authoritative counts, not display limits."""

    def test_super_ideas_stat_uses_db_count_not_display_limit(self):
        """The Super Ideas stat must come from stats.super_ideas (COUNT),
        not from counting the list_super_ideas display (capped at 6).

        This tests the template logic — stats.super_ideas (from DB COUNT query)
        must be what feeds the stat-number element, not ns.active_super.
        """
        template_path = (
            "/opt/vmdata/project-forge/src/project_forge/web/templates/dashboard.html"
        )
        with open(template_path) as f:
            content = f.read()
        # The stat-number for Super Ideas must use stats.super_ideas
        # NOT ns.active_super (which counts the limited list)
        assert "ns.active_super" not in content, (
            "Dashboard uses ns.active_super which counts the limited display list, "
            "not the true DB count. Use stats.super_ideas instead."
        )

    def test_js_updates_contributed_not_avg_score_at_index_4(self):
        """JS numbers[4] must update the Contributed card, not Avg Score."""
        js_path = "/opt/vmdata/project-forge/src/project_forge/web/static/app.js"
        with open(js_path) as f:
            content = f.read()
        # numbers[4] must reference contributed, not avg_feasibility_score
        # Find the block containing numbers[4]
        assert "numbers[4].textContent = stats.ideas_by_status.contributed" in content or \
               "numbers[4].textContent = (stats.ideas_by_status.contributed" in content, (
            "JS numbers[4] must set Contributed count, not avg_feasibility_score"
        )

    def test_js_updates_avg_score_at_index_5(self):
        """JS must update the Avg Score card at numbers[5]."""
        js_path = "/opt/vmdata/project-forge/src/project_forge/web/static/app.js"
        with open(js_path) as f:
            content = f.read()
        assert "numbers[5].textContent = stats.avg_feasibility_score" in content, (
            "JS must update numbers[5] with avg_feasibility_score"
        )
