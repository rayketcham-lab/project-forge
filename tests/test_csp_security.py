"""CSP security tests — no unsafe-inline scripts, no inline handlers in templates."""

from pathlib import Path

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient

from project_forge.web.app import app, db

TEMPLATES_DIR = Path(__file__).parent.parent / "src" / "project_forge" / "web" / "templates"
APP_JS = Path(__file__).parent.parent / "src" / "project_forge" / "web" / "static" / "app.js"


@pytest_asyncio.fixture
async def client(tmp_path):
    db.db_path = tmp_path / "test_csp.db"
    await db.connect()
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac
    await db.close()


@pytest.mark.asyncio
async def test_csp_no_unsafe_inline_scripts(client):
    """The script-src CSP directive must NOT contain 'unsafe-inline'."""
    resp = await client.get("/health")
    assert resp.status_code == 200
    csp = resp.headers.get("Content-Security-Policy", "")
    assert csp, "CSP header is missing entirely"

    # Extract only the script-src directive value
    directives = {
        part.strip().split()[0]: part.strip()
        for part in csp.split(";")
        if part.strip()
    }
    script_src = directives.get("script-src", "")
    assert "'unsafe-inline'" not in script_src, (
        f"'unsafe-inline' must not appear in script-src. Got: {script_src!r}"
    )


def test_no_inline_script_blocks_in_templates():
    """No template should contain a bare <script> block (only <script src=…> is allowed)."""
    html_files = list(TEMPLATES_DIR.glob("*.html"))
    assert html_files, "No HTML templates found — check TEMPLATES_DIR path"

    violations: list[str] = []
    for path in html_files:
        content = path.read_text()
        lines = content.splitlines()
        for lineno, line in enumerate(lines, start=1):
            stripped = line.strip().lower()
            # Match opening <script tags that are NOT <script src=
            if stripped.startswith("<script") and "src=" not in stripped:
                violations.append(f"{path.name}:{lineno}: {line.strip()}")

    assert not violations, (
        "Inline <script> blocks found in templates — move to app.js:\n"
        + "\n".join(violations)
    )


def test_no_onclick_handlers_in_templates():
    """No template should contain onclick= attributes."""
    html_files = list(TEMPLATES_DIR.glob("*.html"))
    assert html_files, "No HTML templates found — check TEMPLATES_DIR path"

    violations: list[str] = []
    for path in html_files:
        content = path.read_text()
        lines = content.splitlines()
        for lineno, line in enumerate(lines, start=1):
            if "onclick=" in line:
                violations.append(f"{path.name}:{lineno}: {line.strip()}")

    assert not violations, (
        "onclick= attributes found in templates — move to addEventListener in app.js:\n"
        + "\n".join(violations)
    )


def test_app_js_has_promote_function():
    """app.js must define the promoteProposal function."""
    content = APP_JS.read_text()
    assert "function promoteProposal" in content or "promoteProposal" in content, (
        "promoteProposal function not found in app.js"
    )
    # Stricter: must be a proper function declaration or expression
    assert "promoteProposal" in content


def test_app_js_has_reject_function():
    """app.js must define the rejectProposal function."""
    content = APP_JS.read_text()
    assert "rejectProposal" in content, (
        "rejectProposal function not found in app.js"
    )


def test_app_js_has_switch_tab():
    """app.js must define the switchTab function (already present, must not be removed)."""
    content = APP_JS.read_text()
    assert "function switchTab" in content, (
        "switchTab function not found in app.js — must not be removed"
    )
