# Python Standards

- Target: Python 3.12+
- Formatter: ruff format
- Linter: ruff check (E, F, W, I, S, B, UP rules)
- Testing: pytest with pytest-asyncio
- Type hints required on public functions
- Pydantic BaseModel for data classes
- pydantic-settings for configuration
- async/await for all I/O operations
- pathlib.Path for file system operations
