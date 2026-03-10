"""Fetch and parse vacancy pages for job details."""

import json
import re
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import parse_qs, urlparse

import anthropic
import requests
from bs4 import BeautifulSoup
from playwright.sync_api import TimeoutError as PlaywrightTimeout
from playwright.sync_api import sync_playwright

from gmail_summarizer.config import Config

LINKEDIN_STATE_PATH = Path("credentials/linkedin_state.json")
XING_STATE_PATH = Path("credentials/xing_state.json")

EXTRACT_PROMPT = """\
Extract job posting details from the following webpage text. \
Return a JSON object with these fields:
- title: job title
- company: company name
- location: job location
- description: brief job description (2-3 sentences)
- requirements: list of key requirements
- salary: salary info if available, otherwise null

Only output the JSON object, nothing else."""


@dataclass
class VacancyInfo:
    url: str
    title: str
    company: str
    location: str
    description: str
    requirements: list[str]
    salary: str | None


_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9,de;q=0.8",
    "Accept-Encoding": "gzip, deflate, br",
}


def scrape_vacancy(config: Config, url: str) -> VacancyInfo | None:
    """Fetch a vacancy page and extract job details. Returns None on failure."""
    # Resolve tracking redirects (e.g. xing.com/m/..., awstrack.me) to actual destination
    resolved_url = _resolve_redirect(url)
    if resolved_url != url:
        print(f"    [redirect] {url[:60]}... -> {resolved_url[:80]}")
        url = resolved_url

    if _needs_browser(url):
        page_text = _fetch_with_playwright(config, url)
    else:
        page_text = _fetch_page_requests(url)
        # Fallback to Playwright if requests got empty content (JS-rendered or tracking redirect)
        if not page_text:
            page_text = _fetch_with_playwright(config, url)

    if not page_text:
        return None

    return _extract_vacancy_info(config, url, page_text)


# Sites that need a real browser (JS-rendered, login walls, tracking redirects)
_BROWSER_DOMAINS = {"linkedin.com", "xing.com"}

# Domains whose URLs are tracking redirects — resolve with requests before scraping
_REDIRECT_DOMAINS = {"xing.com", "awstrack.me"}


def _needs_browser(url: str) -> bool:
    domain = urlparse(url).netloc.lower()
    return any(d in domain for d in _BROWSER_DOMAINS)


def _resolve_redirect(url: str) -> str:
    """Follow redirects for tracking URLs (xing.com/m/..., awstrack.me)."""
    domain = urlparse(url).netloc.lower()
    if not any(d in domain for d in _REDIRECT_DOMAINS):
        return url

    try:
        resp = requests.get(url, timeout=10, headers=_HEADERS, allow_redirects=True)
        final = resp.url

        # Xing redirects /m/ links through login.xing.com with the real URL in dest_url
        parsed = urlparse(final)
        if "login.xing.com" in parsed.netloc or "login" in parsed.path:
            params = parse_qs(parsed.query)
            dest = params.get("dest_url", [""])[0]
            if dest:
                return dest

        # For other redirects, use the final URL if it looks useful
        if parsed.path and parsed.path not in ("/", ""):
            return final
    except requests.RequestException:
        pass

    return url


def _fetch_page_requests(url: str) -> str | None:
    """Fetch a page using requests (for non-LinkedIn sites)."""
    try:
        resp = requests.get(url, timeout=15, headers=_HEADERS, allow_redirects=True)
        resp.raise_for_status()
    except requests.RequestException as e:
        print(f"    [scrape error] {e}")
        return None

    soup = BeautifulSoup(resp.text, "html.parser")
    for tag in soup(["script", "style", "nav", "footer", "header", "noscript", "svg"]):
        tag.decompose()

    text = soup.get_text(separator="\n", strip=True)[:8000]
    if len(text) < 50:
        print(f"    [scrape error] Page text too short ({len(text)} chars)")
        return None
    return text


def _fetch_with_playwright(config: Config, url: str) -> str | None:
    """Fetch a page using Playwright. Retries with visible browser on auth challenge."""
    result = _run_playwright(config, url, headless=True)
    if result is _NEEDS_VERIFICATION:
        print("    Retrying with visible browser for verification...")
        result = _run_playwright(config, url, headless=False)
    return result if isinstance(result, str) else None


# Sentinel indicating a site needs interactive verification
_NEEDS_VERIFICATION = object()


