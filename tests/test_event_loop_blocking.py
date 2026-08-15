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


def _backendish(node: ast.AST) -> bool:
    """True when this expression looks like a resolved LLM backend."""
    if isinstance(node, ast.Name):
        return "backend" in node.id.lower()
    if isinstance(node, ast.Attribute):
        return "backend" in node.attr.lower()
    return False


def _inline_backend_calls(tree: ast.AST) -> list[tuple[str, int]]:
    """(function name, lineno) for each direct backend.call() inside an
    `async def`."""
    found: list[tuple[str, int]] = []
    for fn in ast.walk(tree):
        if not isinstance(fn, ast.AsyncFunctionDef):
            continue
        for node in ast.walk(fn):
            if not isinstance(node, ast.Call):
                continue
            func = node.func
            if not isinstance(func, ast.Attribute) or func.attr != "call":
                continue
            if _backendish(func.value):
                found.append((fn.name, node.lineno))
    return found


def _modules() -> list[Path]:
    return sorted(p for p in SRC.rglob("*.py") if "__pycache__" not in str(p))


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
