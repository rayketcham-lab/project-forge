"""Turn a BotSpec into a repo the operator can actually run.

The money board claims its strategies are executable. This is where that
claim is cashed: a spec becomes a Python package with the venue client, the
strategy loop, the risk guards and the ledger already wired to each other,
plus the README and validation checklist that say what has to be proven
before real capital goes near it.

What this deliberately does NOT do is implement the venue. Every API
primitive the spec named becomes a stub that RAISES NotImplementedError
with the venue's documentation URL attached. Generating a plausible-looking
client for an API nobody checked would be the single most dangerous thing
this codebase could produce — it would look finished, and it would place
real orders against guessed endpoints. The first job the scaffold hands its
operator is "go read the docs and fill these in".

Safety defaults, all enforced by tests:
  * dry-run is the default in the config file AND in the dataclass, so a
    forgotten config file fails safe;
  * live mode additionally requires an explicit environment variable, so
    flipping one boolean is not enough to start trading;
  * a hard capital ceiling is seeded from the spec's own floor;
  * every kill criterion from the spec becomes a guard that the runner
    evaluates before every cycle.
"""

from __future__ import annotations

import re
from pathlib import Path

from project_forge.models import BotSpec, Idea
from project_forge.scaffold.builder import sanitize_repo_name

# Flipping dry_run alone must not be enough to trade real money.
LIVE_MODE_ENV = "FORGE_BOT_I_UNDERSTAND_THE_RISK"


def _pkg_name(repo_name: str) -> str:
    name = repo_name.replace("-", "_")
    if not name or not name[0].isalpha():
        name = f"bot_{name}"
    return re.sub(r"[^a-z0-9_]", "", name.lower()) or "bot"


def _slug(text: str, fallback: str) -> str:
    """A python identifier from free text — used for guard function names."""
    slug = re.sub(r"[^a-z0-9]+", "_", text.lower()).strip("_")
    slug = re.sub(r"_+", "_", slug)[:48].strip("_")
    if not slug or not slug[0].isalpha():
        slug = f"{fallback}_{slug}" if slug else fallback
    return slug


def _readme(idea: Idea, spec: BotSpec, pkg: str) -> str:
    kill = "\n".join(f"- {k}" for k in spec.kill_criteria)
    validation = "\n".join(f"- [ ] {v}" for v in spec.validation_plan) or "- [ ] (no plan recorded)"
    api = "\n".join(f"- `{p}`" for p in spec.api_primitives)
    objection = (
        f"\n## The objection nobody answered\n\n> {spec.surviving_objection}\n\n"
        "This survived the red-team panel. It is not a footnote — read it before funding this.\n"
        if spec.surviving_objection
        else ""
    )
    return f"""# {idea.name}

> {idea.tagline}

{idea.description}

## Where the money comes from

{spec.mechanism}

**Expected return:** {spec.expected_return or "not stated"}

**How this edge decays:** {spec.edge_decay}

Every edge decays. If the return here stops clearing costs, the correct
action is to switch the bot off, not to size up.
{objection}
## Venue

- **Venue:** {spec.venue} ({spec.family.value})
- **Documentation:** {spec.venue_url or "(none cited — find it before writing any client code)"}
- **Eligibility and terms:** confirm you are permitted to trade here and that
  programmatic access is allowed by the current terms of service.

### API surface this strategy needs

{api}

Each of these is a stub in `src/{pkg}/venue.py` that raises on call. Fill them
in against the venue's real documentation. Do not guess an endpoint.

## Capital

- **Floor:** ${spec.capital_floor_usd:,.0f} — below this the strategy cannot run
- **Target:** ${spec.capital_target_usd:,.0f} — where it is worth running

## When it switches itself off

{kill}

These are implemented as guards in `src/{pkg}/risk.py`. They are evaluated
before every cycle, and a tripped guard halts the bot.

## Prove it before you fund it

See `VALIDATION.md`:

{validation}

## Legality

{spec.legality_note or "Not stated — establish this before deploying capital."}

This bot must not depend on market manipulation, wash trading, spoofing,
front-running, non-public information, exploiting a defect, or evading a
venue's terms. If achieving the return requires any of those, delete this
repository.

## Human touchpoints

{spec.human_touchpoints or "None stated — decide what you will check, and how often."}

## Running it

```bash
cp config.example.toml config.toml   # edit it
python -m {pkg}.runner               # paper mode: places no orders
```

Live mode needs BOTH `dry_run = false` in the config and
`{LIVE_MODE_ENV}=1` in the environment. That is deliberate friction.

---

*Scaffolded by Project Forge from a strategy spec. Nothing here has been
backtested, and no return is promised or implied.*
"""


