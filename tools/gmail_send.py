#!/usr/bin/env python3
"""Standalone tool: send an HTML email via SMTP (using .env settings).

Usage:
    uv run python tools/gmail_send.py --to <email> --subject <subject> --html-file <path>
    uv run python tools/gmail_send.py --to <email> --subject <subject> --html '<html>...'

Reads SENDER_EMAIL, SMTP_HOST, SMTP_PORT, SMTP_PASSWORD from .env.
"""

import argparse
import os
import smtplib
import sys
from email.mime.text import MIMEText
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()


def send_email(to: str, subject: str, html_body: str) -> None:
    sender = os.getenv("SENDER_EMAIL", "")
    host = os.getenv("SMTP_HOST", "smtp.migadu.com")
    port = int(os.getenv("SMTP_PORT", "465"))
    password = os.getenv("SMTP_PASSWORD", "")

    if not sender or not password:
        print("Error: SENDER_EMAIL and SMTP_PASSWORD must be set in .env", file=sys.stderr)
        sys.exit(1)

    message = MIMEText(html_body, "html")
    message["Subject"] = subject
    message["From"] = sender
    message["To"] = to

    try:
        with smtplib.SMTP_SSL(host, port) as server:
            server.login(sender, password)
            server.send_message(message)
    except OSError:
        with smtplib.SMTP(host, 587) as server:
            server.starttls()
            server.login(sender, password)
            server.send_message(message)


def main():
    parser = argparse.ArgumentParser(description="Send an HTML email via SMTP")
    parser.add_argument("--to", required=True, help="Recipient email address")
    parser.add_argument("--subject", required=True, help="Email subject")
    parser.add_argument("--html-file", help="Path to HTML file to send as body")
    parser.add_argument("--html", help="Inline HTML string to send as body")
    args = parser.parse_args()

    if args.html_file:
        html_body = Path(args.html_file).read_text()
    elif args.html:
        html_body = args.html
    else:
        print("Error: provide either --html-file or --html", file=sys.stderr)
        sys.exit(1)

    send_email(args.to, args.subject, html_body)
    print(f"Sent from {os.getenv('SENDER_EMAIL')} to {args.to}")


if __name__ == "__main__":
    main()
