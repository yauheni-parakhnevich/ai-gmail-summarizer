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

    # local_hostname is passed explicitly: without it smtplib calls socket.getfqdn()
    # after the greeting, which can take 5-10 s (slow reverse DNS on macOS) and trips
    # Migadu's ~10 s idle timeout ("421 4.4.2 Idle timeout"), breaking EHLO/AUTH.
    local_hostname = sender.split("@", 1)[-1] or "localhost"
    timeout = 60

    try:
        with smtplib.SMTP_SSL(host, port, local_hostname=local_hostname, timeout=timeout) as server:
            server.login(sender, password)
            server.send_message(message)
    except OSError:
        with smtplib.SMTP(host, 587, local_hostname=local_hostname, timeout=timeout) as server:
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