def _validation_doc(idea: Idea, spec: BotSpec) -> str:
    steps = "\n".join(f"- [ ] {v}" for v in spec.validation_plan) or "- [ ] (no plan recorded)"
    kill = "\n".join(f"- [ ] {k} — verify this guard actually fires in a dry run" for k in spec.kill_criteria)
    return f"""# Validation — {idea.name}

Do not scale this until every box is ticked. The point of the exercise is to
find out that the edge is not there while it is still cheap to find out.

## Prove the mechanism

{steps}

## Prove the stops

{kill}

## Prove the accounting

- [ ] Every action the bot takes appears in the ledger
- [ ] Realised P&L reconciles against the venue's own statements
- [ ] Fees and slippage are measured, not assumed
- [ ] The measured return still clears the hurdle after those costs

## Prove it survives being ignored

- [ ] Runs unattended for the full validation window
- [ ] Recovers from an API outage without manual intervention
- [ ] Recovers from a disconnected market-data feed
- [ ] Halts, rather than guesses, on any unexplained state

## Decision

- [ ] The measured edge matches the thesis → scale toward the target
- [ ] It does not → switch it off and write down why
"""


def _config_py(idea: Idea, spec: BotSpec) -> str:
    return f'''"""Configuration. Paper mode is the default, everywhere."""

from __future__ import annotations

import os
import tomllib
from dataclasses import dataclass, field
from pathlib import Path

# Live mode requires this environment variable in ADDITION to dry_run=false.
LIVE_MODE_ENV = "{LIVE_MODE_ENV}"


@dataclass
class RiskConfig:
    """Hard ceilings. Seeded from the strategy's own stated capital floor."""

    max_capital_usd: float = {spec.capital_floor_usd:.1f}
    max_position_fraction: float = 0.4
    halt_on_unexplained_state: bool = True


@dataclass
class BotConfig:
    """Runtime configuration.

    `dry_run` defaults to True in code as well as in the example config, so a
    missing or malformed config file fails safe instead of trading."""

    venue: str = "{spec.venue}"
    dry_run: bool = True
    poll_seconds: float = 5.0
    risk: RiskConfig = field(default_factory=RiskConfig)

    @property
    def live_enabled(self) -> bool:
        """Live trading needs the config flag AND the environment opt-in."""
        return (not self.dry_run) and os.environ.get(LIVE_MODE_ENV) == "1"


def load_config(path: str | Path = "config.toml") -> BotConfig:
    """Load config, falling back to the safe defaults if it is missing."""
    p = Path(path)
    if not p.is_file():
        return BotConfig()
    data = tomllib.loads(p.read_text())
    bot = data.get("bot", {{}})
    risk = data.get("risk", {{}})
    return BotConfig(
        venue=str(bot.get("venue", "{spec.venue}")),
        dry_run=bool(bot.get("dry_run", True)),
        poll_seconds=float(bot.get("poll_seconds", 5.0)),
        risk=RiskConfig(
            max_capital_usd=float(risk.get("max_capital_usd", {spec.capital_floor_usd:.1f})),
            max_position_fraction=float(risk.get("max_position_fraction", 0.4)),
            halt_on_unexplained_state=bool(risk.get("halt_on_unexplained_state", True)),
        ),
    )
'''


