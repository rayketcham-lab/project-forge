# Project Standards

## TDD Discipline
- Write tests FIRST, then implement
- Every GitHub issue labeled `feature` must have at least one corresponding test
- Run `pytest tests/ -v` before every commit
- Run `ruff check src/ tests/` before every commit

## Issue-First Workflow
- Create a GitHub issue before implementing any feature
- Reference the issue number in commit messages
- Every issue gets at least one test (1:1 ratio target)

## Code Style
- Python 3.12+, use modern syntax (StrEnum, UTC, match/case where appropriate)
- Pydantic for all data models
- FastAPI for web endpoints
- aiosqlite for database operations
- All paths via pathlib.Path

## Git
- Meaningful commit messages focusing on "why"
- Co-Authored-By: Claude <noreply@anthropic.com> on all commits
- Never force push to main
