# Contributing

Thanks for considering a contribution. Project Forge is a personal project; PRs are welcome but I can't promise a review SLA, and large changes may not get merged if they don't fit the project's direction. Open an issue first if you're not sure.

## Before opening a PR

1. **Open an issue** describing what you want to change. This is more important than a perfect patch — half the value of an issue is the discussion.
2. **Check the existing tests pass** locally:
   ```
   pytest tests/ -v
   ruff check src/ tests/
   ```
3. **Add a test for any new behavior or bug fix.** New code without tests usually doesn't get merged. The existing tests are the documentation for how things actually work.

## Style

- Python 3.12+. Type hints on all public API functions and methods.
- `ruff` is the linter and formatter. Run `ruff check src/ tests/` before pushing.
- `pathlib.Path` over `os.path`.
- `dataclasses` or `pydantic` for structured data, not raw dicts at API boundaries.
- Functions over 50 lines should be broken up.
- No comments that explain WHAT the code does — name things well instead. Comments are for WHY: a non-obvious constraint, a workaround, a hidden invariant.
- No marketing language in code, comments, READMEs, or scaffold templates.

## Commits

- Conventional commit prefixes: `feat:`, `fix:`, `refactor:`, `test:`, `docs:`, `ci:`, `security:`, `perf:`, `chore:`
- Subject under 70 chars. Body explains the WHY, not the WHAT (the diff shows WHAT).
- One logical change per commit. If you find yourself writing "and also" in the message, split it.

## Tests

- Unit tests for pure logic (`engine/*`, `models.py`, scoring math)
- Integration tests for the database layer (`storage/db.py`)
- ASGITransport tests for HTTP routes — see `tests/test_dashboard_rendering.py` for the pattern
- Mock external calls (Anthropic SDK, GitHub CLI, network fetchers) — never hit the network in tests
- Adversarial tests for any security-sensitive code (input validation, SSRF, CSRF, auth)

## Areas where help is most useful

- **Test coverage** for older modules (the `tests/test_*.py` index is the source of truth — run `pytest --collect-only -q | tail -3` for the count)
- **Decompositions** of large files (`storage/db.py`, `web/routes.py`) — see the SI proposals on the `/thinktank` page for current candidates
- **Documentation** of edge cases that bit you while running it
- **External feed parsers** (`feeds/`) — adding GitHub Trending, HN, or any new RSS source

## Areas where I'm unlikely to merge a PR

- New top-level features that compete with what's already there (a second wizard, an alternate scoring engine, etc.)
- Theming/cosmetic changes to the dashboard
- Adding a JavaScript framework
- Persona/marketing language in user-visible strings

## Self-improvement proposals

The project has a self-improvement loop that proposes its own patches via `/thinktank`. If you see a "Decompose X" or "Add tests for X" proposal there, feel free to claim it via an issue and submit a PR — those are explicitly the easy on-ramp tasks.

## License

By contributing, you agree your contribution is licensed under the project's MIT license.
