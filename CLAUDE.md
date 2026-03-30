# Project Forge

Autonomous IT project think-tank engine. Generates novel project ideas, scores feasibility, scaffolds real GitHub repos.

## Stack
- Python 3.12, FastAPI, SQLite (aiosqlite), Anthropic SDK
- Plain HTML + vanilla JS for web dashboard
- GitHub Actions CI on self-hosted runner

## Rules
- **TDD**: Write tests FIRST, then implement. No code without a corresponding test.
- **Issue-first**: Create a GitHub issue before implementing any feature. Every issue needs at least one test.
- **Pydantic everywhere**: All data flows through Pydantic models. No raw dicts at API boundaries.
- **No secrets in code**: API keys come from environment variables only.
- **Ruff for lint**: `ruff check src/ tests/` must pass before commit.

## Project Structure
- `src/project_forge/` - Main package
  - `models.py` - Pydantic data models (Idea, ScaffoldSpec, GenerationRun)
  - `config.py` - Settings via pydantic-settings
  - `engine/` - Idea generation, scoring, prompts
  - `web/` - FastAPI dashboard on port 55443
  - `scaffold/` - Project scaffolding and GitHub integration
  - `cron/` - Autonomous generation runner
  - `storage/` - SQLite database layer
- `tests/` - pytest test suite
- `scripts/` - Shell scripts for serve, generate, setup

## Commands
- `pytest tests/ -v` - Run tests
- `ruff check src/ tests/` - Lint
- `python -m uvicorn project_forge.web.app:app --host 0.0.0.0 --port 55443` - Start dashboard
- `python -m project_forge.cron.runner` - Generate one idea

## Environment Variables
- `FORGE_ANTHROPIC_API_KEY` or `ANTHROPIC_API_KEY` - Claude API key
- `FORGE_DB_PATH` - SQLite database path (default: data/forge.db)
- `FORGE_PORT` - Web server port (default: 55443)
