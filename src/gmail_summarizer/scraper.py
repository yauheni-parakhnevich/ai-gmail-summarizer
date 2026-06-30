"""Fetch and parse vacancy pages for job details."""

import contextlib
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
    # Only advertise encodings `requests` decodes natively. Brotli ("br") needs the
    # optional brotli package; without it servers like jobcheck.com return raw brotli
    # bytes that surface as mojibake. See _looks_like_garbage for the safety net.
    "Accept-Encoding": "gzip, deflate",
}

# Sites that need a real browser (JS-rendered, login walls, tracking redirects)
_BROWSER_DOMAINS = {"linkedin.com", "xing.com"}

# Domains whose URLs are tracking redirects — resolve with requests before scraping
_REDIRECT_DOMAINS = {"xing.com", "awstrack.me"}


# ---------------------------------------------------------------------------
# Single-shot module API (back-compat wrappers around BrowserSession)
# ---------------------------------------------------------------------------


def fetch_page_text(config: Config, url: str) -> tuple[str, str | None]:
    """Fetch a vacancy page and return (resolved_url, page_text).

    page_text is None if the fetch failed or the listing is closed.
    Does NOT call Claude — only network + DOM extraction.

    Single-shot convenience wrapper. To scrape many URLs, open one
    ``BrowserSession`` and reuse it — that keeps a single logged-in browser
    alive across the whole batch instead of cold-launching per URL.
    """
    with BrowserSession(config) as session:
        return session.fetch_page_text(url)


def scrape_vacancy(config: Config, url: str) -> VacancyInfo | None:
    """Fetch a vacancy page and extract job details. Returns None on failure."""
    with BrowserSession(config) as session:
        return session.scrape_vacancy(url)


# ---------------------------------------------------------------------------
# Reusable browser session
# ---------------------------------------------------------------------------


