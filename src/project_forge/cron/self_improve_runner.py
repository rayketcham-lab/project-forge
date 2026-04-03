"""Autonomous self-improvement runner for Project Forge.

Fetches open ci-queue issues, asks Claude to implement the fix,
applies changes, validates with tests+lint, creates a PR, and closes the issue.
All operations target project-forge itself.
"""

import json
import logging
import subprocess
from pathlib import Path

import anthropic

from project_forge.config import settings
from project_forge.engine.introspect import gather_self_context

logger = logging.getLogger(__name__)

_PROJECT_ROOT = Path(__file__).parent.parent.parent.parent


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _run_gh(args: list[str]) -> str:
    """Run a gh CLI command and return stdout."""
    cmd = ["gh"] + args
    result = subprocess.run(cmd, capture_output=True, text=True, timeout=60)
    if result.returncode != 0:
        raise RuntimeError(f"gh failed: {result.stderr}")
    return result.stdout.strip()


def _run_cmd(cmd: list[str], cwd: str | None = None) -> tuple[int, str]:
    """Run a shell command and return (returncode, combined output)."""
    result = subprocess.run(
        cmd, capture_output=True, text=True, cwd=cwd, timeout=120
    )
    output = (result.stdout + "\n" + result.stderr).strip()
    return result.returncode, output


def _call_claude(prompt: str) -> str:
    """Send a prompt to Claude and return the raw text response.

    Raises ValueError if the response is empty or truncated.
    """
    key = settings.anthropic_api_key
    if not key:
        import os

        key = os.environ.get("ANTHROPIC_API_KEY", "")
    client = anthropic.Anthropic(api_key=key)
    response = client.messages.create(
        model=settings.anthropic_model,
        max_tokens=8192,
        system=(
            "You are a senior Python developer improving the Project Forge codebase. "
            "Respond ONLY with valid JSON in the specified format. No markdown wrapping."
        ),
        messages=[{"role": "user", "content": prompt}],
    )
    if not response.content:
        raise ValueError("Claude returned empty response")
    if response.stop_reason == "max_tokens":
        raise ValueError("Claude response truncated (max_tokens reached)")
    return response.content[0].text


def _revert_changes(changed_files: list[str] | None = None) -> None:
    """Revert specific files in the working tree. Reverts nothing if list is empty."""
    if not changed_files:
        return
    subprocess.run(
        ["git", "checkout", "--"] + changed_files,
        capture_output=True,
        text=True,
        cwd=str(_PROJECT_ROOT),
        timeout=30,
    )


# ---------------------------------------------------------------------------
# 1. Fetch ci-queue issues
# ---------------------------------------------------------------------------


def fetch_ci_queue_issues() -> list[dict]:
    """Fetch open GitHub issues with the ci-queue label."""
    try:
        output = _run_gh([
            "issue", "list",
            "--state", "open",
            "--label", "ci-queue",
            "--json", "number,title,body,url,labels,state",
        ])
        return json.loads(output) if output else []
    except (RuntimeError, json.JSONDecodeError) as exc:
        logger.warning("Could not fetch ci-queue issues: %s", exc)
        return []


# ---------------------------------------------------------------------------
# 2. Build implementation prompt
# ---------------------------------------------------------------------------

_IMPLEMENTATION_PROMPT = """\
You are implementing a self-improvement for the Project Forge codebase.

## Issue to Address
**#{number}: {title}**

{body}

## Codebase Context
{context_section}

## Your Task

Implement the improvement described in the issue above. Make targeted, minimal changes.

Respond with ONLY valid JSON (no markdown wrapping) in this exact format:
{{
    "summary": "One-line description of what you changed",
    "changes": [
        {{
            "path": "relative/path/to/file.py",
            "action": "edit",
            "search": "exact string to find in the file",
            "replace": "replacement string"
        }},
        {{
            "path": "relative/path/to/new_file.py",
            "action": "create",
            "content": "full file content"
        }}
    ]
}}

Rules:
- action is "edit" (modify existing file) or "create" (new file)
- For edits, "search" must be an exact substring of the current file content
- Keep changes minimal and focused on the issue
- Include test changes if appropriate
- All paths relative to project root
"""


def build_implementation_prompt(issue: dict, context: dict) -> str:
    """Build a prompt for Claude to implement the issue fix."""
    context_lines = []
    for key, val in context.items():
        if isinstance(val, dict):
            for k, v in val.items():
                context_lines.append(f"- {k}: {v}")
        else:
            context_lines.append(f"- {key}: {val}")

    return _IMPLEMENTATION_PROMPT.format(
        number=issue.get("number", "?"),
        title=issue.get("title", ""),
        body=issue.get("body", ""),
        context_section="\n".join(context_lines) if context_lines else "(none)",
    )


