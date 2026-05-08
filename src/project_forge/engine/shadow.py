"""Shadow validation — verify generation patches actually move metrics.

Phase 3 (issue #56). Provides the gate that prevents SI from shipping a
generation patch that fails to move its declared target metric.

Pure functions for parsing target-metric declarations and comparing
metric snapshots from engine/telemetry. The actual shadow generation
pipeline (run N generations against a temp DB) is built on top in
scripts/shadow_generate.py.
"""

from __future__ import annotations

import re
from typing import Literal

from project_forge.models import IdeaCategory

Direction = Literal["drop", "rise"]

# Matches "Target metric: <name> should drop|rise"
_TARGET_METRIC_RE = re.compile(
    r"target\s*metric\s*:\s*([\w\[\]\-]+)\s+should\s+(drop|rise)",
    re.IGNORECASE,
)


def parse_target_metric(text: str) -> tuple[str, Direction] | None:
    """Extract the (metric_name, direction) declaration from a string.

    Recognized form: "Target metric: <name> should <drop|rise>".
    Returns None if not found.
    """
    m = _TARGET_METRIC_RE.search(text or "")
    if not m:
        return None
    name = m.group(1)
    direction = m.group(2).lower()
    return name, direction  # type: ignore[return-value]


def metric_value(snapshot: dict, metric_name: str) -> float | None:
    """Look up a metric in a telemetry snapshot.

    Supports:
    - "filter_rate" → mean of filter_rate_by_category values
    - "filter_rate[<category>]" → specific category
    - "novelty" → most-recent novelty_trend value
    - "saturation[<word>]" → count of that word in saturation_per_concept
    - "coverage_gaps" → count of gap categories
    """
    base, idx = _split_index(metric_name)

    if base == "filter_rate":
        rates = snapshot.get("filter_rate_by_category", {})
        if idx is None:
            if not rates:
                return None
            return sum(rates.values()) / len(rates)
        try:
            cat = IdeaCategory(idx)
        except ValueError:
            return None
        return rates.get(cat)

    if base == "novelty":
        trend = snapshot.get("novelty_trend", [])
        if not trend:
            return None
        return float(trend[-1][1])

    if base == "saturation":
        for word, count in snapshot.get("saturation_per_concept", []):
            if word == idx:
                return float(count)
        return None

    if base == "coverage_gaps":
        return float(len(snapshot.get("coverage_gaps", [])))

    return None


def _split_index(metric_name: str) -> tuple[str, str | None]:
    if "[" in metric_name and metric_name.endswith("]"):
        base, _, rest = metric_name.partition("[")
        return base, rest[:-1]
    return metric_name, None


def validate_patch_against_metrics(
    baseline: dict,
    after: dict,
    metric_name: str,
    direction: Direction,
    *,
    epsilon: float = 1e-6,
) -> tuple[bool, str]:
    """Decide whether a patch can ship based on its declared target metric.

    Returns (passed, reason). Caller logs the reason on rejection.
    """
    before_v = metric_value(baseline, metric_name)
    after_v = metric_value(after, metric_name)

    if before_v is None or after_v is None:
        return False, f"unknown/missing metric: {metric_name}"

    delta = after_v - before_v
    if abs(delta) < epsilon:
        return False, f"noop: {metric_name} unchanged at {before_v:.4f}"

    if direction == "drop":
        if delta < 0:
            return True, f"{metric_name}: {before_v:.4f} → {after_v:.4f} (dropped {abs(delta):.4f})"
        return False, f"regress: {metric_name} rose {before_v:.4f} → {after_v:.4f}"

    # direction == "rise"
    if delta > 0:
        return True, f"{metric_name}: {before_v:.4f} → {after_v:.4f} (rose {delta:.4f})"
    return False, f"regress: {metric_name} dropped {before_v:.4f} → {after_v:.4f}"
