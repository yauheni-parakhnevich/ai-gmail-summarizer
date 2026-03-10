from gmail_summarizer.scraper import _needs_browser, _strip_code_fences


class TestNeedsBrowser:
    def test_linkedin(self):
        assert _needs_browser("https://www.linkedin.com/jobs/view/123") is True

    def test_xing(self):
        assert _needs_browser("https://www.xing.com/jobs/detail/123") is True

    def test_regular_site(self):
        assert _needs_browser("https://jobs.example.com/position/42") is False

    def test_subdomain_linkedin(self):
        assert _needs_browser("https://ch.linkedin.com/jobs/view/1") is True


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
