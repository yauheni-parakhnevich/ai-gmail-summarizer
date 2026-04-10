#!/usr/bin/env python3
"""Standalone tool: mark Gmail messages as read via Gmail API.

Usage:
    uv run python tools/gmail_mark_read.py --profile <name> --ids <id1>,<id2>,...

Requires OAuth credentials (credentials.json / token.json) in the profile directory.
"""

import argparse
import sys
from pathlib import Path

from google.auth.exceptions import RefreshError
from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build

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


def mark_as_read(service, email_ids: list[str]) -> int:
    count = 0
    for email_id in email_ids:
        try:
            service.users().messages().modify(
                userId="me",
                id=email_id,
                body={"removeLabelIds": ["UNREAD"]},
            ).execute()
            count += 1
        except Exception as e:
            print(f"Warning: failed to mark {email_id} as read: {e}", file=sys.stderr)
    return count


def main():
    parser = argparse.ArgumentParser(description="Mark Gmail messages as read")
    parser.add_argument("--profile", required=True, help="Profile name (directory under profiles/)")
    parser.add_argument("--ids", required=True, help="Comma-separated message IDs to mark as read")
    args = parser.parse_args()

    ids = [i.strip() for i in args.ids.split(",") if i.strip()]
    if not ids:
        print("Error: no message IDs provided", file=sys.stderr)
        sys.exit(1)

    service = get_service(args.profile)
    count = mark_as_read(service, ids)
    print(f"Marked {count}/{len(ids)} messages as read")


if __name__ == "__main__":
    main()
