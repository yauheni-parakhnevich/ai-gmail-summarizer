import base64

from gmail_summarizer.gmail import _extract_body, _parse_message


def _make_message(body_text="Hello", body_html="<p>Hello</p>", subject="Test", sender="a@b.com"):
    """Build a Gmail API message dict."""
    text_data = base64.urlsafe_b64encode(body_text.encode()).decode()
    html_data = base64.urlsafe_b64encode(body_html.encode()).decode()
    return {
        "id": "msg123",
        "payload": {
            "mimeType": "multipart/alternative",
            "headers": [
                {"name": "Subject", "value": subject},
                {"name": "From", "value": sender},
                {"name": "Date", "value": "Mon, 1 Jan 2024 10:00:00 +0000"},
            ],
            "parts": [
                {
                    "mimeType": "text/plain",
                    "body": {"data": text_data},
                },
                {
                    "mimeType": "text/html",
                    "body": {"data": html_data},
                },
            ],
        },
    }


class TestParseMessage:
    def test_basic_fields(self):
        email = _parse_message(_make_message(subject="Job Alert", sender="jobs@x.com"))
        assert email.id == "msg123"
        assert email.subject == "Job Alert"
        assert email.sender == "jobs@x.com"

    def test_body_extraction(self):
        email = _parse_message(_make_message(body_text="Plain text", body_html="<b>HTML</b>"))
        assert email.body_text == "Plain text"
        assert email.body_html == "<b>HTML</b>"

    def test_missing_subject(self):
        msg = _make_message()
        msg["payload"]["headers"] = [{"name": "From", "value": "a@b.com"}, {"name": "Date", "value": "now"}]
        email = _parse_message(msg)
        assert email.subject == "(no subject)"


class TestExtractBody:
    def test_simple_text(self):
        data = base64.urlsafe_b64encode(b"hello").decode()
        payload = {"mimeType": "text/plain", "body": {"data": data}}
        parts = {"text": "", "html": ""}
        _extract_body(payload, parts)
        assert parts["text"] == "hello"
        assert parts["html"] == ""

    def test_nested_multipart(self):
        text_data = base64.urlsafe_b64encode(b"text").decode()
        html_data = base64.urlsafe_b64encode(b"<p>html</p>").decode()
        payload = {
            "mimeType": "multipart/mixed",
            "parts": [
                {
                    "mimeType": "multipart/alternative",
                    "parts": [
                        {"mimeType": "text/plain", "body": {"data": text_data}},
                        {"mimeType": "text/html", "body": {"data": html_data}},
                    ],
                }
            ],
        }
        parts = {"text": "", "html": ""}
        _extract_body(payload, parts)
        assert parts["text"] == "text"
        assert parts["html"] == "<p>html</p>"

    def test_empty_body(self):
        payload = {"mimeType": "text/plain", "body": {}}
        parts = {"text": "", "html": ""}
        _extract_body(payload, parts)
        assert parts["text"] == ""
