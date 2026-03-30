"""Shared test fixtures."""

import asyncio
from pathlib import Path

import pytest
import pytest_asyncio

from project_forge.storage.db import Database


@pytest.fixture(scope="session")
def event_loop():
    loop = asyncio.new_event_loop()
    yield loop
    loop.close()


@pytest_asyncio.fixture
async def db(tmp_path: Path):
    database = Database(tmp_path / "test.db")
    await database.connect()
    yield database
    await database.close()