def _venue_py(idea: Idea, spec: BotSpec) -> str:
    docs = spec.venue_url or "(no documentation URL was cited for this venue)"
    methods: list[str] = []
    for primitive in spec.api_primitives:
        method = _slug(primitive, "call")
        methods.append(
            f'''    def {method}(self, **kwargs):
        """{primitive}

        Venue documentation: {docs}

        Implement this against the real API. Verify the endpoint, the auth
        scheme, the rate limit and the error semantics before you rely on it.
        """
        raise NotImplementedError(
            "{primitive} is not implemented. Read {docs} and implement it."
        )
'''
        )
    body = "\n".join(methods)
    return f'''"""Venue client for {spec.venue}.

DELIBERATELY UNIMPLEMENTED. Every method below raises.

A generated client for an API nobody verified is worse than no client at
all: it looks finished, and it sends real orders to guessed endpoints. Each
stub names the primitive the strategy needs and the documentation to check
it against.

Credentials come from the environment. Never commit them.
"""

from __future__ import annotations

import os


class VenueError(RuntimeError):
    """Raised when the venue rejects or cannot serve a request."""


class VenueClient:
    """Thin client for {spec.venue} ({spec.family.value})."""

    docs_url = "{docs}"

    def __init__(self, *, api_key: str | None = None, dry_run: bool = True):
        self.api_key = api_key or os.environ.get("VENUE_API_KEY")
        self.dry_run = dry_run

    def require_credentials(self) -> str:
        """Fail loudly rather than silently trading as nobody."""
        if not self.api_key:
            raise VenueError("VENUE_API_KEY is not set")
        return self.api_key

{body}'''


def _risk_py(idea: Idea, spec: BotSpec) -> str:
    guards: list[str] = []
    names: list[str] = []
    for i, criterion in enumerate(spec.kill_criteria):
        name = _slug(criterion, f"guard_{i}")
        names.append(name)
        guards.append(
            f'''    def {name}(self, state: dict) -> str | None:
        """{criterion}

        Return a reason string to HALT, or None to continue. Implement the
        measurement — an unimplemented guard is treated as tripped, because
        a stop nobody wrote is not a stop.
        """
        return "unimplemented guard: {criterion}"
'''
        )
    body = "\n".join(guards)
    calls = "\n".join(f"            self.{n}," for n in names)
    return f'''"""Risk guards derived from the strategy's own kill criteria.

Fail-closed on purpose: a guard that has not been implemented returns a halt
reason. A bot whose stops are TODO comments should not be running.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass
class RiskLimits:
    max_capital_usd: float
    max_position_fraction: float


class RiskManager:
    """Evaluates every kill criterion before each cycle."""

    def __init__(self, limits: RiskLimits):
        self.limits = limits
        self.halted_reason: str | None = None

{body}
    def capital_ceiling(self, state: dict) -> str | None:
        """Hard ceiling independent of the strategy's own criteria."""
        deployed = float(state.get("deployed_usd", 0.0))
        if deployed > self.limits.max_capital_usd:
            return f"deployed ${{deployed:,.0f}} exceeds ceiling ${{self.limits.max_capital_usd:,.0f}}"
        return None

    def check(self, state: dict) -> str | None:
        """Run every guard. First halt reason wins."""
        for guard in (
{calls}
            self.capital_ceiling,
        ):
            reason = guard(state)
            if reason:
                self.halted_reason = reason
                return reason
        return None
'''


def _strategy_py(idea: Idea, spec: BotSpec) -> str:
    return f'''"""The strategy loop for {idea.name}.

Mechanism: {spec.mechanism}

Decay: {spec.edge_decay}

This is a skeleton. `decide()` is where the actual edge lives, and it is
intentionally empty — writing a plausible-looking implementation against an
unverified venue client would produce a bot that trades on guesses.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass
class Intent:
    """One thing the strategy wants to do this cycle."""

    action: str
    detail: dict


class Strategy:
    """{idea.tagline}"""

    def __init__(self, client, config):
        self.client = client
        self.config = config

    def observe(self) -> dict:
        """Read the venue state this strategy depends on.

        Fill this in with the API primitives listed in the README. Return a
        dict describing the world; the risk guards read the same dict.
        """
        return {{"deployed_usd": 0.0, "observed": False}}

    def decide(self, state: dict) -> list[Intent]:
        """Turn observed state into intents. This is the edge — implement it.

        Every intent must be justified by the mechanism above. If an intent
        cannot be traced back to it, the strategy has drifted into guessing.
        """
        return []

    def execute(self, intents: list[Intent]) -> list[dict]:
        """Carry out intents, or log them in dry-run mode."""
        results = []
        for intent in intents:
            if self.config.dry_run:
                results.append({{"intent": intent.action, "executed": False, "reason": "dry-run"}})
                continue
            raise NotImplementedError(
                "Live execution is not implemented. Implement the venue client first."
            )
        return results
'''