# ---------------------------------------------------------------------------
# 3. Parse Claude's response
# ---------------------------------------------------------------------------


def parse_implementation_response(raw: str) -> dict:
    """Parse Claude's JSON response into a structured change set.

    Returns dict with 'summary' and 'changes' keys.
    Raises ValueError on invalid or missing data.
    """
    text = raw.strip()
    if "```json" in text:
        text = text.split("```json")[1].split("```")[0].strip()
    elif "```" in text:
        text = text.split("```")[1].split("```")[0].strip()

    try:
        data = json.loads(text)
    except json.JSONDecodeError as exc:
        raise ValueError(f"Could not parse Claude response as JSON: {exc}") from exc

    if "changes" not in data:
        raise ValueError("Response missing 'changes' key")

    return {
        "summary": data.get("summary", ""),
        "changes": data["changes"],
    }


# ---------------------------------------------------------------------------
# 4. Apply file changes
# ---------------------------------------------------------------------------


_ALLOWED_PREFIXES = ("src/project_forge/engine/", "src/project_forge/cron/", "tests/")
_BLOCKED_FILES = (
    "src/project_forge/web/auth.py",
    "src/project_forge/config.py",
    "src/project_forge/web/app.py",
    "src/project_forge/storage/db.py",
)
_BLOCKED_PATTERNS = (".github/", ".env", ".bashrc", ".profile", ".ssh/", "scripts/")


def _validate_path(change_path: str, root: Path) -> Path:
    """Resolve a change path and verify it stays within the project root.

    Uses an allowlist for writable paths plus a blocklist for sensitive files.
    """
    resolved = (root / change_path).resolve()
    if not resolved.is_relative_to(root.resolve()):
        raise ValueError(f"Path outside project root: {change_path!r}")
    normalized = change_path.replace("\\", "/")
    # Block sensitive patterns
    for pattern in _BLOCKED_PATTERNS:
        if normalized.startswith(pattern) or normalized == pattern.rstrip("/"):
            raise ValueError(f"Path restricted (blocked pattern {pattern!r}): {change_path!r}")
    # Block security-critical files
    if normalized in _BLOCKED_FILES:
        raise ValueError(f"Path restricted (security-critical): {change_path!r}")
    # Allowlist: only permit writes to approved prefixes
    if not any(normalized.startswith(prefix) for prefix in _ALLOWED_PREFIXES):
        raise ValueError(
            f"Path not in allowed prefixes {_ALLOWED_PREFIXES}: {change_path!r}"
        )
    return resolved


def apply_changes(changes: list[dict], project_root: Path | None = None) -> list[str]:
    """Apply file changes to disk. Returns list of changed file paths.

    Raises ValueError if path escapes project root, search string not found,
    or action is unknown.
    """
    root = project_root or _PROJECT_ROOT
    changed_files = []

    for change in changes:
        path = _validate_path(change["path"], root)
        action = change["action"]

        if action == "create":
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(change["content"])
            changed_files.append(change["path"])

        elif action == "edit":
            if not path.exists():
                raise ValueError(f"File not found for edit: {change['path']}")
            content = path.read_text()
            search = change["search"]
            if search not in content:
                raise ValueError(
                    f"Search string not found in {change['path']}: {search[:80]!r}"
                )
            content = content.replace(search, change["replace"], 1)
            path.write_text(content)
            changed_files.append(change["path"])

        else:
            raise ValueError(f"Unknown action: {action!r}")

    return changed_files


# ---------------------------------------------------------------------------
# 5. Validate changes
# ---------------------------------------------------------------------------


def validate_changes(project_root: str | None = None) -> dict:
    """Run pytest + ruff and return validation result."""
    root = project_root or str(_PROJECT_ROOT)

    # Run tests
    test_rc, test_out = _run_cmd(
        ["python3", "-m", "pytest", "tests/", "-v", "--tb=short"],
        cwd=root,
    )

    # Run lint
    lint_rc, lint_out = _run_cmd(
        ["python3", "-m", "ruff", "check", "src/", "tests/"],
        cwd=root,
    )

    passed = test_rc == 0 and lint_rc == 0
    details = []
    if test_rc != 0:
        details.append(f"Tests failed:\n{test_out}")
    if lint_rc != 0:
        details.append(f"Lint failed:\n{lint_out}")

    return {
        "passed": passed,
        "detail": "\n".join(details),
    }