class BrowserSession:
    """One live Playwright browser reused across many fetches.

    Lazily launches Chromium on the first browser-bound URL, keeps a single
    context per domain group (``linkedin`` / ``xing`` / ``generic``) seeded
    from the saved ``storage_state``, logs in once, and reuses that warm,
    authenticated context for every subsequent URL. This avoids the cold
    browser launch per URL that made batch scraping slow and tripped
    LinkedIn's anti-bot throttling (which surfaced as guest/login-wall pages).

    Non-browser URLs (e.g. jobs.ch) use plain ``requests`` and never launch a
    browser. Use as a context manager::

        with BrowserSession(config) as session:
            for url in urls:
                resolved, text = session.fetch_page_text(url)
    """

    def __init__(self, config: Config, *, headless: bool = True):
        self.config = config
        self._headless = headless
        self._pw = None
        self._browser = None
        # group label -> (context, state_path | None)
        self._contexts: dict[str, tuple[object, Path | None]] = {}

    def __enter__(self) -> "BrowserSession":
        return self

    def __exit__(self, *_exc) -> None:
        self.close()

    # -- lifecycle ----------------------------------------------------------

    def _ensure_browser(self) -> None:
        if self._browser is None:
            self._pw = sync_playwright().start()
            self._browser = self._pw.chromium.launch(headless=self._headless)

    def _context_for(self, group: str, state_path: Path | None):
        existing = self._contexts.get(group)
        if existing is not None:
            return existing[0]
        self._ensure_browser()
        if state_path and state_path.exists():
            context = self._browser.new_context(storage_state=str(state_path))
        else:
            context = self._browser.new_context()
        self._contexts[group] = (context, state_path)
        return context

    def close(self) -> None:
        for context, state_path in self._contexts.values():
            if state_path is not None:
                try:
                    state_path.parent.mkdir(parents=True, exist_ok=True)
                    context.storage_state(path=str(state_path))
                except Exception:
                    pass
            with contextlib.suppress(Exception):
                context.close()
        self._contexts.clear()
        if self._browser is not None:
            with contextlib.suppress(Exception):
                self._browser.close()
            self._browser = None
        if self._pw is not None:
            with contextlib.suppress(Exception):
                self._pw.stop()
            self._pw = None

    # -- public fetch API ---------------------------------------------------

    def fetch_page_text(self, url: str) -> tuple[str, str | None]:
        """Fetch one page via the shared browser/requests. See module docstring."""
        resolved_url = _resolve_redirect(url)
        if resolved_url != url:
            print(f"    [redirect] {url[:60]}... -> {resolved_url[:80]}")
            url = resolved_url

        if _needs_browser(url):
            page_text = self._fetch_browser(url)
        else:
            page_text = _fetch_page_requests(url)
            if not page_text:
                page_text = self._fetch_browser(url)

        if not page_text:
            return url, None

        if _is_closed_listing(page_text):
            print(f"    [skip] Listing no longer accepting applications: {url[:80]}")
            return url, None

        return url, page_text

    def scrape_vacancy(self, url: str) -> VacancyInfo | None:
        """Fetch + Claude-extract one vacancy via the shared session."""
        resolved_url, page_text = self.fetch_page_text(url)
        if not page_text:
            return None
        return _extract_vacancy_info(self.config, resolved_url, page_text)

    # -- browser internals --------------------------------------------------

    def _fetch_browser(self, url: str) -> str | None:
        clean_url = url.replace("/comm/jobs/", "/jobs/")
        group, state_path = _domain_group(url)
        context = self._context_for(group, state_path)
        page = context.new_page()
        try:
            result = self._load(page, clean_url, group)
        finally:
            with contextlib.suppress(Exception):
                page.close()

        if result is _NEEDS_VERIFICATION:
            # Interactive challenge: fall back to the standalone headful retry,
            # which drives its own visible browser. Rare — the saved session is
            # normally valid, so login does not trigger inside the batch.
            return _fetch_with_playwright(self.config, url)
        return result if isinstance(result, str) else None

    def _load(self, page, clean_url: str, group: str) -> "str | object | None":
        try:
            page.goto(clean_url, wait_until="domcontentloaded", timeout=30000)

            if group == "linkedin" and ("login" in page.url or "authwall" in page.url):
                login_result = _handle_login(
                    self.config,
                    page,
                    clean_url,
                    self._headless,
                    label="linkedin",
                    login_url="https://www.linkedin.com/login",
                    email=self.config.linkedin_email,
                    password=self.config.linkedin_password,
                    user_field="#username",
                    pass_field="#password",
                    submit="button[type='submit']",
                    state_path=LINKEDIN_STATE_PATH,
                    challenge_check=lambda u: "checkpoint" in u or "challenge" in u,
                )
                if login_result is not None:
                    return login_result if login_result is _NEEDS_VERIFICATION else None

            if group == "xing" and ("login" in page.url or "/start" in page.url):
                login_result = _handle_login(
                    self.config,
                    page,
                    clean_url,
                    self._headless,
                    label="xing",
                    login_url="https://login.xing.com/",
                    email=self.config.xing_email,
                    password=self.config.xing_password,
                    user_field='input[name="username"]',
                    pass_field='input[name="password"]',
                    submit='button[type="submit"]',
                    state_path=XING_STATE_PATH,
                    challenge_check=lambda u: "captcha" in u or "challenge" in u,
                )
                if login_result is not None:
                    return login_result if login_result is _NEEDS_VERIFICATION else None

            return self._read_content(page, group, clean_url)

        except PlaywrightTimeout:
            print("    [scrape error] Page timed out")
            return None
        except Exception as e:
            print(f"    [scrape error] Playwright error: {e}")
            return None

    def _read_content(self, page, group: str, clean_url: str) -> str | None:
        """Read page text, retrying past LinkedIn guest/login-wall variants.

        LinkedIn sometimes serves a truncated logged-out job view at the same
        ``/jobs/view/...`` URL — no login/authwall redirect — even with a valid
        session, typically under throttling. Detect that thin guest page and
        reload a couple of times on the warm, authenticated context before
        giving up, instead of silently accepting the wall as a success.
        """
        is_linkedin = group == "linkedin"
        attempts = 3 if is_linkedin else 1
        last = ""
        for attempt in range(attempts):
            if is_linkedin:
                with contextlib.suppress(Exception):
                    page.wait_for_selector("text=About the job", timeout=6000)
            page.wait_for_timeout(2500 if attempt == 0 else 4000)
            last = _clean_html_to_text(page.content())
            if len(last) >= 50 and not _looks_like_linkedin_wall(clean_url, last):
                return last
            if attempt < attempts - 1:
                print(f"    [linkedin wall] thin/guest page, retry {attempt + 1}/{attempts - 1}: {clean_url[:70]}")
                try:
                    page.goto(clean_url, wait_until="domcontentloaded", timeout=30000)
                except Exception:
                    break

        if is_linkedin and _looks_like_linkedin_wall(clean_url, last):
            print(f"    [linkedin wall] still walled after retries: {clean_url[:70]}")
            return None
        if len(last) < 50:
            print(f"    [scrape error] Page text too short ({len(last)} chars) after Playwright")
            return None
        return last


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


