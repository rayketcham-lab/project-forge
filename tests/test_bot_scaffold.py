"""The runnable half: a BotSpec becomes a repo you can actually run.

The board's whole claim is that these strategies are executable, so the
scaffold has to be a real skeleton and not a README with ambitions. What
these tests pin is mostly SAFETY, because this scaffold is one config file
away from touching real money:

  * paper mode is the default, in config and in code;
  * the venue client is an explicit stub that RAISES — it must be
    impossible to place a live order against a client nobody implemented;
  * every kill criterion from the spec becomes a guard in risk.py;
  * every API primitive from the spec becomes a named stub, so the first
    thing the operator does is check them against the venue's real docs;
  * every generated Python file compiles.
"""

from __future__ import annotations

import py_compile
import tomllib
from pathlib import Path

import pytest

from project_forge.models import BotSpec, BotVenueFamily, Idea, IdeaCategory
from project_forge.scaffold.bot_builder import render_bot_scaffold


def _spec(**over) -> BotSpec:
    base = dict(
        venue="Polymarket",
        venue_url="https://docs.polymarket.com/rewards",
        family=BotVenueFamily.PREDICTION_MARKETS,
        api_primitives=["CLOB REST order placement", "websocket book feed", "rewards endpoint"],
        mechanism="Venue pays a published per-minute liquidity reward for two-sided quotes.",
        capital_floor_usd=500.0,
        capital_target_usd=10000.0,
        expected_return="Pro-rata share of the published reward budget",
        edge_decay="Fixed pool split pro-rata — yield falls as makers arrive",
        kill_criteria=[
            "reward per minute falls below fees plus adverse selection",
            "inventory exceeds 40% of deployed capital",
        ],
        validation_plan=["one book, floor capital, 14 days, measure realised reward share"],
        legality_note="Published venue program with public rules; no manipulation",
        human_touchpoints="Weekly book selection review",
        surviving_objection="Capacity caps this near $20k of deployed size",
    )
    base.update(over)
    return BotSpec(**base)


def _idea(**over) -> Idea:
    base = dict(
        name="Reward Minute Maker",
        tagline="rest two-sided quotes and collect the published reward budget",
        description="Quotes both sides of high-reward books inside the qualifying spread.",
        category=IdeaCategory.INCENTIVE_CAPTURE,
        market_analysis="Reward budgets are published per market; few makers quote them.",
        feasibility_score=0.72,
        mvp_scope="One book at floor capital with a hard inventory kill switch.",
        tech_stack=["python", "websockets"],
    )
    base.update(over)
    idea = Idea(**base)
    if "bot_spec" not in over:
        idea.bot_spec = _spec()
    return idea


@pytest.fixture
def built(tmp_path: Path) -> Path:
    return render_bot_scaffold(_idea(), tmp_path)


class TestLayout:
    def test_creates_the_expected_files(self, built: Path):
        for rel in (
            "README.md",
            "VALIDATION.md",
            "pyproject.toml",
            "config.example.toml",
            ".gitignore",
            "tests/test_risk.py",
        ):
            assert (built / rel).is_file(), f"missing {rel}"

    def test_package_modules_exist(self, built: Path):
        pkg = built / "src" / "reward_minute_maker"
        for mod in ("__init__.py", "config.py", "venue.py", "strategy.py", "risk.py", "ledger.py", "runner.py"):
            assert (pkg / mod).is_file(), f"missing {mod}"

    def test_refuses_an_idea_with_no_spec(self, tmp_path: Path):
        with pytest.raises(ValueError, match="BotSpec"):
            render_bot_scaffold(_idea(bot_spec=None), tmp_path)


class TestGeneratedCodeIsValid:
    def test_every_python_file_compiles(self, built: Path):
        files = list(built.rglob("*.py"))
        assert files
        for path in files:
            py_compile.compile(str(path), doraise=True)

    def test_pyproject_parses(self, built: Path):
        data = tomllib.loads((built / "pyproject.toml").read_text())
        assert data["project"]["name"]

    def test_config_example_parses(self, built: Path):
        data = tomllib.loads((built / "config.example.toml").read_text())
        assert data["bot"]["dry_run"] is True
        assert data["risk"]["max_capital_usd"] >= 500.0


class TestSafetyDefaults:
    def test_dry_run_is_the_code_default_too(self, built: Path):
        """A config file the operator forgets to copy must still be safe."""
        config = (built / "src" / "reward_minute_maker" / "config.py").read_text()
        assert "dry_run: bool = True" in config

    def test_venue_client_stubs_raise(self, built: Path):
        venue = (built / "src" / "reward_minute_maker" / "venue.py").read_text()
        assert "NotImplementedError" in venue
        # No silent pass-through that would look implemented.
        assert "        pass\n" not in venue

    def test_every_api_primitive_becomes_a_stub(self, built: Path):
        venue = (built / "src" / "reward_minute_maker" / "venue.py").read_text()
        for prim in _spec().api_primitives:
            assert prim in venue, f"{prim} not represented in the venue client"

    def test_venue_docs_url_is_in_the_client(self, built: Path):
        venue = (built / "src" / "reward_minute_maker" / "venue.py").read_text()
        assert "https://docs.polymarket.com/rewards" in venue

    def test_every_kill_criterion_becomes_a_guard(self, built: Path):
        risk = (built / "src" / "reward_minute_maker" / "risk.py").read_text()
        for criterion in _spec().kill_criteria:
            assert criterion in risk

    def test_runner_refuses_live_mode_without_explicit_optin(self, built: Path):
        runner = (built / "src" / "reward_minute_maker" / "runner.py").read_text()
        assert "dry_run" in runner
        assert "FORGE_BOT_I_UNDERSTAND_THE_RISK" in runner


class TestDocsCarryTheStrategy:
    def test_readme_carries_the_spec(self, built: Path):
        readme = (built / "README.md").read_text()
        for fragment in (
            "Polymarket",
            "published per-minute liquidity reward",
            "Fixed pool split pro-rata",
            "inventory exceeds 40% of deployed capital",
            "no manipulation",
        ):
            assert fragment in readme

    def test_readme_publishes_the_unanswered_objection(self, built: Path):
        readme = (built / "README.md").read_text()
        assert "Capacity caps this near $20k" in readme

    def test_readme_does_not_promise_returns(self, built: Path):
        readme = (built / "README.md").read_text().lower()
        for claim in ("guaranteed return", "risk-free", "cannot lose"):
            assert claim not in readme

    def test_validation_doc_carries_the_plan(self, built: Path):
        validation = (built / "VALIDATION.md").read_text()
        assert "one book, floor capital, 14 days" in validation
