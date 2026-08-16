"""URL ingestion engine — fetch URLs, extract content, generate ideas."""

import ipaddress
import re
import socket
from dataclasses import dataclass
from urllib.parse import parse_qs, urlencode, urlparse

import httpx

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


def _check_ssrf(hostname: str) -> None:
    """Resolve hostname and raise ValueError if it resolves to a private/reserved address.

    Protects against Server-Side Request Forgery (SSRF) by blocking requests
    to loopback, private, link-local, and other reserved IP ranges.

    Raises:
        ValueError: If the hostname resolves to a non-public IP address.
        socket.gaierror: If the hostname cannot be resolved (propagated to caller).
    """
    try:
        addrinfos = socket.getaddrinfo(hostname, None)
    except socket.gaierror:
        # DNS resolution failure — not a private IP issue; let caller handle
        raise

    for addrinfo in addrinfos:
        raw_ip = addrinfo[4][0]
        try:
            addr = ipaddress.ip_address(raw_ip)
        except ValueError:
            continue
        if addr.is_loopback or addr.is_private or addr.is_link_local or addr.is_reserved:
            raise ValueError(f"Requests to private/reserved addresses are not allowed: {raw_ip}")


def validate_url(url: str) -> bool:
    """Check if URL is valid http(s) and does not point to a private/reserved address.

    Returns:
        True if the URL is structurally valid and resolves to a public address.

    Raises:
        ValueError: If the URL resolves to a private, loopback, or link-local IP (SSRF guard).
    """
    if not url:
        return False
    try:
        parsed = urlparse(url)
        if not (parsed.scheme in ("http", "https") and bool(parsed.netloc)):
            return False
    except Exception:
        return False

    # Strip port from netloc to get bare hostname for DNS resolution
    hostname = parsed.hostname
    if not hostname:
        return False

    # For bare IP addresses, validate directly without a DNS lookup
    try:
        addr = ipaddress.ip_address(hostname)
        if addr.is_loopback or addr.is_private or addr.is_link_local or addr.is_reserved:
            raise ValueError(f"Requests to private/reserved addresses are not allowed: {hostname}")
        return True
    except ValueError as exc:
        # Re-raise only the SSRF guard errors; ignore the "not a valid IP" parse error
        if "not allowed" in str(exc):
            raise

    # Hostname is not a bare IP — resolve via DNS
    _check_ssrf(hostname)
    return True


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


# Only the first 5000 characters ever reach an idea, so there is no reason to
# pull a whole response into memory. Without a ceiling a hostile (or merely
# broken) endpoint can stream until the single-process app dies.
MAX_RESPONSE_BYTES = 2 * 1024 * 1024


async def _get_bounded(client: httpx.AsyncClient, url: str) -> tuple[httpx.Response, str]:
    """GET ``url``, reading at most ``MAX_RESPONSE_BYTES`` of the body.

    Returns the response (headers/status only — the body is not buffered on it)
    alongside the decoded text. Oversized bodies are truncated, not rejected:
    the useful part of an article is at the top, and the caller keeps 5000
    characters regardless.
    """
    async with client.stream("GET", url) as response:
        if response.status_code >= 400:
            return response, ""

        declared = response.headers.get("content-length", "")
        if declared.isdigit() and int(declared) > MAX_RESPONSE_BYTES:
            raise UrlFetchError(f"Response too large ({declared} bytes) fetching {url}")

        chunks: list[bytes] = []
        total = 0
        async for chunk in response.aiter_bytes():
            chunks.append(chunk)
            total += len(chunk)
            if total >= MAX_RESPONSE_BYTES:
                break

    raw = b"".join(chunks)[:MAX_RESPONSE_BYTES]
    return response, raw.decode(response.encoding or "utf-8", errors="replace")


async def fetch_url_content(url: str) -> UrlContent:
    """Fetch URL and extract content.

    Validates the URL for SSRF safety before making any network request.
    Redirects are disabled to prevent redirect-based SSRF bypasses.

    Raises:
        ValueError: If the URL resolves to a private/reserved address.
        UrlFetchError: If the HTTP response indicates an error (status >= 400).
    """
    # SSRF guard — must run before any network I/O
    validate_url(url)

    async with httpx.AsyncClient(follow_redirects=False, timeout=30.0) as client:
        response, body = await _get_bounded(client, url)

    if response.status_code >= 400:
        raise UrlFetchError(f"HTTP {response.status_code} fetching {url}")

    text = body
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


