"""Entry point — orchestrates the email summarization pipeline."""

import argparse
import sys
from datetime import date

from gmail_summarizer.config import load_config
from gmail_summarizer.gmail import (
    authenticate,
    fetch_unread_emails,
    send_email,
    mark_as_read,
)
from gmail_summarizer.classifier import classify_emails
from gmail_summarizer.link_extractor import extract_job_links
from gmail_summarizer.scraper import scrape_vacancy
from gmail_summarizer.matcher import match_vacancy, MatchResult
from gmail_summarizer.report import format_report


def main() -> None:
    parser = argparse.ArgumentParser(description="AI-powered Gmail job summarizer")
    parser.add_argument(
        "--profile",
        required=True,
        help="Profile name (directory under profiles/)",
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

    print("Authenticating with Gmail...")
    service = authenticate(config)

    print("Fetching unread emails...")
    emails = fetch_unread_emails(service)
    print(f"  Found {len(emails)} unread emails")

    if not emails:
        print("No unread emails found. Done.")
        return

    print("Classifying emails with Claude...")
    job_emails = classify_emails(config, emails)
    print(f"  {len(job_emails)} job-related emails identified")

    if not job_emails:
        print("No job-related emails found. Done.")
        return

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
        return

    print("Scraping vacancy pages...")
    vacancies = []
    for url in unique_links:
        print(f"  Scraping: {url[:80]}...")
        vacancy = scrape_vacancy(config, url)
        if vacancy:
            vacancies.append(vacancy)
            print(f"    -> {vacancy.title} at {vacancy.company}")
        else:
            print(f"    -> Failed to scrape")

    if not vacancies:
        print("Could not extract details from any vacancy pages. Done.")
        return

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
        return

    matches.sort(key=lambda m: m.fit_percentage, reverse=True)

    print("Generating report...")
    html_report = format_report(matches)
    subject = f"Job Opportunities Report — {date.today().isoformat()}"

    print(f"Sending report to {config.recipient_email}...")
    send_email(config, subject, html_report)
    print("  Email sent!")

    print("Marking processed emails as read...")
    mark_as_read(service, [e.id for e in job_emails])
    print(f"  {len(job_emails)} emails marked as read")

    print(f"\nDone! {len(matches)} matching vacancies found:")
    for i, m in enumerate(matches, 1):
        print(f"  {i}. [{m.fit_percentage}%] {m.vacancy.title} at {m.vacancy.company}")


if __name__ == "__main__":
    main()
