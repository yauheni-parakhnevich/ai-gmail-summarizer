"""Gmail API for reading emails, and SMTP for sending reports."""

import base64
import smtplib
from dataclasses import dataclass
from email.mime.text import MIMEText

from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import Resource, build

from gmail_summarizer.config import Config

SCOPES = ["https://www.googleapis.com/auth/gmail.modify"]


@dataclass
class Email:
    id: str
    subject: str
    sender: str
    date: str
    body_text: str
    body_html: str


def authenticate(config: Config) -> Resource:
    """Run OAuth 2.0 flow and return an authorized Gmail API service."""
    creds = None

    if config.token_path.exists():
        creds = Credentials.from_authorized_user_file(str(config.token_path), SCOPES)

    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            creds.refresh(Request())
        else:
            flow = InstalledAppFlow.from_client_secrets_file(str(config.credentials_path), SCOPES)
            creds = flow.run_local_server(port=0)

        config.token_path.parent.mkdir(parents=True, exist_ok=True)
        config.token_path.write_text(creds.to_json())

    return build("gmail", "v1", credentials=creds)


def fetch_unread_emails(service: Resource) -> list[Email]:
    """Fetch all unread emails from the inbox."""
    results = service.users().messages().list(userId="me", q="is:unread category:primary", maxResults=100).execute()

    messages = results.get("messages", [])
    emails: list[Email] = []

    for msg_ref in messages:
        msg = service.users().messages().get(userId="me", id=msg_ref["id"], format="full").execute()
        emails.append(_parse_message(msg))

    return emails


def mark_as_read(service: Resource, email_ids: list[str]) -> None:
    """Mark emails as read by removing the UNREAD label."""
    for email_id in email_ids:
        service.users().messages().modify(
            userId="me",
            id=email_id,
            body={"removeLabelIds": ["UNREAD"]},
        ).execute()


def send_email(config: Config, subject: str, html_body: str) -> None:
    """Send an email via SMTP. Tries SSL first, falls back to STARTTLS."""
    message = MIMEText(html_body, "html")
    message["Subject"] = subject
    message["From"] = config.sender_email
    message["To"] = config.recipient_email

    try:
        with smtplib.SMTP_SSL(config.smtp_host, config.smtp_port) as server:
            server.login(config.sender_email, config.smtp_password)
            server.send_message(message)
    except OSError:
        # SSL failed — try STARTTLS (port 587)
        with smtplib.SMTP(config.smtp_host, 587) as server:
            server.starttls()
            server.login(config.sender_email, config.smtp_password)
            server.send_message(message)


def _parse_message(msg: dict) -> Email:
    """Parse a Gmail API message into an Email dataclass."""
    headers = {h["name"].lower(): h["value"] for h in msg["payload"]["headers"]}

    _extract_body(msg["payload"], body_parts := {"text": "", "html": ""})

    return Email(
        id=msg["id"],
        subject=headers.get("subject", "(no subject)"),
        sender=headers.get("from", ""),
        date=headers.get("date", ""),
        body_text=body_parts["text"],
        body_html=body_parts["html"],
    )


def _extract_body(payload: dict, parts: dict) -> None:
    """Recursively extract text and HTML body from a message payload."""
    mime_type = payload.get("mimeType", "")

    if "parts" in payload:
        for part in payload["parts"]:
            _extract_body(part, parts)
    elif "body" in payload and payload["body"].get("data"):
        decoded = base64.urlsafe_b64decode(payload["body"]["data"]).decode("utf-8", errors="replace")
        if mime_type == "text/plain":
            parts["text"] += decoded
        elif mime_type == "text/html":
            parts["html"] += decoded
