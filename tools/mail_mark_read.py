#!/usr/bin/env python3
"""Standalone tool: mark emails as read across all accounts of a profile.

Usage:
    uv run python tools/mail_mark_read.py --profile <name> --ids <id1>,<id2>,...

Takes the prefixed ids produced by tools/mail_fetch.py:
    gmail:<msgid>          -> Gmail API removeLabelIds UNREAD
    <imap-email>:<uid>     -> IMAP UID STORE +FLAGS (\\Seen)
"""

import argparse
import imaplib
import sys
from pathlib import Path

import yaml
from gmail_fetch import get_service


def load_accounts(profile: str) -> list[dict]:
    config_path = Path("profiles") / profile / "config.yaml"
    if not config_path.exists():
        print(f"Error: config.yaml not found at {config_path}", file=sys.stderr)
        sys.exit(1)
    config = yaml.safe_load(config_path.read_text()) or {}
    return config.get("accounts") or [{"type": "gmail"}]


def mark_gmail_read(profile: str, ids: list[str]) -> int:
    service = get_service(profile)
    count = 0
    for email_id in ids:
        try:
            service.users().messages().modify(
                userId="me",
                id=email_id,
                body={"removeLabelIds": ["UNREAD"]},
            ).execute()
            count += 1
        except Exception as e:
            print(f"Warning: failed to mark gmail:{email_id} as read: {e}", file=sys.stderr)
    return count


def mark_imap_read(account: dict, uids: list[str]) -> int:
    with imaplib.IMAP4_SSL(account["host"], 993) as conn:
        conn.login(account["email"], account["password"])
        conn.select("INBOX")
        count = 0
        for uid in uids:
            status, _ = conn.uid("store", uid, "+FLAGS", "(\\Seen)")
            if status == "OK":
                count += 1
            else:
                print(f"Warning: failed to mark {account['email']}:{uid} as read: {status}", file=sys.stderr)
        return count


def main():
    parser = argparse.ArgumentParser(description="Mark emails as read across profile accounts")
    parser.add_argument("--profile", required=True, help="Profile name (directory under profiles/)")
    parser.add_argument("--ids", required=True, help="Comma-separated prefixed ids from mail_fetch.py")
    args = parser.parse_args()

    by_account: dict[str, list[str]] = {}
    for raw in args.ids.split(","):
        raw = raw.strip()
        if not raw:
            continue
        if ":" not in raw:
            print(f"Warning: id '{raw}' has no account prefix, assuming gmail", file=sys.stderr)
            by_account.setdefault("gmail", []).append(raw)
            continue
        prefix, rest = raw.split(":", 1)
        by_account.setdefault(prefix, []).append(rest)

    if not by_account:
        print("Error: no message IDs provided", file=sys.stderr)
        sys.exit(1)

    imap_accounts = {a["email"]: a for a in load_accounts(args.profile) if a.get("type") == "imap"}

    total = marked = 0
    for prefix, ids in by_account.items():
        total += len(ids)
        if prefix == "gmail":
            marked += mark_gmail_read(args.profile, ids)
        elif prefix in imap_accounts:
            try:
                marked += mark_imap_read(imap_accounts[prefix], ids)
            except Exception as e:
                print(f"Warning: IMAP mark-read for '{prefix}' failed: {e}", file=sys.stderr)
        else:
            print(f"Warning: no account '{prefix}' in profile config, skipping {len(ids)} ids", file=sys.stderr)

    print(f"Marked {marked}/{total} messages as read")


if __name__ == "__main__":
    main()
