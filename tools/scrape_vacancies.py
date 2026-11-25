#!/usr/bin/env python3
"""Standalone tool: scrape a batch of vacancy URLs using the project's scraper.

Reuses gmail_summarizer.scraper.scrape_vacancy so it transparently handles
jobs.ch (requests), LinkedIn/Xing (Playwright with session reuse), and redirects.
Decodes HTML entities (e.g. &amp;) in URLs before scraping.

Usage:
    # Read URLs from a file (one per line)
    uv run python tools/scrape_vacancies.py --profile <name> --urls-file urls.txt

    # Read URLs from stdin
    echo "https://..." | uv run python tools/scrape_vacancies.py --profile <name> --urls-file -

    # Read URLs from JSON file with a key
    uv run python tools/scrape_vacancies.py --profile <name> --json-file links.json --json-key linkedin

Outputs JSON array to stdout (or --output PATH). Each result:
    {url, ok, title?, company?, location?, description?, requirements?, salary?, error?}
"""

import argparse
import html
import json
import sys
import traceback
from pathlib import Path

from gmail_summarizer.config import load_config
from gmail_summarizer.scraper import BrowserSession


def load_urls(args) -> list[str]:
    urls: list[str] = []
    if args.urls_file:
        text = sys.stdin.read() if args.urls_file == "-" else Path(args.urls_file).read_text(encoding="utf-8")
        urls.extend(line.strip() for line in text.splitlines() if line.strip())
    if args.json_file:
        data = json.loads(Path(args.json_file).read_text(encoding="utf-8"))
        if args.json_key:
            data = data[args.json_key]
        if not isinstance(data, list):
            print(f"Error: JSON at key '{args.json_key}' is not a list", file=sys.stderr)
            sys.exit(2)
        urls.extend(str(u) for u in data)
    if args.url:
        urls.extend(args.url)
    if not urls:
        print("Error: no URLs provided (use --urls-file, --json-file, or --url)", file=sys.stderr)
        sys.exit(2)
    return [html.unescape(u) for u in urls]


def main():
    parser = argparse.ArgumentParser(description="Scrape vacancy URLs into structured JSON")
    parser.add_argument("--profile", required=True, help="Profile name (directory under profiles/)")
    parser.add_argument("--urls-file", help="Path to a file with one URL per line (use '-' for stdin)")
    parser.add_argument("--json-file", help="Path to a JSON file containing a list of URLs")
    parser.add_argument("--json-key", help="Key inside --json-file holding the list (omit for top-level list)")
    parser.add_argument("--url", action="append", help="Single URL (may be repeated)")
    parser.add_argument("--output", help="Write JSON to this path instead of stdout")
    parser.add_argument("--progress", action="store_true", help="Print per-URL progress to stderr")
    parser.add_argument(
        "--raw",
        action="store_true",
        help="Skip Claude extraction; output {url, ok, page_text, error?} for downstream parsing",
    )
    args = parser.parse_args()

    urls = load_urls(args)
    config = load_config(args.profile)
    results: list[dict] = []
    total = len(urls)

    # One shared browser session across the whole batch: a single warm,
    # logged-in LinkedIn/Xing context is reused for every URL instead of
    # cold-launching a browser per URL (faster + avoids throttle/guest walls).
    with BrowserSession(config) as session:
        for i, url in enumerate(urls, 1):
            if args.progress:
                print(f"[{i}/{total}] {url[:100]}", file=sys.stderr, flush=True)
            try:
                if args.raw:
                    resolved_url, page_text = session.fetch_page_text(url)
                    if not page_text:
                        results.append({"url": url, "ok": False, "error": "fetch returned no text"})
                        if args.progress:
                            print("  -> FAILED (no text)", file=sys.stderr, flush=True)
                        continue
                    results.append({"url": resolved_url, "ok": True, "page_text": page_text})
                    if args.progress:
                        print(f"  -> OK ({len(page_text)} chars)", file=sys.stderr, flush=True)
                    continue

                v = session.scrape_vacancy(url)
                if v is None:
                    results.append({"url": url, "ok": False, "error": "scrape returned None"})
                    if args.progress:
                        print("  -> FAILED (None)", file=sys.stderr, flush=True)
                    continue
                results.append(
                    {
                        "url": url,
                        "ok": True,
                        "title": v.title,
                        "company": v.company,
                        "location": v.location,
                        "description": v.description,
                        "requirements": v.requirements,
                        "salary": v.salary,
                    }
                )
                if args.progress:
                    print(f"  -> {v.title} @ {v.company}", file=sys.stderr, flush=True)
            except Exception as e:
                results.append({"url": url, "ok": False, "error": str(e)})
                if args.progress:
                    print(f"  -> EXC: {e}", file=sys.stderr, flush=True)
                    traceback.print_exc(file=sys.stderr)

    payload = json.dumps(results, ensure_ascii=False, indent=2)
    if args.output:
        Path(args.output).write_text(payload, encoding="utf-8")
        ok = sum(1 for r in results if r["ok"])
        print(f"Scraped {ok}/{total} -> {args.output}", file=sys.stderr)
    else:
        print(payload)


if __name__ == "__main__":
    main()
