"""Unit coverage for the pure helper functions in engine/dedup.py (#99).

engine/dedup.py was flagged as untested — these cover the string-similarity
and name-normalization helpers that don't require a database.
"""

from __future__ import annotations

from project_forge.engine.dedup import (
    _name_token_jaccard,
    _strip_vertical_name,
    _super_base_name,
    tagline_similarity,
)


class TestTaglineSimilarity:
    def test_identical_taglines_score_one(self):
        assert tagline_similarity("automated dashboard tool", "automated dashboard tool") == 1.0

    def test_both_empty_score_one(self):
        assert tagline_similarity("", "") == 1.0

    def test_one_empty_scores_zero(self):
        assert tagline_similarity("hello world", "") == 0.0
        assert tagline_similarity("", "hello world") == 0.0

    def test_partial_overlap(self):
        score = tagline_similarity("automated dashboard tool", "automated dashboard system")
        assert score == 0.5

    def test_no_overlap_scores_zero(self):
        assert tagline_similarity("apples oranges", "trucks planes") == 0.0


class TestNameTokenJaccard:
    def test_identical_names_score_one(self):
        assert _name_token_jaccard("Pqc Tracker", "Pqc Tracker") == 1.0

    def test_disjoint_names_score_zero(self):
        assert _name_token_jaccard("Alpha Beta", "Gamma Delta") == 0.0

    def test_both_empty_score_one(self):
        assert _name_token_jaccard("", "") == 1.0


class TestStripVerticalName:
    def test_strips_for_vertical_suffix(self):
        assert _strip_vertical_name("Pqc Tracker for Healthcare") == "pqc tracker"

    def test_no_match_returns_none(self):
        assert _strip_vertical_name("Simple Name") is None


class TestSuperBaseName:
    def test_strips_parenthetical(self):
        assert _super_base_name("[SUPER] Threat Engine (Attack & Defense)") == "threat engine"

    def test_strips_synthesis_suffix(self):
        assert _super_base_name("[SUPER] Well Known Defense Suite") == "well known"

    def test_normalizes_hyphens_and_synthesis_suffix(self):
        assert _super_base_name("[SUPER] Data-Cardinality Operations Center") == "data cardinality"