def _ledger_py(idea: Idea) -> str:
    return '''"""Append-only record of everything the bot did.

Reconciliation is not optional for a bot handling money: if the ledger and
the venue's own statements disagree, the correct response is to halt.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path


class Ledger:
    """One JSON line per event."""

    def __init__(self, path: str | Path = "ledger.jsonl"):
        self.path = Path(path)

    def record(self, event: str, **fields) -> dict:
        entry = {"at": datetime.now(UTC).isoformat(), "event": event, **fields}
        with self.path.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(entry) + "\\n")
        return entry

    def read_all(self) -> list[dict]:
        if not self.path.is_file():
            return []
        out = []
        for line in self.path.read_text(encoding="utf-8").splitlines():
            if line.strip():
                out.append(json.loads(line))
        return out
'''


def _runner_py(idea: Idea, spec: BotSpec, pkg: str) -> str:
    return f'''"""Entry point. Paper mode unless explicitly told otherwise, twice.

    python -m {pkg}.runner

Live trading requires BOTH `dry_run = false` in config.toml AND the
environment variable {LIVE_MODE_ENV}=1. One switch is too easy to flip by
accident when the thing on the other side is real money.
"""

from __future__ import annotations

import logging
import time

from {pkg}.config import LIVE_MODE_ENV, load_config
from {pkg}.ledger import Ledger
from {pkg}.risk import RiskLimits, RiskManager
from {pkg}.strategy import Strategy
from {pkg}.venue import VenueClient

logger = logging.getLogger(__name__)


def build(config=None):
    """Wire the pieces together."""
    config = config or load_config()
    client = VenueClient(dry_run=not config.live_enabled)
    strategy = Strategy(client, config)
    risk = RiskManager(RiskLimits(
        max_capital_usd=config.risk.max_capital_usd,
        max_position_fraction=config.risk.max_position_fraction,
    ))
    return config, strategy, risk, Ledger()


def cycle(strategy: Strategy, risk: RiskManager, ledger: Ledger) -> str | None:
    """One pass. Returns a halt reason, or None to keep going."""
    state = strategy.observe()

    halt = risk.check(state)
    if halt:
        ledger.record("halt", reason=halt)
        return halt

    intents = strategy.decide(state)
    results = strategy.execute(intents)
    ledger.record("cycle", intents=len(intents), results=results)
    return None


def main() -> int:
    logging.basicConfig(level=logging.INFO)
    config, strategy, risk, ledger = build()

    mode = "LIVE" if config.live_enabled else "paper"
    if not config.dry_run and not config.live_enabled:
        logger.warning(
            "config says dry_run=false but %s is not set — staying in paper mode",
            LIVE_MODE_ENV,
        )
    logger.info("{idea.name} starting in %s mode against %s", mode, config.venue)
    ledger.record("start", mode=mode, venue=config.venue)

    while True:
        halt = cycle(strategy, risk, ledger)
        if halt:
            logger.error("halted: %s", halt)
            return 1
        time.sleep(config.poll_seconds)


if __name__ == "__main__":
    raise SystemExit(main())
'''


