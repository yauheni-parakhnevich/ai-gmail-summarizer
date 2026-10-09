from gmail_summarizer.scraper import (
    INDEED_STATE_PATH,
    _domain_group,
    _is_cloudflare_challenge,
    _needs_browser,
    _strip_code_fences,
)


class TestNeedsBrowser:
    def test_linkedin(self):
        assert _needs_browser("https://www.linkedin.com/jobs/view/123") is True

    def test_xing(self):
        assert _needs_browser("https://www.xing.com/jobs/detail/123") is True

    def test_regular_site(self):
        assert _needs_browser("https://jobs.example.com/position/42") is False

    def test_subdomain_linkedin(self):
        assert _needs_browser("https://ch.linkedin.com/jobs/view/1") is True

    def test_indeed(self):
        assert _needs_browser("https://ch.indeed.com/rc/clk/dl?jk=abc") is True


class TestIndeed:
    def test_domain_group(self):
        assert _domain_group("https://ch.indeed.com/viewjob?jk=abc") == ("indeed", INDEED_STATE_PATH)

    def test_cloudflare_challenge_titles(self):
        assert _is_cloudflare_challenge("Security Check - Indeed.com") is True
        assert _is_cloudflare_challenge("Just a moment...") is True
        assert _is_cloudflare_challenge("Sachbearbeitung Finanzbuchhaltung - Zürich, ZH - Indeed.com") is False


class TestStripCodeFences:
    def test_json_fence(self):
        text = '```json\n{"key": "value"}\n```'
        assert _strip_code_fences(text) == '{"key": "value"}'

    def test_plain_fence(self):
        text = '```\n{"key": "value"}\n```'
        assert _strip_code_fences(text) == '{"key": "value"}'

    def test_no_fence(self):
        text = '{"key": "value"}'
        assert _strip_code_fences(text) == '{"key": "value"}'

    def test_whitespace_around(self):
        text = '  ```json\n{"a": 1}\n```  '
        assert _strip_code_fences(text) == '{"a": 1}'
