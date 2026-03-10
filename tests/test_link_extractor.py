from gmail_summarizer.gmail import Email
from gmail_summarizer.link_extractor import _clean_url, _dedup_key, _gather_links_with_context, _is_http_url


class TestCleanUrl:
    def test_strips_utm_params(self):
        url = "https://example.com/job?utm_source=email&utm_medium=cpc&id=42"
        assert _clean_url(url) == "https://example.com/job?id=42"

    def test_strips_all_tracking_params(self):
        url = "https://example.com/job?utm_source=x&utm_medium=y&utm_campaign=z"
        cleaned = _clean_url(url)
        assert "utm_" not in cleaned
        assert cleaned == "https://example.com/job"

    def test_preserves_non_tracking_params(self):
        url = "https://example.com/job?id=42&ref=search"
        assert _clean_url(url) == url

    def test_no_query_unchanged(self):
        url = "https://example.com/job/123"
        assert _clean_url(url) == url


class TestIsHttpUrl:
    def test_valid_http(self):
        assert _is_http_url("http://example.com") is True

    def test_valid_https(self):
        assert _is_http_url("https://example.com/path") is True

    def test_mailto(self):
        assert _is_http_url("mailto:user@example.com") is False

    def test_empty(self):
        assert _is_http_url("") is False

    def test_no_scheme(self):
        assert _is_http_url("example.com") is False


class TestDedupKey:
    def test_ignores_query_params(self):
        assert _dedup_key("https://example.com/job?id=1") == _dedup_key("https://example.com/job?id=2")

    def test_different_paths_differ(self):
        assert _dedup_key("https://example.com/a") != _dedup_key("https://example.com/b")


class TestGatherLinksWithContext:
    def _email(self, body_html="", body_text=""):
        return Email(
            id="1",
            subject="Jobs",
            sender="bot@x.com",
            date="2024-01-01",
            body_text=body_text,
            body_html=body_html,
        )

    def test_extracts_from_html(self):
        html = '<a href="https://example.com/job/1">Apply</a>'
        links = _gather_links_with_context(self._email(body_html=html))
        assert len(links) == 1
        assert links[0][0].startswith("https://example.com/job/1")
        assert links[0][1] == "Apply"

    def test_extracts_from_text(self):
        links = _gather_links_with_context(self._email(body_text="Check https://example.com/job/2 now"))
        assert len(links) == 1
        assert "example.com/job/2" in links[0][0]

    def test_deduplicates(self):
        html = '<a href="https://example.com/job/1?a=1">A</a><a href="https://example.com/job/1?b=2">B</a>'
        links = _gather_links_with_context(self._email(body_html=html))
        assert len(links) == 1

    def test_skips_non_http(self):
        html = '<a href="mailto:a@b.com">Mail</a><a href="https://example.com">Link</a>'
        links = _gather_links_with_context(self._email(body_html=html))
        assert len(links) == 1

    def test_empty_email(self):
        links = _gather_links_with_context(self._email())
        assert links == []
