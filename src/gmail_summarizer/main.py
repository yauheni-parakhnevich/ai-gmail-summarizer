"""Entry point — orchestrates the email summarization pipeline."""

import argparse
import dataclasses
import json
import sys
from datetime import date
from pathlib import Path

from gmail_summarizer.classifier import classify_emails
from gmail_summarizer.config import Config, load_config
from gmail_summarizer.gmail import (
    authenticate,
    fetch_unread_emails,
    mark_as_read,
    send_email,
)
from gmail_summarizer.link_extractor import extract_job_links
from gmail_summarizer.matcher import MatchResult, match_vacancy
from gmail_summarizer.report import format_report
from gmail_summarizer.scraper import VacancyInfo, scrape_vacancy


def _state_path(config: Config) -> Path:
    return config.profile_dir / "state.json"


def _save_state(config: Config, data: dict) -> None:
    path = _state_path(config)
    path.write_text(json.dumps(data, indent=2, ensure_ascii=False))


def _load_state(config: Config) -> dict | None:
    path = _state_path(config)
    if not path.exists():
        return None
    return json.loads(path.read_text())


def _delete_state(config: Config) -> None:
    path = _state_path(config)
    if path.exists():
        path.unlink()


def _vacancy_to_dict(v: VacancyInfo) -> dict:
    return dataclasses.asdict(v)


def _vacancy_from_dict(d: dict) -> VacancyInfo:
    return VacancyInfo(**d)


def _match_to_dict(m: MatchResult) -> dict:
    return {
        "vacancy": _vacancy_to_dict(m.vacancy),
        "fit_percentage": m.fit_percentage,
        "summary": m.summary,
        "key_matches": m.key_matches,
        "gaps": m.gaps,
    }


def _match_from_dict(d: dict) -> MatchResult:
    return MatchResult(
        vacancy=_vacancy_from_dict(d["vacancy"]),
        fit_percentage=d["fit_percentage"],
        summary=d["summary"],
        key_matches=d["key_matches"],
        gaps=d["gaps"],
    )


def main() -> None:
    parser = argparse.ArgumentParser(description="AI-powered Gmail job summarizer")
    parser.add_argument(
        "--profile",
        required=True,
        help="Profile name (directory under profiles/)",
    )
    parser.add_argument(
        "--resume",
        action="store_true",
        help="Resume from saved state (skip already-completed stages)",
    )
    args = parser.parse_args()

    try:
        config = load_config(args.profile)
    except FileNotFoundError as e:
        print(f"Error: {e}")
        sys.exit(1)

    if not config.anthropic_api_key:
        print("Error: ANTHROPIC_API_KEY is not set. Add it to .env or environment.")
        sys.exit(1)
    if not config.credentials_path.exists():
        print(f"Error: Gmail credentials not found at {config.credentials_path}")
        sys.exit(1)
    if not config.sender_email or not config.recipient_email or not config.smtp_password:
        print("Error: SENDER_EMAIL and SMTP_PASSWORD must be set in .env; recipient_email in profile config.yaml")
        sys.exit(1)
    if not config.cv_path.exists():
        print(f"Error: CV file not found at {config.cv_path}")
        sys.exit(1)

    print(f"Using profile: {config.profile}")

    # Load saved state if resuming
    state: dict = {}
    if args.resume:
        state = _load_state(config) or {}
        if state:
            print(f"Resuming from saved state ({', '.join(state.keys())} available)")
        else:
            print("No saved state found, starting fresh")

    print("Authenticating with Gmail...")
    service = authenticate(config)

    # --- Determine resume point ---
    if state.get("matches"):
        matches = [_match_from_dict(d) for d in state["matches"]]
        job_email_ids = state.get("job_email_ids", [])
        print(f"  Restored {len(matches)} matches from state, skipping to report/send")
    elif state.get("vacancies"):
        vacancies = [_vacancy_from_dict(d) for d in state["vacancies"]]
        job_email_ids = state.get("job_email_ids", [])
        print(f"  Restored {len(vacancies)} vacancies from state, skipping to matching")
        matches = _run_matching(config, vacancies, state, job_email_ids)
        if matches is None:
            return
    elif state.get("links"):
        unique_links = state["links"]
        job_email_ids = state.get("job_email_ids", [])
        print(f"  Restored {len(unique_links)} links from state, skipping to scraping")
        vacancies = _run_scraping(config, unique_links, state, job_email_ids)
        if vacancies is None:
            return
        matches = _run_matching(config, vacancies, state, job_email_ids)
        if matches is None:
            return
    elif state.get("job_email_ids"):
        job_email_ids = state["job_email_ids"]
        print(f"  Restored {len(job_email_ids)} job email IDs, re-fetching for link extraction")
        emails = fetch_unread_emails(service)
        job_emails = [e for e in emails if e.id in job_email_ids]
        if not job_emails:
            print("Could not re-fetch job emails. Starting fresh.")
            unique_links, job_email_ids = _run_fetch_and_extract(config, service, state)
            if unique_links is None:
                return
        else:
            unique_links = _extract_links(config, job_emails, state, job_email_ids)
            if unique_links is None:
                return
        vacancies = _run_scraping(config, unique_links, state, job_email_ids)
        if vacancies is None:
            return
        matches = _run_matching(config, vacancies, state, job_email_ids)
        if matches is None:
            return
    else:
        # Full pipeline
        result = _run_fetch_and_extract(config, service, state)
        if result is None:
            return
        unique_links, job_email_ids = result
        vacancies = _run_scraping(config, unique_links, state, job_email_ids)
        if vacancies is None:
            return
        matches = _run_matching(config, vacancies, state, job_email_ids)
        if matches is None:
            return

    # --- Report & Send ---
    matches.sort(key=lambda m: m.fit_percentage, reverse=True)

    print("Generating report...")
    html_report = format_report(matches)
    subject = f"Job Opportunities Report — {date.today().isoformat()}"

    print(f"Sending report to {config.recipient_email}...")
    send_email(config, subject, html_report)
    print("  Email sent!")

    print("Marking processed emails as read...")
    mark_as_read(service, job_email_ids)
    print(f"  {len(job_email_ids)} emails marked as read")

    _delete_state(config)

    print(f"\nDone! {len(matches)} matching vacancies found:")
    for i, m in enumerate(matches, 1):
        print(f"  {i}. [{m.fit_percentage}%] {m.vacancy.title} at {m.vacancy.company}")


