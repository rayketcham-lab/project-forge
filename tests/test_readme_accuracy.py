"""The public README must describe the engine that actually exists.

This repo is open-sourced, so the README is the only thing most readers
will ever check the claims against. It has drifted before — it advertised
`/money-bots` as fundability-ranked product ideas for a full release after
the board had been rebuilt around a different axis entirely.

These tests pin the claims that are mechanically checkable: board
groupings and their sizes, the scoring axes, the environment variables and
their defaults, the cadence table, and the counts the prose asserts. Prose
that cannot be checked mechanically is not tested here — it is reviewed by
hand — but anything with a number in it should fail loudly when the code
moves underneath it.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

README = Path(__file__).resolve().parent.parent / "README.md"


@pytest.fixture(scope="module")
def readme() -> str:
    return README.read_text()


class TestVersionAndCounts:
    def test_badge_matches_package_version(self, readme: str):
        from project_forge import __version__

        m = re.search(r"img\.shields\.io/badge/version-(\d+\.\d+)-", readme)
        assert m
        assert m.group(1) == ".".join(__version__.split(".")[:2])

    def test_claimed_playbook_size_is_real(self, readme: str):
        from project_forge.engine.strategy_library import STRATEGY_LIBRARY

        m = re.search(r"playbook\*\* of (\d+) mechanisms", readme)
        assert m, "README no longer states the playbook size"
        assert int(m.group(1)) == len(STRATEGY_LIBRARY)

    def test_claimed_probe_repo_count_is_real(self, readme: str):
        from project_forge.feeds.venue_probe import PROBE_REPOS

        m = re.search(r"across (\d+) repositories", readme)
        assert m, "README no longer states how many repos the probe sweeps"
        assert int(m.group(1)) == len(PROBE_REPOS)


class TestBoardTable:
    def test_money_board_categories_are_listed_correctly(self, readme: str):
        from project_forge.models import MONEY_CATEGORIES

        row = next(line for line in readme.splitlines() if line.startswith("| **/money-bots**"))
        for cat in MONEY_CATEGORIES:
            assert cat.value in row, f"{cat.value} missing from the boards table"
        assert "bot_edge_score" in row

    @pytest.mark.parametrize(
        ("board", "grouping"),
        [
            ("/claude-lab", "CLAUDE_LAB_CATEGORIES"),
            ("/crypto", "CRYPTO_CATEGORIES"),
            ("/cashflow", "CASHFLOW_CATEGORIES"),
            ("/pki", "PKI_CATEGORIES"),
        ],
    )
    def test_board_sizes_match_the_groupings(self, readme: str, board: str, grouping: str):
        import project_forge.models as models

        size = len(getattr(models, grouping))
        row = next(line for line in readme.splitlines() if line.startswith(f"| **{board}**"))
        assert str(size) in row, f"{board} row does not state {size} categories"

    def test_every_axis_in_the_table_exists_on_the_model(self, readme: str):
        from project_forge.models import Idea

        axes = re.findall(r"^\| `(\w+_score)` \|", readme, re.MULTILINE)
        assert axes, "axis table missing"
        for axis in axes:
            assert axis in Idea.model_fields, f"README documents a non-existent axis {axis}"


class TestEnvTable:
    """Every documented variable must be one the code actually reads."""

    def _documented(self, readme: str) -> dict[str, str]:
        rows = re.findall(r"^\| `(FORGE_[A-Z_]+)` \| `?([^|`]*)`? \|", readme, re.MULTILINE)
        return {name: default.strip() for name, default in rows}

    def test_documented_vars_are_read_somewhere(self, readme: str):
        """Either the literal appears in source, or it is a Settings field —
        pydantic-settings binds FORGE_DB_PATH to `db_path` via the env
        prefix, so the string itself is never written down."""
        from project_forge.config import Settings

        src = Path(__file__).resolve().parent.parent / "src"
        blob = "\n".join(p.read_text() for p in src.rglob("*.py"))
        settings_fields = {f"FORGE_{name.upper()}" for name in Settings.model_fields}

        unknown = [name for name in self._documented(readme) if name not in blob and name not in settings_fields]
        assert not unknown, f"README documents variables nothing reads: {unknown}"

    @pytest.mark.parametrize(
        ("var", "expected"),
        [
            ("FORGE_BOT_GEN_MODEL", "sonnet"),
            ("FORGE_BOT_REVIEW_MODEL", "opus"),
            ("FORGE_BOT_INTERVAL_HOURS", 2.0),
            ("FORGE_LLM_TIMEOUT_SEC", 420),
        ],
    )
    def test_documented_defaults_match_the_code(self, readme: str, var: str, expected):
        documented = self._documented(readme)
        assert var in documented, f"{var} is not documented"

        if var.endswith("_MODEL"):
            from project_forge.engine.llm_backend import _ROLE_DEFAULTS

            role = "generate" if "GEN" in var else "review"
            assert _ROLE_DEFAULTS[role] == (var, expected)
            assert documented[var] == expected
        elif var == "FORGE_LLM_TIMEOUT_SEC":
            from project_forge.engine.llm_backend import _timeout_from_env

            assert _timeout_from_env() == expected
            assert documented[var] == str(expected)
        else:
            from project_forge.web.lifespan_scheduler import BOT_INTERVAL

            assert BOT_INTERVAL.total_seconds() / 3600 == expected
            assert documented[var] == str(int(expected))


class TestCadenceTable:
    def test_documented_cadences_are_registered(self, readme: str):
        from project_forge.web.lifespan_scheduler import cadence_names

        registered = set(cadence_names())
        # The table names each cadence by its interval variable; map the
        # money-bot one explicitly since it is the newest claim.
        assert "bot_strategy" in registered
        assert "FORGE_BOT_INTERVAL_HOURS" in readme


class TestNoStaleClaims:
    """Claims the code has since contradicted."""

    def test_money_board_is_not_described_as_fundability_ranked(self, readme: str):
        row = next(line for line in readme.splitlines() if line.startswith("| **/money-bots**"))
        assert "fundability_score" not in row

    def test_us_only_rule_is_stated(self, readme: str):
        assert "US-only" in readme or "US-eligible" in readme

    def test_flagged_handling_is_described_accurately(self, readme: str):
        """The board stopped showing flagged strategies as cards; the README
        must not still promise they are on it."""
        assert "shows only what passed" in readme
