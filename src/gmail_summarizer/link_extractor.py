"""Extract job vacancy URLs from email bodies using Claude for intelligent filtering."""

import json
import re
from urllib.parse import urlparse, urlencode, parse_qs

import anthropic
from bs4 import BeautifulSoup

from gmail_summarizer.config import Config
from gmail_summarizer.gmail import Email

# Tracking/marketing query params to strip from URLs
TRACKING_PARAMS = {
    "utm_source", "utm_medium", "utm_campaign", "utm_content", "utm_term",
    "trk", "midToken", "midSig", "lipi", "lgCta", "lgTemp",
    "uid", "pid", "hash", "profile-id", "reference-date",
}

SYSTEM_PROMPT = """\
You are a link analyst. Given an email with numbered links, \
identify ONLY the links that lead to specific job vacancy pages.

Include links that:
- Point to a specific job posting (e.g., /vacancies/detail/..., /jobs/view/...)
- Are "Apply now", "View job", "See position" type links
- Lead to a specific role on job boards (LinkedIn, Indeed, Glassdoor, jobs.ch, etc.)

Exclude links that:
- Are unsubscribe, preferences, or email management links
- Lead to generic homepages, feeds, notifications, or search result pages
- Are social media, privacy policy, or terms of service links

Return a JSON array of the link NUMBERS that are vacancy links.
Example: [2, 5, 8]

If none, return: []
Only output the JSON array, nothing else."""


def extract_job_links(config: Config, email: Email) -> list[str]:
    """Extract job vacancy URLs from an email using Claude for intelligent filtering."""
    links = _gather_links_with_context(email)

    if not links:
        return []

    client = anthropic.Anthropic(api_key=config.anthropic_api_key)

    # Build a compact numbered list for Claude
    prompt_lines = [f"Email subject: {email.subject}", f"From: {email.sender}", ""]
    for i, (url, anchor, _full_url) in enumerate(links, 1):
        prompt_lines.append(f"{i}. URL: {url}")
        if anchor:
            prompt_lines.append(f"   Anchor: {anchor}")

    prompt = "\n".join(prompt_lines)

    response = client.messages.create(
        model=config.claude_model,
        max_tokens=256,
        system=SYSTEM_PROMPT,
        messages=[{"role": "user", "content": prompt}],
    )

    response_text = response.content[0].text.strip()
    try:
        indices: list[int] = json.loads(response_text)
    except (json.JSONDecodeError, TypeError):
        return []

    # Map indices back to full original URLs
    result = []
    for idx in indices:
        if 1 <= idx <= len(links):
            result.append(links[idx - 1][2])  # full_url
    return result


def _gather_links_with_context(email: Email) -> list[tuple[str, str, str]]:
    """Extract links as (clean_url, anchor_text, full_original_url) tuples."""
    entries: list[tuple[str, str, str]] = []
    seen: set[str] = set()

    if email.body_html:
        soup = BeautifulSoup(email.body_html, "html.parser")
        for a_tag in soup.find_all("a", href=True):
            href = a_tag["href"].strip()
            if not _is_http_url(href):
                continue
            key = _dedup_key(href)
            if key in seen:
                continue
            seen.add(key)
            clean = _clean_url(href)
            anchor = a_tag.get_text(strip=True)
            entries.append((clean, anchor, href))

    if email.body_text:
        for url in re.findall(r"https?://[^\s<>\"']+", email.body_text):
            url = url.rstrip(".,;:!?)>]}")
            if not _is_http_url(url):
                continue
            key = _dedup_key(url)
            if key not in seen:
                seen.add(key)
                clean = _clean_url(url)
                entries.append((clean, "", url))

    return entries


def _dedup_key(url: str) -> str:
    """Extract a deduplication key — just scheme + host + path, ignoring all query params."""
    parsed = urlparse(url)
    return f"{parsed.scheme}://{parsed.netloc}{parsed.path}"


def _clean_url(url: str) -> str:
    """Strip tracking parameters to produce a shorter, cleaner URL for display."""
    parsed = urlparse(url)
    if not parsed.query:
        return url
    params = parse_qs(parsed.query, keep_blank_values=True)
    filtered = {k: v for k, v in params.items() if k.lower() not in TRACKING_PARAMS}
    if filtered:
        clean_query = urlencode(filtered, doseq=True)
        return parsed._replace(query=clean_query).geturl()
    return parsed._replace(query="").geturl()


def _is_http_url(url: str) -> bool:
    try:
        parsed = urlparse(url)
        return parsed.scheme in ("http", "https") and bool(parsed.netloc)
    except ValueError:
        return False
