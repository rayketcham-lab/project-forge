"""Request ID sanitization — client-supplied X-Request-ID must be validated."""

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient

from project_forge.web.app import app, db, sanitize_request_id


@pytest_asyncio.fixture
async def client(tmp_path):
    db.db_path = tmp_path / "test_request_id.db"
    await db.connect()
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac
    await db.close()


SAFE_DEFAULT_CHARS = set("0123456789abcdef")


@pytest.mark.parametrize(
    "value",
    [
        None,
        "",
        "a" * 65,
        "has space",
        "inject\r\nX-Evil: 1",
        "café",
        "semi;colon",
        "\x00nul",
    ],
)
def test_sanitize_rejects_bad_values(value):
    """Missing, over-long, or non-alphanumeric IDs are replaced with a fresh one."""
    result = sanitize_request_id(value)
    assert result != value
    assert len(result) == 16
    assert set(result) <= SAFE_DEFAULT_CHARS


@pytest.mark.parametrize("value", ["abc123", "A-B_c", "x", "9" * 64])
def test_sanitize_accepts_good_values(value):
    """Well-formed client IDs are preserved for correlation."""
    assert sanitize_request_id(value) == value


@pytest.mark.asyncio
async def test_malicious_request_id_not_reflected(client):
    """A header-injection payload must not be echoed back."""
    resp = await client.get("/health", headers={"X-Request-ID": "aaa\r\nX-Evil: 1"})
    assert resp.status_code == 200
    echoed = resp.headers["X-Request-ID"]
    assert "X-Evil" not in echoed
    assert "\r" not in echoed
    assert "\n" not in echoed
    assert len(echoed) == 16


@pytest.mark.asyncio
async def test_oversized_request_id_capped(client):
    """A multi-KB request ID is discarded, not reflected."""
    resp = await client.get("/health", headers={"X-Request-ID": "z" * 4096})
    assert resp.status_code == 200
    assert len(resp.headers["X-Request-ID"]) == 16


@pytest.mark.asyncio
async def test_valid_request_id_preserved(client):
    """A well-formed client ID still round-trips for correlation."""
    resp = await client.get("/health", headers={"X-Request-ID": "trace-abc_123"})
    assert resp.status_code == 200
    assert resp.headers["X-Request-ID"] == "trace-abc_123"
