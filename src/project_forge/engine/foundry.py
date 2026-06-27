"""The Foundry — turn a top idea into a concrete, ready-to-create starter repo.

build_scaffold_plan() generates a full repo scaffold plan: repo name,
description, language, file tree, starter issues, README, and first steps.

Two-stage, matching the snipe.py / ambition.py pattern:
  1. Heuristic (always runs, ~free): derives sensible structure from the
     idea's tech_stack, mvp_scope, and description.
  2. LLM (optional, injectable): asks the backend for a richer plan;
     falls back gracefully to the heuristic when no backend resolves.
"""

from __future__ import annotations

import json
import logging
import re
from typing import Any

from project_forge.engine.llm_backend import resolve_cheap_backend
from project_forge.models import Idea

logger = logging.getLogger(__name__)


# Language detection: ordered so TypeScript wins over Python when both fire.
_LANG_PATTERNS: list[tuple[str, re.Pattern]] = [
    ("typescript", re.compile(r"\b(typescript|tsx?|next\.?js|react|vue|angular|node|deno|bun)\b", re.I)),
    ("rust", re.compile(r"\b(rust|cargo|tokio|actix|axum|warp)\b", re.I)),
    ("go", re.compile(r"\b(golang?|gin|fiber|echo)\b", re.I)),
    ("python", re.compile(r"\b(python|py|fastapi|django|flask|starlette|pydantic|uvicorn)\b", re.I)),
]

_FILE_TREES: dict[str, list[str]] = {
    "typescript": [
        "README.md",
        "package.json",
        "tsconfig.json",
        ".gitignore",
        "src/index.ts",
        "src/lib/core.ts",
        "src/types.ts",
        "tests/core.test.ts",
        ".github/workflows/ci.yml",
    ],
    "python": [
        "README.md",
        "pyproject.toml",
        ".gitignore",
        "src/__init__.py",
        "src/main.py",
        "src/core.py",
        "tests/__init__.py",
        "tests/test_core.py",
        ".github/workflows/ci.yml",
    ],
    "rust": [
        "README.md",
        "Cargo.toml",
        ".gitignore",
        "src/main.rs",
        "src/lib.rs",
        "src/error.rs",
        "tests/integration.rs",
        ".github/workflows/ci.yml",
    ],
    "go": [
        "README.md",
        "go.mod",
        ".gitignore",
        "main.go",
        "internal/core.go",
        "internal/errors.go",
        "cmd/root.go",
        "tests/core_test.go",
        ".github/workflows/ci.yml",
    ],
}

_FIRST_STEPS: dict[str, list[str]] = {
    "typescript": [
        "Run `npm install` to install dependencies",
        "Run `npm test` to verify the starter test suite",
        "Update `src/types.ts` with your domain models",
        "Implement core logic in `src/lib/core.ts`",
        "Open the first GitHub issue and assign it to yourself",
    ],
    "python": [
        "Run `uv sync` (or `pip install -e .`) to install dependencies",
        "Run `pytest tests/` to verify the starter test suite",
        "Update `src/core.py` with your domain models and logic",
        "Add any environment variables to `.env.example`",
        "Open the first GitHub issue and assign it to yourself",
    ],
    "rust": [
        "Run `cargo build` to compile the project",
        "Run `cargo test` to verify the starter tests",
        "Add your domain types to `src/lib.rs`",
        "Define custom errors in `src/error.rs` using `thiserror`",
        "Open the first GitHub issue and assign it to yourself",
    ],
    "go": [
        "Run `go mod tidy` to resolve dependencies",
        "Run `go test ./...` to verify the starter tests",
        "Add your domain types and interfaces in `internal/core.go`",
        "Wire up the CLI root command in `cmd/root.go`",
        "Open the first GitHub issue and assign it to yourself",
    ],
}


def _detect_language(tech_stack: list[str]) -> str:
    """Detect primary language from a list of tech stack tokens.

    Ordered so the first match in _LANG_PATTERNS wins (TypeScript before Python).
    Defaults to 'python' when nothing matches.
    """
    joined = " ".join(tech_stack)
    for lang, pattern in _LANG_PATTERNS:
        if pattern.search(joined):
            return lang
    return "python"


def _repo_slug(name: str) -> str:
    """Convert an idea name to a valid kebab-case GitHub repo slug."""
    slug = name.lower()
    slug = re.sub(r"[^a-z0-9]+", "-", slug)
    slug = slug.strip("-")
    return slug[:80] or "forge-idea"


def _issues_from_mvp(mvp_scope: str) -> list[dict[str, str]]:
    """Derive 3-5 starter issues from the MVP scope text.

    Splits on sentence boundaries and numbered lists. Falls back to generic
    engineering issues so there are always at least three to open on day 1.
    """
    raw = re.split(r"(?<=[.!?])\s+|(?:\n+)|(?:\d+\.\s+)", mvp_scope)
    issues: list[dict[str, str]] = []
    for fragment in raw:
        fragment = fragment.strip().rstrip(".")
        if len(fragment) < 10:
            continue
        issues.append(
            {
                "title": fragment[:120],
                "body": f"Implement: {fragment}\n\nPart of the MVP scope for this project.",
            }
        )
        if len(issues) >= 5:
            break

    defaults = [
        {
            "title": "Set up project structure and CI",
            "body": "Initialize the repository, CI pipeline, and test harness.",
        },
        {
            "title": "Implement core domain logic",
            "body": "Build the primary business logic as described in the idea.",
        },
        {
            "title": "Write integration tests",
            "body": "Cover the happy path and key error scenarios end-to-end.",
        },
    ]
    while len(issues) < 3:
        issues.append(defaults[len(issues)])
    return issues[:5]


