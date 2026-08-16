"""No cadence may call the LLM backend inline on the event loop.

Observed twice in production, both times as "the whole dashboard is dead":
every route hung — not a 500, no response at all — for minutes, while a
cadence sat inside `subprocess.run(["claude", "--print", ...])`. The CLI
backend blocks for tens of seconds per call and a review panel makes six,
so one fire can freeze the app for the better part of ten minutes.

The rule is simple and mechanically checkable: inside an `async def`, a
backend call must go through `asyncio.to_thread`. Passing `backend.call`
as an argument to `to_thread` is fine (it is a reference, not a call);
invoking `backend.call(...)` directly is not.

Synchronous helpers are exempt — they are not on the loop, and the caller
that awaits them is what this test polices.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

SRC = Path(__file__).resolve().parent.parent / "src" / "project_forge"

# These call the backend from a sync function that a cadence never awaits
# directly, or are entry points run as one-shot scripts off the loop.
EXEMPT_FILES: frozenset[str] = frozenset(
    {
        "engine/generator.py",  # sync template generator
        "engine/text_ingest.py",  # sync, request-scoped
        "engine/idea_builder.py",  # sync helper
        "engine/launchpad.py",  # sync, request-scoped
        "engine/recruiter.py",  # sync, request-scoped
        "cron/self_improve_runner.py",  # one-shot CLI runner, own process
    }
)


def _resolves_backend(node: ast.AST) -> bool:
    """True when an expression produces a backend by calling a resolver.

    Covers the `b = backend if backend is not None else resolve_cheap_backend()`
    shape used throughout the engine, hence the walk rather than a direct
    isinstance check on the value itself.
    """
    for sub in ast.walk(node):
        if not isinstance(sub, ast.Call):
            continue
        func = sub.func
        name = func.id if isinstance(func, ast.Name) else getattr(func, "attr", "")
        if "resolve" in name and "backend" in name:
            return True
    return False


def _backend_aliases(fn: ast.AST) -> set[str]:
    """Local names bound to a resolved backend inside this function.

    Naming the variable `backend` is a convention, not a guarantee: two live
    blockers hid behind `b` (foundry.build_scaffold_plan) and `resolved`
    (premortem.generate_premortem) for exactly as long as this detector
    keyed off the identifier alone.
    """
    aliases: set[str] = set()
    for node in ast.walk(fn):
        if isinstance(node, ast.Assign) and _resolves_backend(node.value):
            aliases.update(t.id for t in node.targets if isinstance(t, ast.Name))
        elif (
            isinstance(node, ast.AnnAssign)
            and node.value is not None
            and _resolves_backend(node.value)
            and isinstance(node.target, ast.Name)
        ):
            aliases.add(node.target.id)
    return aliases


def _backendish(node: ast.AST, aliases: frozenset[str] = frozenset()) -> bool:
    """True when this expression looks like a resolved LLM backend."""
    if isinstance(node, ast.Name):
        return "backend" in node.id.lower() or node.id in aliases
    if isinstance(node, ast.Attribute):
        return "backend" in node.attr.lower()
    return False


# Feed fetchers are blocking urllib. A rate-limited GitHub turns each one
# into a full 15s timeout, and a probe makes dozens — the web app stops
# answering for minutes while they drain.
BLOCKING_FETCHERS = frozenset(
    {
        "fetch_pki_gaps",
        "fetch_venue_programs",
        "fetch_pulse_signals",
        "fetch_incumbent_intel",
        "http_get_bytes",
    }
)


def _direct_body(fn: ast.AST):
    """Walk an async function's own body, pruning nested function bodies.

    A sync callback DEFINED inside an async function does not run on the
    loop — whoever invokes it decides that. `_fire_scoreboard` builds a
    `_gh_stars` closure that blocks, and hands it to a consumer that awaits
    it off-thread; flagging the definition site would force the blocking
    call to move somewhere it does not belong.
    """
    for node in ast.iter_child_nodes(fn):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda)):
            continue
        yield node
        for sub in ast.walk(node):
            if isinstance(sub, (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda)):
                continue
            if sub is not node:
                yield sub


def _inline_fetch_calls(tree: ast.AST) -> list[tuple[str, int]]:
    """(function name, lineno) for each blocking feed fetch called directly
    inside an `async def`."""
    found: list[tuple[str, int]] = []
    for fn in ast.walk(tree):
        if not isinstance(fn, ast.AsyncFunctionDef):
            continue
        for node in _direct_body(fn):
            if not isinstance(node, ast.Call):
                continue
            name = None
            if isinstance(node.func, ast.Name):
                name = node.func.id
            elif isinstance(node.func, ast.Attribute):
                name = node.func.attr
            if name in BLOCKING_FETCHERS:
                found.append((fn.name, node.lineno))
    return found


def _inline_backend_calls(tree: ast.AST) -> list[tuple[str, int]]:
    """(function name, lineno) for each direct backend.call() inside an
    `async def`."""
    found: list[tuple[str, int]] = []
    for fn in ast.walk(tree):
        if not isinstance(fn, ast.AsyncFunctionDef):
            continue
        aliases = frozenset(_backend_aliases(fn))
        for node in ast.walk(fn):
            if not isinstance(node, ast.Call):
                continue
            func = node.func
            if not isinstance(func, ast.Attribute) or func.attr != "call":
                continue
            if _backendish(func.value, aliases):
                found.append((fn.name, node.lineno))
    return found


_SUBPROCESS_CALLS = frozenset({"run", "check_output", "call", "check_call", "Popen"})


def _inline_subprocess_calls(tree: ast.AST) -> list[tuple[str, int]]:
    """(function name, lineno) for each `subprocess.X(...)` invoked directly
    inside an `async def`.

    The sync-helper rule below only catches blocking work one frame down. Two
    `gh repo view` shell-outs sat directly in async route handlers instead —
    fired once per repo-linked idea on page load, each up to a 10s stall on
    the loop — and no existing check looked for that shape.
    """
    found: list[tuple[str, int]] = []
    for fn in ast.walk(tree):
        if not isinstance(fn, ast.AsyncFunctionDef):
            continue
        for node in _direct_body(fn):
            if not isinstance(node, ast.Call):
                continue
            func = node.func
            if (
                isinstance(func, ast.Attribute)
                and isinstance(func.value, ast.Name)
                and func.value.id == "subprocess"
                and func.attr in _SUBPROCESS_CALLS
            ):
                found.append((fn.name, node.lineno))
    return found


def _modules() -> list[Path]:
    return sorted(p for p in SRC.rglob("*.py") if "__pycache__" not in str(p))


@pytest.mark.parametrize("path", _modules(), ids=lambda p: str(p.name))
def test_no_inline_subprocess_in_async_functions(path: Path):
    """`await asyncio.to_thread(subprocess.run, ...)` is fine — the reference
    is an argument, not a call. `subprocess.run(...)` on the loop is not."""
    rel = str(path.relative_to(SRC))
    if rel in EXEMPT_FILES:
        pytest.skip(f"{rel} is sync by design")

    tree = ast.parse(path.read_text())
    offenders = _inline_subprocess_calls(tree)
    assert not offenders, (
        f"{rel} shells out directly inside an async function {offenders} — "
        f"wrap it in `await asyncio.to_thread(subprocess.run, ...)` or every "
        f"request the app is serving stalls for the duration."
    )


@pytest.mark.parametrize("path", _modules(), ids=lambda p: str(p.name))
def test_no_inline_backend_calls_in_async_functions(path: Path):
    rel = str(path.relative_to(SRC))
    if rel in EXEMPT_FILES:
        pytest.skip(f"{rel} calls the backend from sync code")

    tree = ast.parse(path.read_text())
    offenders = _inline_backend_calls(tree)
    assert not offenders, (
        f"{rel} calls the LLM backend inline inside an async function "
        f"{offenders} — wrap it in `await asyncio.to_thread(backend.call, prompt)` "
        f"or the whole web app stops answering while it runs."
    )


@pytest.mark.parametrize("path", _modules(), ids=lambda p: str(p.name))
def test_no_inline_feed_fetches_in_async_functions(path: Path):
    """Same rule, for the network. The dashboard died on this too: the PKI
    probe's `fetch_pki_gaps()` makes ten HTTP calls inline, and once GitHub
    started rate-limiting, each one burned its full timeout on the loop."""
    rel = str(path.relative_to(SRC))
    if rel in EXEMPT_FILES or rel.startswith("feeds/"):
        pytest.skip(f"{rel} is the fetcher itself, or sync by design")

    tree = ast.parse(path.read_text())
    offenders = _inline_fetch_calls(tree)
    assert not offenders, (
        f"{rel} makes a blocking feed fetch inside an async function {offenders} — "
        f"wrap it in `await asyncio.to_thread(...)`."
    )


def _sync_blocking_functions() -> set[str]:
    """Names of SYNC functions anywhere in the package that block: they call
    the LLM backend, or they shell out to a subprocess.

    These are the second-order blockers. `semantic_dedup_check` is sync and
    shells out to `claude --print`; called inline from an async function it
    froze every request the web app was serving, once per borderline pair,
    on every save — and the first version of this test could not see it,
    because the blocking call sits one frame down.
    """
    # First pass: sync functions that call the backend directly.
    names: set[str] = set()
    # name -> the sync function names it calls, for the transitive pass.
    calls: dict[str, set[str]] = {}

    for path in _modules():
        tree = ast.parse(path.read_text())
        for fn in ast.walk(tree):
            if not isinstance(fn, ast.FunctionDef):  # sync only
                continue
            called: set[str] = set()
            aliases = frozenset(_backend_aliases(fn))
            for node in ast.walk(fn):
                if not isinstance(node, ast.Call):
                    continue
                if (
                    isinstance(node.func, ast.Attribute)
                    and node.func.attr == "call"
                    and _backendish(node.func.value, aliases)
                ):
                    names.add(fn.name)
                # Shelling out blocks exactly like an LLM call does. The `gh`
                # CLI in the hourly issue-sync froze the whole dashboard for
                # minutes — one subprocess per promoted idea, on the loop.
                if (
                    isinstance(node.func, ast.Attribute)
                    and isinstance(node.func.value, ast.Name)
                    and node.func.value.id == "subprocess"
                    and node.func.attr in {"run", "check_output", "call", "check_call", "Popen"}
                ):
                    names.add(fn.name)
                if isinstance(node.func, ast.Name):
                    called.add(node.func.id)
            calls[fn.name] = called

    # Transitive closure: a sync wrapper around a blocker blocks too.
    # `estimate_build` never touches the backend itself — it calls
    # `_llm_estimate`, which does — and /api/recruiter awaited neither.
    changed = True
    while changed:
        changed = False
        for name, called in calls.items():
            if name not in names and (called & names):
                names.add(name)
                changed = True
    return names


# `_reasoning_llm_call` builds and RETURNS a callable; invoking the factory
# does not touch the network. What it returns does block, which is why its
# consumer (synthesize_super_idea) is threaded instead.
_FACTORY_FALSE_POSITIVES = frozenset({"_reasoning_llm_call"})

SYNC_BLOCKERS = _sync_blocking_functions() - _FACTORY_FALSE_POSITIVES

# Blocking calls that predate this test, on paths that are human-initiated
# (a click, a promote) or disarmed by default (Mechanic), rather than
# cadences and page loads that fire on their own. They still block and they
# should still be fixed.
#
# This is a RATCHET, not an exemption: the assertion below allows exactly
# these and fails on anything new, so the list can only shrink. Every entry
# is a `gh` CLI shell-out inherited from when this engine ran as one-shot
# cron scripts, where blocking the process cost nothing.
KNOWN_BLOCKING_DEBT: dict[str, set[str]] = {
    "cron/auto_promote_runner.py": {"_create_promotion_issue"},
    "cron/introspect_runner.py": {"gather_self_context"},
    "cron/scheduler.py": {
        "_create_enhancement_issue",
        "create_label",
        "create_issue",
        "scaffold_project",
    },
    "engine/audit.py": {"audit_promoted_idea", "close_github_issue"},
    "engine/mechanic.py": {
        "_create_workspace",
        "run_agent",
        "_changed_paths",
        "_quality_gate",
        "_open_pr",
        "list_open_prs",
    },
    "web/lifespan_scheduler.py": {"spawn_mechanic_run"},
    "web/routes.py": {
        "create_repo",
        "push_initial_commit",
        "create_issue",
        "create_label",
        "_create_promotion_issue",
        "_promote_to_ci_queue",
        "spawn_mechanic_run",
        "merge_pr",
        "close_pr",
    },
}


@pytest.mark.parametrize("path", _modules(), ids=lambda p: str(p.name))
def test_no_async_function_calls_a_sync_blocker_inline(path: Path):
    """An async function may reference a blocking sync helper (passing it to
    to_thread), but must never invoke it directly."""
    rel = str(path.relative_to(SRC))
    if rel in EXEMPT_FILES:
        pytest.skip(f"{rel} is sync by design")

    tree = ast.parse(path.read_text())
    offenders: list[tuple[str, str, int]] = []
    for fn in ast.walk(tree):
        if not isinstance(fn, ast.AsyncFunctionDef):
            continue
        for node in ast.walk(fn):
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Name):
                if node.func.id in SYNC_BLOCKERS:
                    offenders.append((fn.name, node.func.id, node.lineno))

    allowed = KNOWN_BLOCKING_DEBT.get(rel, set())
    unexpected = [o for o in offenders if o[1] not in allowed]

    assert not unexpected, (
        f"{rel} calls a blocking sync helper inline from async code {unexpected} — "
        f"these shell out to the LLM backend or a subprocess and freeze every "
        f"request the app is serving; wrap in `await asyncio.to_thread(fn, ...)`."
    )
