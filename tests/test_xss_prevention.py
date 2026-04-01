"""XSS prevention tests for idea detail template.

These tests verify that LLM-generated content is never injected as raw HTML,
and that newlines are handled via CSS rather than HTML `<br>` tags.
"""

from pathlib import Path

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient

from project_forge.models import Idea, IdeaCategory
from project_forge.web.app import app, db


@pytest_asyncio.fixture
async def client(tmp_path):
    db.db_path = tmp_path / "test_xss.db"
    await db.connect()
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        yield client
    await db.close()


def _make_idea(**overrides) -> Idea:
    """Factory for a minimal valid Idea."""
    defaults = dict(
        name="XSS Test Idea",
        tagline="Security testing",
        description="line1\nline2",
        category=IdeaCategory.AUTOMATION,
        market_analysis="Good market.",
        feasibility_score=0.75,
        mvp_scope="Build it.",
        tech_stack=["python"],
    )
    defaults.update(overrides)
    return Idea(**defaults)


@pytest.mark.asyncio
async def test_idea_detail_no_raw_br_tags(client):
    """Newlines in description must NOT produce <br> tags in the HTML response.

    The `| replace('\\n', '<br>')` Jinja filter (or any equivalent) must not
    be present so that adding `|safe` later cannot open a stored-XSS path.
    """
    idea = _make_idea(description="line1\nline2")
    await db.save_idea(idea)

    resp = await client.get(f"/ideas/{idea.id}")
    assert resp.status_code == 200
    assert "<br>" not in resp.text, (
        "Response contains raw <br> tags — Jinja replace filter must be removed"
    )


@pytest.mark.asyncio
async def test_idea_detail_escapes_script_tags(client):
    """Script tags in LLM-generated content must be HTML-escaped, not rendered."""
    idea = _make_idea(description="<script>alert(1)</script>")
    await db.save_idea(idea)

    resp = await client.get(f"/ideas/{idea.id}")
    assert resp.status_code == 200
    assert "<script>" not in resp.text, (
        "Unescaped <script> tag found — content is not being auto-escaped"
    )
    assert "&lt;script&gt;" in resp.text, (
        "Expected HTML-escaped &lt;script&gt; was not found in response"
    )


def test_idea_detail_uses_css_whitespace():
    """The .prose CSS rule must include white-space: pre-line.

    The style is in the linked stylesheet, not inlined in HTML, so we check
    the CSS source directly.  This is the authoritative gate that the CSS
    approach is actually wired up.
    """
    css_path = (
        Path(__file__).parent.parent
        / "src"
        / "project_forge"
        / "web"
        / "static"
        / "style.css"
    )
    assert css_path.is_file(), f"style.css not found: {css_path}"
    css_content = css_path.read_text()

    # The .prose rule must carry white-space: pre-line so newlines render
    # visually without any HTML injection.
    assert "pre-line" in css_content, (
        ".prose rule in style.css is missing 'white-space: pre-line' — "
        "newlines will not render without HTML injection"
    )


def test_no_replace_br_in_templates():
    """No .html template file may contain the replace-newline-to-br pattern.

    This is a static analysis gate: if the pattern is re-introduced, this test
    fails immediately without needing a running server.
    """
    templates_dir = (
        Path(__file__).parent.parent
        / "src"
        / "project_forge"
        / "web"
        / "templates"
    )
    assert templates_dir.is_dir(), f"Templates directory not found: {templates_dir}"

    bad_pattern_single = "replace('\\n', '<br>')"
    bad_pattern_double = 'replace("\\n", "<br>")'

    violations: list[str] = []
    for html_file in templates_dir.glob("**/*.html"):
        content = html_file.read_text()
        if bad_pattern_single in content or bad_pattern_double in content:
            violations.append(str(html_file))

    assert violations == [], (
        "The replace-newline-to-br XSS trap was found in templates:\n"
        + "\n".join(f"  {v}" for v in violations)
    )