_SECURITY_KEYWORDS = {
    "crypto",
    "tls",
    "ssl",
    "pki",
    "certificate",
    "merkle",
    "hash",
    "encrypt",
    "vulnerability",
    "cve",
    "exploit",
    "attack",
    "malware",
    "ransomware",
    "threat",
    "authentication",
    "auth",
    "oauth",
    "jwt",
    "token",
    "key",
    "firewall",
    "intrusion",
    "pentest",
    "ctf",
    "reverse engineering",
    "binary",
    "fuzzing",
    "sanitize",
    "injection",
    "xss",
    "csrf",
    "zero trust",
    "siem",
    "soc",
    "audit",
    "compliance",
    "gdpr",
    "nist",
}

_CATEGORY_KEYWORDS: dict[str, list[str]] = {
    "security-tool": ["security", "scanner", "monitor", "detect", "protect", "guard"],
    "vulnerability-research": ["vuln", "cve", "exploit", "bug", "flaw", "weakness"],
    "crypto-infrastructure": ["crypto", "tls", "pki", "certificate", "merkle", "hash", "rsa", "ecdsa"],
    "compliance": ["compliance", "gdpr", "nist", "audit", "regulation", "policy", "standard"],
    "devops-tool": ["deploy", "cicd", "docker", "kubernetes", "k8s", "pipeline", "infra"],
    "privacy-tool": ["privacy", "anonymize", "tracking", "consent", "pii", "data protection"],
}


def _heuristic_idea_from_content(content: UrlContent, category_hint: str | None = None):
    """Build a basic Idea from URL content without an API call.

    Used as a fallback when no Anthropic API key is configured.
    """
    from project_forge.models import Idea, IdeaCategory

    title = content.title or content.domain
    text_lower = (content.text + " " + title).lower()

    # Pick category
    if category_hint:
        try:
            category = IdeaCategory(category_hint)
        except ValueError:
            category = IdeaCategory.SECURITY_TOOL
    else:
        category = IdeaCategory.SECURITY_TOOL
        best_score = 0
        for cat_value, keywords in _CATEGORY_KEYWORDS.items():
            score = sum(1 for kw in keywords if kw in text_lower)
            if score > best_score:
                best_score = score
                try:
                    category = IdeaCategory(cat_value)
                except ValueError:
                    pass

    # Extract first meaningful sentence from body as tagline
    sentences = [s.strip() for s in re.split(r"[.!?]", content.text) if len(s.strip()) > 30]
    tagline = sentences[0][:120] if sentences else f"Tool inspired by insights from {content.domain}"

    # Name: derive from title or domain
    name = re.sub(r"\s*[-|:]\s*.*$", "", title).strip() or content.domain
    name = name[:60]

    description = f"Idea derived from: {content.url}\n\n" + (
        content.text[:800] if content.text else f"Source: {content.domain}"
    )

    return Idea(
        name=name,
        tagline=tagline,
        description=description,
        category=category,
        market_analysis="Derived from external source — manual market analysis required.",
        feasibility_score=0.6,
        mvp_scope="Review source content and define scope before implementation.",
        tech_stack=[],
        source_url=clean_url(content.url),
    )


async def generate_idea_from_url(content: UrlContent, category_hint: str | None = None):
    """Generate an idea from URL content via IdeaGenerator.

    Falls back to a heuristic extraction when no Anthropic API key is configured.
    """
    import os

    from project_forge.config import settings

    content.url = clean_url(content.url)

    key = settings.anthropic_api_key or os.environ.get("ANTHROPIC_API_KEY", "")
    if not key:
        return _heuristic_idea_from_content(content, category_hint=category_hint)

    from project_forge.engine.generator import IdeaGenerator

    generator = IdeaGenerator()
    idea = await generator.generate_from_content(content, category_hint=category_hint)
    return idea