# ---------------------------------------------------------------------------
# 6. Create branch + PR
# ---------------------------------------------------------------------------


def create_improvement_pr(
    issue_number: int,
    summary: str,
    changed_files: list[str],
) -> str:
    """Create a git branch, commit changes, and open a PR. Returns PR URL."""
    branch = f"self-improve-{issue_number}"
    root = str(_PROJECT_ROOT)

    # Create and switch to branch
    _run_cmd(["git", "checkout", "-b", branch], cwd=root)

    # Stage changed files
    for f in changed_files:
        _run_cmd(["git", "add", f], cwd=root)

    # Commit
    msg = (
        f"fix: self-improvement #{issue_number} — {summary}\n\n"
        f"Co-Authored-By: Claude <noreply@anthropic.com>"
    )
    _run_cmd(["git", "commit", "-m", msg], cwd=root)

    # Push and create PR
    _run_cmd(["git", "push", "-u", "origin", branch], cwd=root)
    url = _run_gh([
        "pr", "create",
        "--title", f"fix: self-improvement #{issue_number} — {summary}",
        "--body", f"Closes #{issue_number}\n\nAutonomously generated by Project Forge self-improvement runner.",
        "--head", branch,
    ])

    # Switch back to main
    _run_cmd(["git", "checkout", "main"], cwd=root)

    return url


# ---------------------------------------------------------------------------
# 7. Close issue
# ---------------------------------------------------------------------------


def close_issue(issue_number: int) -> None:
    """Close a GitHub issue by number."""
    _run_gh(["issue", "close", str(issue_number)])


# ---------------------------------------------------------------------------
# 8. Full orchestration
# ---------------------------------------------------------------------------


async def run_self_improve_cycle() -> dict:
    """Run one autonomous self-improvement cycle.

    Returns dict with 'processed' count and 'results' list.
    """
    issues = fetch_ci_queue_issues()
    if not issues:
        logger.info("No ci-queue issues found. Nothing to do.")
        return {"processed": 0, "results": []}

    # Check for API key — SI requires Claude to generate code
    import os

    key = settings.anthropic_api_key or os.environ.get("ANTHROPIC_API_KEY", "")
    if not key:
        logger.warning(
            "No API key configured — skipping %d ci-queue issues. "
            "Self-improvement requires a Claude API key to generate code changes.",
            len(issues),
        )
        return {
            "processed": len(issues),
            "results": [
                {"issue": i.get("number", 0), "status": "skipped",
                 "detail": "No API key — self-improvement requires Claude."}
                for i in issues
            ],
        }

    context = gather_self_context()
    results = []

    for issue in issues:
        issue_num = issue.get("number", 0)
        issue_title = issue.get("title", "unknown")
        logger.info("Processing ci-queue issue #%d: %s", issue_num, issue_title)

        changed_files: list[str] = []
        try:
            # Ask Claude to implement
            prompt = build_implementation_prompt(issue, context)
            raw_response = _call_claude(prompt)
            parsed = parse_implementation_response(raw_response)

            # Apply changes
            changed_files = apply_changes(parsed["changes"])

            # Validate
            validation = validate_changes()
            if not validation["passed"]:
                logger.warning(
                    "Validation failed for #%d: %s", issue_num, validation["detail"]
                )
                _revert_changes(changed_files)
                results.append({
                    "issue": issue_num,
                    "status": "validation_failed",
                    "detail": validation["detail"],
                })
                continue

            # Create PR
            pr_url = create_improvement_pr(
                issue_number=issue_num,
                summary=parsed["summary"],
                changed_files=changed_files,
            )

            # Close the issue
            close_issue(issue_num)

            results.append({
                "issue": issue_num,
                "status": "success",
                "pr_url": pr_url,
            })
            logger.info("Successfully processed #%d → %s", issue_num, pr_url)

        except Exception as exc:
            logger.error("Failed to process #%d: %s", issue_num, exc)
            _revert_changes(changed_files)
            results.append({
                "issue": issue_num,
                "status": "error",
                "detail": str(exc),
            })

    return {"processed": len(issues), "results": results}


# ---------------------------------------------------------------------------
# CLI entry point
# ---------------------------------------------------------------------------


async def _run() -> None:
    result = await run_self_improve_cycle()
    logger.info(
        "Self-improve cycle complete: %d processed, %d succeeded",
        result["processed"],
        sum(1 for r in result["results"] if r["status"] == "success"),
    )


def main() -> None:
    """Entry point for forge-self-improve console script."""
    import asyncio

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )
    asyncio.run(_run())


if __name__ == "__main__":
    main()