_CLOSED_MARKERS = (
    "no longer accepting applications",
    "nimmt keine bewerbungen mehr",
    "bewerbungen werden nicht mehr",
)


def _is_closed_listing(page_text: str) -> bool:
    low = page_text.lower()
    return any(m in low for m in _CLOSED_MARKERS)


# Markers of a LinkedIn guest / premium upsell wall (shown when not authenticated)
_LINKEDIN_WALL_MARKERS = (
    "reactivate premium",
    "join now to see",
    "sign in to view",
    "looking for talent?",
)


def _looks_like_linkedin_wall(url: str, text: str) -> bool:
    """True if a LinkedIn page is the truncated guest/login-wall variant.

    Full authenticated job pages contain the "About the job" section and run to
    several thousand characters (they may also carry premium-upsell strings, so
    the upsell markers alone are not enough — the absence of real job content
    plus a short body is what distinguishes the wall).
    """
    if "linkedin.com" not in urlparse(url).netloc.lower():
        return False
    low = text.lower()
    if "about the job" in low:
        return False
    return len(text) < 2500 and any(m in low for m in _LINKEDIN_WALL_MARKERS)


def _domain_group(url: str) -> tuple[str, Path | None]:
    """Map a URL to its (context group, saved-session path)."""
    netloc = urlparse(url).netloc.lower()
    if "linkedin.com" in netloc:
        return "linkedin", LINKEDIN_STATE_PATH
    if "xing.com" in netloc:
        return "xing", XING_STATE_PATH
    return "generic", None


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


def _looks_like_garbage(text: str) -> bool:
    """True if the body is mostly non-printable — e.g. an undecoded compressed response."""
    sample = text[:1000]
    if not sample:
        return False
    printable = sum(1 for c in sample if c.isprintable() or c in "\n\r\t")
    return printable / len(sample) < 0.8


def _clean_html_to_text(html: str) -> str:
    """Strip chrome tags and return readable page text (capped at 8000 chars)."""
    soup = BeautifulSoup(html, "html.parser")
    for tag in soup(["script", "style", "nav", "footer", "header", "noscript", "svg"]):
        tag.decompose()
    return soup.get_text(separator="\n", strip=True)[:8000]


def _fetch_page_requests(url: str) -> str | None:
    """Fetch a page using requests (for non-LinkedIn sites)."""
    try:
        resp = requests.get(url, timeout=15, headers=_HEADERS, allow_redirects=True)
        resp.raise_for_status()
    except requests.RequestException as e:
        print(f"    [scrape error] {e}")
        return None

    if _looks_like_garbage(resp.text):
        enc = resp.headers.get("Content-Encoding", "?")
        print(f"    [scrape error] Undecodable body (Content-Encoding: {enc})")
        return None

    text = _clean_html_to_text(resp.text)
    if len(text) < 50:
        print(f"    [scrape error] Page text too short ({len(text)} chars)")
        return None
    return text


# Sentinel indicating a site needs interactive verification
_NEEDS_VERIFICATION = object()


def _fetch_with_playwright(config: Config, url: str) -> str | None:
    """Fetch a page using a dedicated browser. Retries visibly on auth challenge.

    Used as the fallback when a shared-session fetch hits an interactive login
    challenge that needs a human; drives its own browser so it can run headful.
    """
    result = _run_playwright(config, url, headless=True)
    if result is _NEEDS_VERIFICATION:
        print("    Retrying with visible browser for verification...")
        result = _run_playwright(config, url, headless=False)
    return result if isinstance(result, str) else None


def _run_playwright(config: Config, url: str, *, headless: bool) -> str | object | None:
    """Run a one-off browser to fetch a page (verification fallback path)."""
    group, state_path = _domain_group(url)
    with BrowserSession(config, headless=headless) as session:
        session._ensure_browser()
        context = session._context_for(group, state_path)
        page = context.new_page()
        try:
            return session._load(page, url.replace("/comm/jobs/", "/jobs/"), group)
        finally:
            with contextlib.suppress(Exception):
                page.close()


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