def _run_fetch_and_extract(config, service, state) -> tuple[list[str], list[str]] | None:
    """Fetch emails, classify, extract links. Returns (unique_links, job_email_ids) or None."""
    print("Fetching unread emails...")
    emails = fetch_unread_emails(service)
    print(f"  Found {len(emails)} unread emails")

    if not emails:
        print("No unread emails found. Done.")
        return None

    print("Classifying emails with Claude...")
    job_emails = classify_emails(config, emails)
    print(f"  {len(job_emails)} job-related emails identified")

    if not job_emails:
        print("No job-related emails found. Done.")
        return None

    job_email_ids = [e.id for e in job_emails]

    unique_links = _extract_links(config, job_emails, state, job_email_ids)
    if unique_links is None:
        return None
    return unique_links, job_email_ids


def _extract_links(config, job_emails, state, job_email_ids) -> list[str] | None:
    """Extract links from job emails, save state. Returns unique_links or None."""
    print("Extracting job links...")
    all_links: list[str] = []
    for email in job_emails:
        print(f"  [{email.subject}]")
        print(f"    body_html length: {len(email.body_html)}, body_text length: {len(email.body_text)}")
        links = extract_job_links(config, email)
        all_links.extend(links)
        print(f"    -> {len(links)} vacancy links found")
        for link in links:
            print(f"       {link[:100]}")

    unique_links = list(dict.fromkeys(all_links))
    print(f"  {len(unique_links)} unique links found")

    if not unique_links:
        print("No job links found in emails. Done.")
        return None

    # Save state after link extraction
    _save_state(config, {"job_email_ids": job_email_ids, "links": unique_links})
    return unique_links


def _run_scraping(config, unique_links, state, job_email_ids) -> list[VacancyInfo] | None:
    """Scrape vacancy pages, save state. Returns vacancies or None."""
    print("Scraping vacancy pages...")
    vacancies = []
    for url in unique_links:
        print(f"  Scraping: {url[:80]}...")
        vacancy = scrape_vacancy(config, url)
        if vacancy:
            vacancies.append(vacancy)
            print(f"    -> {vacancy.title} at {vacancy.company}")
        else:
            print("    -> Failed to scrape")

    if not vacancies:
        print("Could not extract details from any vacancy pages. Done.")
        return None

    # Save state after scraping
    _save_state(
        config,
        {
            "job_email_ids": job_email_ids,
            "links": unique_links,
            "vacancies": [_vacancy_to_dict(v) for v in vacancies],
        },
    )
    return vacancies


def _run_matching(config, vacancies, state, job_email_ids) -> list[MatchResult] | None:
    """Match vacancies against CV, save state. Returns matches or None."""
    print(f"Loading CV from {config.cv_path}...")
    cv_text = config.cv_path.read_text()

    print("Matching vacancies against CV...")
    matches: list[MatchResult] = []
    for vacancy in vacancies:
        print(f"  Matching: {vacancy.title}...")
        result = match_vacancy(config, vacancy, cv_text)
        if result.fit_percentage >= config.min_fit_percentage:
            matches.append(result)
            print(f"    -> {result.fit_percentage}% fit")
        else:
            print(f"    -> {result.fit_percentage}% fit (below threshold, skipped)")

    if not matches:
        print(f"No vacancies above {config.min_fit_percentage}% fit threshold. Done.")
        return None

    # Save state after matching
    _save_state(
        config,
        {
            "job_email_ids": job_email_ids,
            "links": state.get("links", []),
            "vacancies": [_vacancy_to_dict(v) for v in vacancies],
            "matches": [_match_to_dict(m) for m in matches],
        },
    )
    return matches


if __name__ == "__main__":
    main()