def _test_risk_py(spec: BotSpec, pkg: str) -> str:
    return f'''"""The stops must fail closed.

An unimplemented guard has to halt the bot. If this test ever starts
failing because a guard returns None by default, the bot has become one
that trades with no stops at all.
"""

from {pkg}.risk import RiskLimits, RiskManager


def _manager(max_capital=1000.0):
    return RiskManager(RiskLimits(max_capital_usd=max_capital, max_position_fraction=0.4))


def test_unimplemented_guards_halt():
    halt = _manager().check({{"deployed_usd": 0.0}})
    assert halt is not None


def test_capital_ceiling_halts():
    manager = _manager(max_capital=100.0)
    # Neutralise the strategy guards so the ceiling is what is under test.
    for name in dir(manager):
        if name.startswith("_") or name in {{"check", "capital_ceiling", "limits", "halted_reason"}}:
            continue
        attr = getattr(manager, name)
        if callable(attr):
            setattr(manager, name, lambda state: None)
    assert manager.check({{"deployed_usd": 500.0}}) is not None
'''


def _config_example(spec: BotSpec) -> str:
    return f'''# Copy to config.toml and edit. Paper mode is the default.
#
# Live trading also requires {LIVE_MODE_ENV}=1 in the environment.
# Two switches, on purpose.

[bot]
venue = "{spec.venue}"
dry_run = true
poll_seconds = 5.0

[risk]
# Seeded from the strategy's own stated capital floor.
max_capital_usd = {spec.capital_floor_usd:.1f}
max_position_fraction = 0.4
halt_on_unexplained_state = true
'''


def _pyproject(idea: Idea, pkg: str, repo_name: str) -> str:
    return f'''[project]
name = "{repo_name}"
version = "0.1.0"
description = "{idea.tagline}"
requires-python = ">=3.12"
dependencies = []

[project.optional-dependencies]
dev = ["pytest>=8.0", "ruff>=0.5"]

[build-system]
requires = ["setuptools>=68"]
build-backend = "setuptools.build_meta"

[tool.setuptools.packages.find]
where = ["src"]

[tool.ruff]
line-length = 110

[tool.pytest.ini_options]
pythonpath = ["src"]
testpaths = ["tests"]
'''


def render_bot_scaffold(idea: Idea, output_dir: Path) -> Path:
    """Render a runnable bot skeleton for *idea* into *output_dir*.

    Returns the project root. Raises ValueError when the idea carries no
    BotSpec — there is nothing to scaffold from, and inventing a venue would
    be the worst possible default."""
    spec = idea.bot_spec
    if spec is None:
        raise ValueError("cannot scaffold a bot without a BotSpec")

    repo_name = sanitize_repo_name(idea.name)
    pkg = _pkg_name(repo_name)
    root = Path(output_dir) / repo_name
    pkg_dir = root / "src" / pkg
    pkg_dir.mkdir(parents=True, exist_ok=True)
    (root / "tests").mkdir(parents=True, exist_ok=True)

    (root / "README.md").write_text(_readme(idea, spec, pkg))
    (root / "VALIDATION.md").write_text(_validation_doc(idea, spec))
    (root / "pyproject.toml").write_text(_pyproject(idea, pkg, repo_name))
    (root / "config.example.toml").write_text(_config_example(spec))
    (root / ".gitignore").write_text(
        "\n".join(
            [
                "__pycache__/",
                "*.pyc",
                ".venv/",
                "config.toml",  # holds venue settings — never commit
                "ledger.jsonl",
                ".env",
            ]
        )
        + "\n"
    )

    (pkg_dir / "__init__.py").write_text(f'"""{idea.name}."""\n\n__all__ = []\n')
    (pkg_dir / "config.py").write_text(_config_py(idea, spec))
    (pkg_dir / "venue.py").write_text(_venue_py(idea, spec))
    (pkg_dir / "strategy.py").write_text(_strategy_py(idea, spec))
    (pkg_dir / "risk.py").write_text(_risk_py(idea, spec))
    (pkg_dir / "ledger.py").write_text(_ledger_py(idea))
    (pkg_dir / "runner.py").write_text(_runner_py(idea, spec, pkg))
    (root / "tests" / "test_risk.py").write_text(_test_risk_py(spec, pkg))

    return root
