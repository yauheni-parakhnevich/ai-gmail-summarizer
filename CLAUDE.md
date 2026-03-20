# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

AI-powered Gmail summarizer that reads unread emails, classifies job-related ones using Claude, scrapes vacancy pages, matches them against a user's CV, and emails an HTML report ranked by fit percentage.

## Commands

```bash
# Install dependencies (uses uv with Python 3.12)
uv sync --group dev

# Install Playwright browsers (needed for LinkedIn/Xing scraping)
uv run playwright install chromium

# Run the tool
uv run gmail-summarizer --profile <name>       # full pipeline
uv run gmail-summarizer --profile <name> --resume  # resume from saved state

# Lint
uv run ruff check src/ tests/
uv run ruff format --check src/ tests/

# Tests
uv run pytest                    # all tests
uv run pytest tests/test_foo.py  # single file
uv run pytest -k "test_name"    # single test by name
```

## Architecture

The pipeline runs sequentially through these stages, with state saved after each so `--resume` can skip completed stages:

1. **gmail.py** — OAuth 2.0 authentication, fetch unread primary emails, mark as read, send report via SMTP
2. **classifier.py** — Claude classifies emails as job-related or not (batch, returns email IDs)
3. **link_extractor.py** — Extracts URLs from email HTML/text, Claude identifies which are vacancy links (vs unsubscribe, homepage, etc.)
4. **scraper.py** — Fetches vacancy pages: `requests` for simple sites, Playwright for JS-rendered sites (LinkedIn, Xing) with auto-login and session persistence via `credentials/*_state.json`. Claude extracts structured job info from page text.
5. **matcher.py** — Claude compares each vacancy against the CV, returns fit percentage + summary + matches/gaps
6. **report.py** — Generates HTML email with color-coded fit scores (green ≥70%, yellow ≥50%, red <50%)

**config.py** — Loads shared settings from `.env` and per-profile settings from `profiles/<name>/config.yaml`.

## Multi-Profile System

Each profile is a directory under `profiles/` containing:
- `config.yaml` — recipient email, min fit %, LinkedIn/Xing credentials
- `cv.md` — the candidate's CV in markdown
- `matcher_instructions.md` — (optional) custom rules for the matcher: which vacancies to rank higher/lower/skip
- `credentials.json` — Gmail OAuth client credentials
- `token.json` — auto-generated OAuth token (gitignored)
- `state.json` — auto-generated resume state (deleted on success)

See `profiles/example/` for the template.

## Key Patterns

- All Claude API calls use `config.claude_model` (defaults to `claude-sonnet-4-20250514`) and return JSON parsed after stripping markdown code fences (`_strip_code_fences` helper duplicated across modules)
- Scraper uses a two-tier fetch strategy: `requests` first, falls back to Playwright if content is empty/JS-rendered
- `_BROWSER_DOMAINS` and `_REDIRECT_DOMAINS` sets in scraper.py control which sites need browser/redirect resolution
- Ruff config: line-length 120, rules E/F/I/UP/B/SIM
