"""URL ingestion engine — fetch URLs, extract content, generate ideas."""

import re
from dataclasses import dataclass
from urllib.parse import parse_qs, urlencode, urlparse

import httpx

from project_forge.engine.generator import IdeaGenerator  # noqa: F401 — needed for mock patch targets


# Tracking parameters to strip from URLs
TRACKING_PARAMS = {
    "utm_source",
    "utm_medium",
    "utm_campaign",
    "utm_content",
    "utm_term",
    "ref",
    "fbclid",
    "gclid",
}


class UrlFetchError(Exception):
    """Raised when a URL cannot be fetched successfully."""


@dataclass
class UrlContent:
    url: str
    domain: str
    title: str
    text: str


def validate_url(url: str) -> bool:
    """Check if URL is valid http(s)."""
    if not url:
        return False
    try:
        parsed = urlparse(url)
        return parsed.scheme in ("http", "https") and bool(parsed.netloc)
    except Exception:
        return False


def extract_domain(url: str) -> str:
    """Extract clean domain from URL (strip www.)."""
    parsed = urlparse(url)
    domain = parsed.netloc
    if domain.startswith("www."):
        domain = domain[4:]
    return domain


def clean_url(url: str) -> str:
    """Remove tracking parameters from URL."""
    parsed = urlparse(url)
    params = parse_qs(parsed.query)
    clean_params = {k: v for k, v in params.items() if k not in TRACKING_PARAMS}
    if clean_params:
        clean_query = urlencode(clean_params, doseq=True)
        return f"{parsed.scheme}://{parsed.netloc}{parsed.path}?{clean_query}"
    return f"{parsed.scheme}://{parsed.netloc}{parsed.path}"


async def fetch_url_content(url: str) -> UrlContent:
    """Fetch URL and extract content."""
    async with httpx.AsyncClient(follow_redirects=True, timeout=30.0) as client:
        response = await client.get(url)

    if response.status_code >= 400:
        raise UrlFetchError(f"HTTP {response.status_code} fetching {url}")

    text = response.text
    domain = extract_domain(url)

    # Extract title from HTML and strip tags for text content
    title = ""
    content_type = response.headers.get("content-type", "")
    if "html" in content_type:
        title_match = re.search(r"<title[^>]*>(.*?)</title>", text, re.DOTALL | re.IGNORECASE)
        if title_match:
            title = title_match.group(1).strip()
        # Strip script/style blocks first, then all other tags
        text = re.sub(r"<script[^>]*>.*?</script>", "", text, flags=re.DOTALL | re.IGNORECASE)
        text = re.sub(r"<style[^>]*>.*?</style>", "", text, flags=re.DOTALL | re.IGNORECASE)
        text = re.sub(r"<[^>]+>", " ", text)
        text = re.sub(r"\s+", " ", text).strip()

    if not title:
        title = domain  # Fallback to domain when no HTML title found

    return UrlContent(url=url, domain=domain, title=title, text=text[:5000])


async def generate_idea_from_url(content: UrlContent, category_hint: str | None = None):
    """Generate an idea from URL content via IdeaGenerator."""
    content.url = clean_url(content.url)
    generator = IdeaGenerator()
    idea = await generator.generate_from_content(content, category_hint=category_hint)
    return idea