def _heuristic_plan(idea: Idea) -> dict[str, Any]:
    """Deterministic scaffold plan derived from the idea — no LLM needed."""
    lang = _detect_language(idea.tech_stack)
    return {
        "repo_name": _repo_slug(idea.name),
        "description": idea.tagline,
        "language": lang,
        "file_tree": _FILE_TREES[lang],
        "starter_issues": _issues_from_mvp(idea.mvp_scope or ""),
        "readme_md": (
            f"# {idea.name}\n\n"
            f"> {idea.tagline}\n\n"
            f"## Overview\n\n{idea.description}\n\n"
            f"## Getting Started\n\n" + "\n".join(f"- {s}" for s in _FIRST_STEPS[lang])
        ),
        "first_steps": _FIRST_STEPS[lang],
    }


def _strip_codefence(raw: str) -> str:
    """Remove ```json ... ``` or ``` ... ``` wrappers if present."""
    raw = raw.strip()
    if "```json" in raw:
        raw = raw.split("```json", 1)[1].split("```", 1)[0].strip()
    elif "```" in raw:
        raw = raw.split("```", 1)[1].split("```", 1)[0].strip()
    return raw


def build_scaffold_plan(idea: Idea, *, backend: Any = None) -> dict[str, Any]:
    """Generate a concrete repo scaffold plan for an idea.

    When a backend is injected or resolvable, the LLM produces a rich plan;
    otherwise falls back to a deterministic heuristic plan.

    Args:
        idea: The Idea to scaffold.
        backend: Optional LLMBackend. If None, resolve_cheap_backend() is called.

    Returns:
        dict with keys: repo_name, description, language, file_tree,
        starter_issues, readme_md, first_steps.
    """
    b = backend if backend is not None else resolve_cheap_backend()

    if b is None:
        logger.debug("foundry: no backend — heuristic plan for %r", idea.name)
        return _heuristic_plan(idea)

    prompt = (
        "You are a senior engineer. Given the idea below, produce a concrete "
        "starter-repo scaffold plan as JSON. Reply with ONLY a JSON object "
        "(no markdown, no preamble) with these exact keys:\n"
        "  repo_name: kebab-case GitHub repo name (max 80 chars)\n"
        "  description: one-sentence repo description (max 160 chars)\n"
        "  language: one of python | typescript | rust | go\n"
        "  file_tree: list of file paths (10-15 entries) for the starter repo\n"
        "  starter_issues: list of {title, body} objects (3-5 items), "
        "each a concrete MVP task\n"
        "  readme_md: a concise README.md in Markdown (200-400 words)\n"
        "  first_steps: list of 4-6 strings the developer should do "
        "immediately after cloning\n\n"
        f"Idea name: {idea.name}\n"
        f"Tagline: {idea.tagline}\n"
        f"Description: {idea.description}\n"
        f"Tech stack: {', '.join(idea.tech_stack)}\n"
        f"MVP scope: {idea.mvp_scope}\n\n"
        "Reply with valid JSON only."
    )

    raw = (b.call(prompt) or "").strip()
    cleaned = _strip_codefence(raw)

    try:
        data: dict[str, Any] = json.loads(cleaned)
        required = {"repo_name", "description", "language", "file_tree", "starter_issues", "readme_md", "first_steps"}
        if not required.issubset(data.keys()):
            raise ValueError(f"missing keys: {required - data.keys()}")
        if data.get("language") not in {"python", "typescript", "rust", "go"}:
            data["language"] = _detect_language(idea.tech_stack)
        return data
    except Exception:
        logger.info("foundry LLM parse failed for %r; using heuristic", idea.name)
        return _heuristic_plan(idea)


def format_plan_markdown(plan: dict[str, Any]) -> str:
    """Render a scaffold plan as a human-readable Markdown string."""
    lines: list[str] = [
        f"# {plan.get('repo_name', 'repo')}",
        "",
        f"> {plan.get('description', '')}",
        "",
        f"**Language:** {plan.get('language', '—')}",
        "",
        "## File Tree",
        "",
    ]
    for path in plan.get("file_tree", []):
        lines.append(f"- `{path}`")

    lines += ["", "## Starter Issues", ""]
    for i, issue in enumerate(plan.get("starter_issues", []), 1):
        title = issue.get("title", "")
        body = issue.get("body", "")
        lines.append(f"**#{i} — {title}**")
        if body:
            lines.append(f"> {body}")
        lines.append("")

    lines += ["## First Steps", ""]
    for step in plan.get("first_steps", []):
        lines.append(f"1. {step}")

    readme = plan.get("readme_md", "")
    if readme:
        lines += ["", "## README Preview", "", readme, ""]
    return "\n".join(lines)
