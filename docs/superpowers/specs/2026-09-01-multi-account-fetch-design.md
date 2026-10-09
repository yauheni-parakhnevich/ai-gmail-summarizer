# Multi-Account Email Fetch — Design

**Date:** 2026-09-01
**Status:** Approved (user confirmed config shape + provided Migadu credentials)

## Goal

The `summarize-gmail` command currently reads one Gmail account per profile. The `alena` profile
needs a second mailbox — `user@example.com` on Migadu (IMAP, no Gmail API) — fetched and
marked-read in the same pipeline run.

## Config

`profiles/<name>/config.yaml` gains an optional `accounts:` list:

```yaml
accounts:
  - type: gmail          # uses profiles/<name>/credentials.json + token.json
  - type: imap
    host: imap.migadu.com
    email: user@example.com
    password: "..."
```

- No `accounts:` key → implicit `[{type: gmail}]`. Existing profiles (eugene, example) unchanged.
- IMAP entries: `host` (port 993, SSL implied), `email` (also the login), `password`.
  Plaintext password matches the existing linkedin/xing credential convention; `profiles/*/` is
  gitignored.

## Tools

Two new dispatch wrappers so the command keeps single-call steps regardless of account count:

- **`tools/mail_fetch.py --profile <name>`** — iterates accounts:
  - `gmail`: reuses `get_service` / `fetch_unread_emails` from `tools/gmail_fetch.py`.
  - `imap`: `imaplib.IMAP4_SSL(host, 993)`, `SEARCH UNSEEN` in INBOX, fetch via `BODY.PEEK[]`
    so fetching does not set `\Seen` (read status only changes in the mark-read step).
  - Output: one merged JSON array, same fields as before (`subject`, `sender`, `date`,
    `body_text`, `body_html`) plus `account`, with `id` prefixed by account key:
    `gmail:<msgid>` / `<imap-email>:<uid>`.
  - One account failing (auth, network) prints a warning to stderr and the run continues with
    the other accounts.
- **`tools/mail_mark_read.py --profile <name> --ids <prefixed,...>`** — splits each id on the
  first `:`; `gmail` prefix → Gmail API `removeLabelIds: [UNREAD]`, otherwise prefix is the IMAP
  account email → `UID STORE +FLAGS (\Seen)`.

`tools/gmail_fetch.py` / `tools/gmail_mark_read.py` remain for manual single-account use.

## Command changes

`.claude/commands/summarize-gmail.md`:

- Step 0: mention optional `accounts:` list.
- Step 1: call `tools/mail_fetch.py` instead of `tools/gmail_fetch.py`; note `account` field and
  prefixed ids.
- Step 8: call `tools/mail_mark_read.py` with the prefixed ids as-is.
- All other steps unchanged. The `src/` pipeline (`uv run gmail-summarizer`) is out of scope.

## Testing

Live verification: run `mail_fetch.py --profile alena` (read-only thanks to PEEK), confirm both
accounts return; ruff clean on new tools.
