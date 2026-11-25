#!/usr/bin/env python3
"""Standalone tool: fetch unread primary Gmail emails via Gmail API.

Usage:
    uv run python tools/gmail_fetch.py --profile <name>

Requires OAuth credentials (credentials.json / token.json) in the profile directory.
Outputs JSON array of emails to stdout.
"""

import argparse
import json
import sys
from pathlib import Path

from google.auth.exceptions import RefreshError
from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build

from gmail_summarizer.gmail import _parse_message

SCOPES = ["https://www.googleapis.com/auth/gmail.modify"]


def get_service(profile: str):
    profile_dir = Path("profiles") / profile
    credentials_path = profile_dir / "credentials.json"
    token_path = profile_dir / "token.json"

    if not credentials_path.exists():
        print(f"Error: credentials.json not found at {credentials_path}", file=sys.stderr)
        sys.exit(1)

    creds = None
    if token_path.exists():
        creds = Credentials.from_authorized_user_file(str(token_path), SCOPES)

    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            try:
                creds.refresh(Request())
            except RefreshError:
                print("Token expired, re-authenticating...", file=sys.stderr)
                token_path.unlink(missing_ok=True)
                creds = None
        if not creds or not creds.valid:
            flow = InstalledAppFlow.from_client_secrets_file(str(credentials_path), SCOPES)
            creds = flow.run_local_server(port=0)
        token_path.write_text(creds.to_json())

    return build("gmail", "v1", credentials=creds)


def fetch_unread_emails(service) -> list[dict]:
    results = service.users().messages().list(userId="me", q="is:unread category:primary", maxResults=100).execute()
    messages = results.get("messages", [])
    emails = []

    for msg_ref in messages:
        msg = service.users().messages().get(userId="me", id=msg_ref["id"], format="full").execute()
        email = _parse_message(msg)
        emails.append({
            "id": email.id,
            "subject": email.subject,
            "sender": email.sender,
            "date": email.date,
            "body_text": email.body_text,
            "body_html": email.body_html,
        })

    return emails


def main():
    parser = argparse.ArgumentParser(description="Fetch unread primary Gmail emails")
    parser.add_argument("--profile", required=True, help="Profile name (directory under profiles/)")
    args = parser.parse_args()

    service = get_service(args.profile)
    emails = fetch_unread_emails(service)
    print(json.dumps(emails, ensure_ascii=False))


if __name__ == "__main__":
    main()
