"""TDD: Shadow validation — verify generation patches actually move metrics.

Phase 3 (issue #56). SI's generation-mode patches now declare a target
metric; this phase enforces it. After applying a patch, the runner
generates a small batch of ideas in an isolated temp DB and compares
metric snapshots before/after. If the declared target didn't move
in the right direction, the patch is rejected.

Tests target the pure logic:
- parse_target_metric: extracts metric name + direction from market_analysis
- metric_value: looks up a metric in a telemetry snapshot
- validate_patch_against_metrics: gate returns (pass, reason)
"""

from __future__ import annotations

import pytest

from project_forge.engine.shadow import (
    metric_value,
    parse_target_metric,
    validate_patch_against_metrics,
)
from project_forge.models import IdeaCategory

# ── parse_target_metric ──────────────────────────────────────────────


class TestParseTargetMetric:
    def test_parses_simple_metric_should_drop(self):
        result = parse_target_metric("Target metric: filter_rate should drop.")
        assert result is not None
        name, direction = result
        assert name == "filter_rate"
        assert direction == "drop"

    def test_parses_indexed_metric(self):
        result = parse_target_metric(
            "Target metric: filter_rate[security-tool] should drop by 10%.",
        )
        assert result is not None
        name, direction = result
        assert name == "filter_rate[security-tool]"
        assert direction == "drop"

    def test_parses_should_rise(self):
        result = parse_target_metric(
            "Target metric: novelty should rise. Current 0.91 → expected 0.85.",
        )
        assert result is not None
        name, direction = result
        assert name == "novelty"
        assert direction == "rise"

    def test_returns_none_when_missing(self):
        assert parse_target_metric("This patch will be cool.") is None

    def test_case_insensitive(self):
        result = parse_target_metric("TARGET METRIC: filter_rate SHOULD drop")
        assert result is not None
        assert result[0] == "filter_rate"
        assert result[1] == "drop"


# ── metric_value ─────────────────────────────────────────────────────


def _snapshot():
    return {
        "filter_rate_by_category": {
            IdeaCategory.SECURITY_TOOL: 0.85,
            IdeaCategory.PRIVACY: 0.40,
        },
        "saturation_per_concept": [("certificate", 1357), ("detection", 952)],
        "novelty_trend": [("2026-05-07", 0.91), ("2026-05-08", 0.93)],
        "diversity_lever_usage": {"contrarian": 0.33},
        "coverage_gaps": [IdeaCategory.SELF_IMPROVEMENT],
    }


class TestMetricValue:
    def test_indexed_filter_rate(self):
        v = metric_value(_snapshot(), "filter_rate[security-tool]")
        assert v == pytest.approx(0.85)

    def test_aggregate_filter_rate(self):
        # Average across categories when no index
        v = metric_value(_snapshot(), "filter_rate")
        assert v == pytest.approx((0.85 + 0.40) / 2)

    def test_latest_novelty(self):
        # 'novelty' resolves to most-recent novelty_trend value
        v = metric_value(_snapshot(), "novelty")
        assert v == pytest.approx(0.93)

    def test_unknown_metric_returns_none(self):
        assert metric_value(_snapshot(), "unobtanium") is None


# ── validate_patch_against_metrics ───────────────────────────────────


class TestValidatePatch:
    def test_accepts_when_drop_metric_dropped(self):
        baseline = _snapshot()
        after = {**baseline,
                 "filter_rate_by_category": {
                     IdeaCategory.SECURITY_TOOL: 0.70,  # was 0.85
                     IdeaCategory.PRIVACY: 0.40,
                 }}
        ok, reason = validate_patch_against_metrics(
            baseline, after, "filter_rate[security-tool]", "drop",
        )
        assert ok, reason

    def test_rejects_when_drop_metric_rose(self):
        baseline = _snapshot()
        after = {**baseline,
                 "filter_rate_by_category": {
                     IdeaCategory.SECURITY_TOOL: 0.92,  # got worse
                     IdeaCategory.PRIVACY: 0.40,
                 }}
        ok, reason = validate_patch_against_metrics(
            baseline, after, "filter_rate[security-tool]", "drop",
        )
        assert not ok
        assert "regress" in reason.lower() or "worse" in reason.lower() or "rose" in reason.lower()

    def test_rejects_when_metric_unchanged(self):
        baseline = _snapshot()
        after = {**baseline}
        ok, reason = validate_patch_against_metrics(
            baseline, after, "filter_rate[security-tool]", "drop",
        )
        assert not ok
        assert "no" in reason.lower() or "unchanged" in reason.lower() or "noop" in reason.lower()

    def test_accepts_when_rise_metric_rose(self):
        baseline = _snapshot()
        after = {
            **baseline,
            "novelty_trend": [("2026-05-07", 0.91), ("2026-05-08", 0.85)],
            # Lower similarity = HIGHER novelty
        }
        # Note: target is 'novelty' which means lower similarity is better.
        # We model 'novelty rise' as similarity DROP — so caller must use
        # the right direction. Test that the comparator respects the literal direction.
        # If author said 'rise' and value went DOWN, that's a regression.
        ok, _ = validate_patch_against_metrics(
            baseline, after, "novelty", "rise",
        )
        # 0.85 < 0.93, so for direction=rise this is a regress
        assert not ok

    def test_unknown_metric_rejects(self):
        ok, reason = validate_patch_against_metrics(
            _snapshot(), _snapshot(), "unobtanium", "drop",
        )
        assert not ok
        assert "unknown" in reason.lower() or "missing" in reason.lower()
