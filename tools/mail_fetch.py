#!/usr/bin/env python3
"""Standalone tool: fetch unread emails from all accounts configured for a profile.

Usage:
    uv run python tools/mail_fetch.py --profile <name>

Reads the optional `accounts:` list from profiles/<name>/config.yaml:

    accounts:
      - type: gmail          # uses profiles/<name>/credentials.json + token.json
      - type: imap
        host: imap.migadu.com
        email: user@example.com
        password: "..."

Without an `accounts:` key the profile is treated as a single gmail account
(backward compatible with tools/gmail_fetch.py).

Outputs one merged JSON array to stdout. Each email has the usual fields
(subject, sender, date, body_text, body_html) plus `account`, and `id` is
prefixed with the account key: `gmail:<msgid>` or `<imap-email>:<uid>`.
IMAP fetching uses BODY.PEEK[] so messages stay unread until mail_mark_read.py.
"""

import argparse
import email
import email.header
import imaplib
import json
import sys
from pathlib import Path

import yaml
from gmail_fetch import fetch_unread_emails, get_service


def load_accounts(profile: str) -> list[dict]:
    config_path = Path("profiles") / profile / "config.yaml"
    if not config_path.exists():
        print(f"Error: config.yaml not found at {config_path}", file=sys.stderr)
        sys.exit(1)
    config = yaml.safe_load(config_path.read_text()) or {}
    return config.get("accounts") or [{"type": "gmail"}]


def _decode_header(value: str | None) -> str:
    if not value:
        return ""
    parts = []
    for chunk, charset in email.header.decode_header(value):
        if isinstance(chunk, bytes):
            parts.append(chunk.decode(charset or "utf-8", errors="replace"))
        else:
            parts.append(chunk)
    return "".join(parts)


def _extract_imap_body(msg: email.message.Message) -> tuple[str, str]:
    text, html = "", ""
    for part in msg.walk():
        if part.get_content_maintype() == "multipart" or part.get_content_disposition() == "attachment":
            continue
        payload = part.get_payload(decode=True)
        if payload is None:
            continue
        decoded = payload.decode(part.get_content_charset() or "utf-8", errors="replace")
        if part.get_content_type() == "text/plain":
            text += decoded
        elif part.get_content_type() == "text/html":
            html += decoded
    return text, html


def fetch_imap_unread(account: dict) -> list[dict]:
    host = account["host"]
    address = account["email"]
    with imaplib.IMAP4_SSL(host, 993) as conn:
        conn.login(address, account["password"])
        conn.select("INBOX")
        status, data = conn.uid("search", None, "UNSEEN")
        if status != "OK":
            raise RuntimeError(f"IMAP search failed: {status}")
        uids = data[0].split()

        emails = []
        for uid in uids:
            status, msg_data = conn.uid("fetch", uid, "(BODY.PEEK[])")
            if status != "OK" or not msg_data or msg_data[0] is None:
                print(f"Warning: failed to fetch IMAP uid {uid.decode()} from {address}", file=sys.stderr)
                continue
            msg = email.message_from_bytes(msg_data[0][1])
            body_text, body_html = _extract_imap_body(msg)
            emails.append(
                {
                    "id": f"{address}:{uid.decode()}",
                    "subject": _decode_header(msg["Subject"]) or "(no subject)",
                    "sender": _decode_header(msg["From"]),
                    "date": msg["Date"] or "",
                    "body_text": body_text,
                    "body_html": body_html,
                    "account": address,
                }
            )
        return emails


def fetch_gmail_unread(profile: str) -> list[dict]:
    service = get_service(profile)
    emails = fetch_unread_emails(service)
    for e in emails:
        e["id"] = f"gmail:{e['id']}"
        e["account"] = "gmail"
    return emails


def main():
    parser = argparse.ArgumentParser(description="Fetch unread emails from all accounts of a profile")
    parser.add_argument("--profile", required=True, help="Profile name (directory under profiles/)")
    args = parser.parse_args()

    all_emails = []
    for account in load_accounts(args.profile):
        kind = account.get("type", "gmail")
        try:
            if kind == "gmail":
                all_emails.extend(fetch_gmail_unread(args.profile))
            elif kind == "imap":
                all_emails.extend(fetch_imap_unread(account))
            else:
                print(f"Warning: unknown account type '{kind}', skipping", file=sys.stderr)
        except Exception as e:
            label = account.get("email", kind)
            print(f"Warning: fetching account '{label}' failed: {e}", file=sys.stderr)

    print(json.dumps(all_emails, ensure_ascii=False))


if __name__ == "__main__":
    main()