def _run_playwright(config: Config, url: str, *, headless: bool) -> str | object | None:
    """Run Playwright to fetch a page. Returns page text, _NEEDS_VERIFICATION, or None."""
    clean_url = url.replace("/comm/jobs/", "/jobs/")
    is_linkedin = "linkedin.com" in urlparse(url).netloc.lower()
    is_xing = "xing.com" in urlparse(url).netloc.lower()

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=headless)

        # Load saved session if available
        state_path = LINKEDIN_STATE_PATH if is_linkedin else XING_STATE_PATH if is_xing else None
        if state_path and state_path.exists():
            context = browser.new_context(storage_state=str(state_path))
        else:
            context = browser.new_context()

        page = context.new_page()

        try:
            page.goto(clean_url, wait_until="domcontentloaded", timeout=30000)

            # Handle LinkedIn login
            if "linkedin.com" in page.url and ("login" in page.url or "authwall" in page.url):
                login_result = _handle_login(
                    config,
                    page,
                    clean_url,
                    headless,
                    label="linkedin",
                    login_url="https://www.linkedin.com/login",
                    email=config.linkedin_email,
                    password=config.linkedin_password,
                    user_field="#username",
                    pass_field="#password",
                    submit="button[type='submit']",
                    state_path=LINKEDIN_STATE_PATH,
                    challenge_check=lambda u: "checkpoint" in u or "challenge" in u,
                )
                if login_result is not None:  # _NEEDS_VERIFICATION or False (no creds)
                    context.close()
                    browser.close()
                    return login_result if login_result is _NEEDS_VERIFICATION else None

            # Handle Xing login
            if "xing.com" in page.url and ("login" in page.url or "/start" in page.url):
                login_result = _handle_login(
                    config,
                    page,
                    clean_url,
                    headless,
                    label="xing",
                    login_url="https://login.xing.com/",
                    email=config.xing_email,
                    password=config.xing_password,
                    user_field='input[name="username"]',
                    pass_field='input[name="password"]',
                    submit='button[type="submit"]',
                    state_path=XING_STATE_PATH,
                    challenge_check=lambda u: "captcha" in u or "challenge" in u,
                )
                if login_result is not None:  # _NEEDS_VERIFICATION or False (no creds)
                    context.close()
                    browser.close()
                    return login_result if login_result is _NEEDS_VERIFICATION else None

            page.wait_for_timeout(3000)
            html = page.content()

        except PlaywrightTimeout:
            print("    [scrape error] Page timed out")
            context.close()
            browser.close()
            return None
        except Exception as e:
            print(f"    [scrape error] Playwright error: {e}")
            context.close()
            browser.close()
            return None

        # Save session for future runs
        if state_path:
            try:
                state_path.parent.mkdir(parents=True, exist_ok=True)
                context.storage_state(path=str(state_path))
            except Exception:
                pass

        context.close()
        browser.close()

    soup = BeautifulSoup(html, "html.parser")
    for tag in soup(["script", "style", "nav", "footer", "header", "noscript", "svg"]):
        tag.decompose()

    text = soup.get_text(separator="\n", strip=True)[:8000]
    if len(text) < 50:
        print(f"    [scrape error] Page text too short ({len(text)} chars) after Playwright")
        return None
    return text


def _handle_login(
    config: Config,
    page,
    target_url: str,
    headless: bool,
    *,
    label: str,
    login_url: str,
    email: str,
    password: str,
    user_field: str,
    pass_field: str,
    submit: str,
    state_path: Path,
    challenge_check,
) -> object | None:
    """Handle login for a site. Returns None on success, _NEEDS_VERIFICATION, or False (no creds)."""
    if not email or not password:
        print(f"    [{label}] Login required but {label}_email/{label}_password not set in profile config")
        return False

    print(f"    [{label}] Logging in...")
    page.goto(login_url, wait_until="domcontentloaded")
    page.fill(user_field, email)
    page.fill(pass_field, password)
    page.click(submit)
    page.wait_for_load_state("domcontentloaded", timeout=30000)

    if challenge_check(page.url):
        if headless:
            print(f"    [{label}] Security verification required")
            return _NEEDS_VERIFICATION
        else:
            print(f"    [{label}] Complete verification in the browser window...")
            page.wait_for_url(
                lambda u: not challenge_check(u),
                timeout=120000,
            )
            print(f"    [{label}] Verification completed!")

    print(f"    [{label}] Login successful, saving session...")
    state_path.parent.mkdir(parents=True, exist_ok=True)
    page.context.storage_state(path=str(state_path))

    page.goto(target_url, wait_until="domcontentloaded", timeout=30000)
    # Return None to signal "continue processing" (not an error)
    return None  # noqa: RET501


def _extract_vacancy_info(config: Config, url: str, page_text: str) -> VacancyInfo | None:
    """Use Claude to extract structured vacancy info from page text."""
    client = anthropic.Anthropic(api_key=config.anthropic_api_key)
    try:
        response = client.messages.create(
            model=config.claude_model,
            max_tokens=1024,
            system=EXTRACT_PROMPT,
            messages=[{"role": "user", "content": page_text}],
        )

        data = json.loads(_strip_code_fences(response.content[0].text))

        reqs = data.get("requirements", [])
        if isinstance(reqs, str):
            reqs = [reqs] if reqs else []
        elif not isinstance(reqs, list):
            reqs = []

        return VacancyInfo(
            url=url,
            title=data.get("title", "Unknown"),
            company=data.get("company", "Unknown"),
            location=data.get("location", "Unknown"),
            description=data.get("description", ""),
            requirements=reqs,
            salary=data.get("salary"),
        )
    except json.JSONDecodeError as e:
        print(f"    [scrape error] Claude returned invalid JSON: {e}")
        return None
    except anthropic.APIError as e:
        print(f"    [scrape error] Claude API error: {e}")
        return None


def _strip_code_fences(text: str) -> str:
    """Remove markdown code fences (```json ... ```) from Claude responses."""
    text = text.strip()
    text = re.sub(r"^```(?:json)?\s*\n?", "", text)
    text = re.sub(r"\n?```\s*$", "", text)
    return text.strip()
