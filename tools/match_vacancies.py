#!/usr/bin/env python3
"""Standalone tool: match scraped vacancies against a profile's CV.

Reads the JSON produced by tools/scrape_vacancies.py (a list of
{url, ok, title, company, location, description, requirements, salary, ...}),
calls gmail_summarizer.matcher for each entry where ok=true, and writes a
JSON list sorted by fit_percentage descending.

Usage:
    uv run python tools/match_vacancies.py \\
        --profile <name> \\
        --scraped-file /tmp/<name>_scraped.json \\
        --output /tmp/<name>_matches.json

Output JSON entries:
    {url, title, company, location, fit_percentage, summary, key_matches, gaps, note?}

Failures are reported on stderr and excluded from the output. Use --include-fails
to keep them with fit_percentage=null and an error field.

You may also append extra vacancies that were not scraped (e.g. WebFetch results)
via --extra-file PATH (same shape as a scraped-file entry with ok=true).
"""

import argparse
import json
import sys
from pathlib import Path

from gmail_summarizer.config import load_config
from gmail_summarizer.matcher import match_vacancy
from gmail_summarizer.scraper import VacancyInfo


def to_vacancy(entry: dict) -> VacancyInfo:
    return VacancyInfo(
        url=entry["url"],
        title=entry.get("title", ""),
        company=entry.get("company", ""),
        location=entry.get("location", ""),
        description=entry.get("description", ""),
        requirements=entry.get("requirements") or [],
        salary=entry.get("salary"),
    )


def load_entries(args) -> list[dict]:
    entries: list[dict] = []
    if args.scraped_file:
        entries.extend(json.loads(Path(args.scraped_file).read_text(encoding="utf-8")))
    if args.extra_file:
        entries.extend(json.loads(Path(args.extra_file).read_text(encoding="utf-8")))
    if not entries:
        print("Error: no input (use --scraped-file and/or --extra-file)", file=sys.stderr)
        sys.exit(2)
    return entries


def main():
    parser = argparse.ArgumentParser(description="Match scraped vacancies against a profile's CV")
    parser.add_argument("--profile", required=True, help="Profile name (directory under profiles/)")
    parser.add_argument("--scraped-file", help="JSON file from tools/scrape_vacancies.py")
    parser.add_argument("--extra-file", help="Additional JSON list of scraped-shape entries to merge in")
    parser.add_argument("--output", help="Write JSON to this path instead of stdout")
    parser.add_argument("--include-fails", action="store_true",
                        help="Include entries with ok=false in the output (fit_percentage=null)")
    parser.add_argument("--progress", action="store_true", help="Print per-entry progress to stderr")
    args = parser.parse_args()

    entries = load_entries(args)
    config = load_config(args.profile)
    cv_text = config.cv_path.read_text(encoding="utf-8")

    results: list[dict] = []
    for i, e in enumerate(entries, 1):
        url = e.get("url", "")
        if not e.get("ok", True):
            msg = f"[{i}/{len(entries)}] [skip-fail] {url[:80]} -> {e.get('error')}"
            if args.progress:
                print(msg, file=sys.stderr, flush=True)
            if args.include_fails:
                results.append({"url": url, "title": e.get("title"), "company": e.get("company"),
                                "location": e.get("location"), "fit_percentage": None,
                                "summary": None, "key_matches": [], "gaps": [], "error": e.get("error")})
            continue

        v = to_vacancy(e)
        try:
            m = match_vacancy(config, v, cv_text)
            if args.progress:
                print(f"[{i}/{len(entries)}] [{m.fit_percentage:3d}%] {v.title} @ {v.company}",
                      file=sys.stderr, flush=True)
            entry_out = {
                "url": v.url, "title": v.title, "company": v.company, "location": v.location,
                "fit_percentage": m.fit_percentage, "summary": m.summary,
                "key_matches": m.key_matches, "gaps": m.gaps,
            }
            if "note" in e:
                entry_out["note"] = e["note"]
            results.append(entry_out)
        except Exception as ex:
            if args.progress:
                print(f"[{i}/{len(entries)}] [match-err] {v.title}: {ex}", file=sys.stderr, flush=True)
            if args.include_fails:
                results.append({"url": v.url, "title": v.title, "company": v.company,
                                "location": v.location, "fit_percentage": None,
                                "summary": None, "key_matches": [], "gaps": [], "error": str(ex)})

    results.sort(key=lambda r: (r.get("fit_percentage") or -1), reverse=True)

    payload = json.dumps(results, ensure_ascii=False, indent=2)
    if args.output:
        Path(args.output).write_text(payload, encoding="utf-8")
        ok = sum(1 for r in results if r.get("fit_percentage") is not None)
        print(f"Matched {ok}/{len(entries)} -> {args.output}", file=sys.stderr)
    else:
        print(payload)


if __name__ == "__main__":
    main()
