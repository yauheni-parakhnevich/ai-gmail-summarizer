"""Classify emails as job-related or not using Claude."""

import json
import re

import anthropic

from gmail_summarizer.config import Config
from gmail_summarizer.gmail import Email

SYSTEM_PROMPT = """\
You are an email classifier. Given a list of emails (subject + snippet), \
determine which ones are related to job opportunities, new positions, \
job postings, career opportunities, or recruitment.

Include emails that:
- Directly advertise jobs ("new position", "we're hiring", "job opportunity")
- Suggest candidate fit ("You may be a fit...", "based on your profile", "roles matching your skills")
- Come from recruiters, job boards, or hiring platforms (LinkedIn, Indeed, Glassdoor, etc.)
- Contain job alerts, saved search results, or talent community updates

Exclude newsletters, marketing, social media notifications, and non-job correspondence.

Respond with a JSON array of email IDs that are job-related.
Example: ["id1", "id3"]

If none are job-related, respond with an empty array: []
Only output the JSON array, nothing else."""


def classify_emails(config: Config, emails: list[Email]) -> list[Email]:
    """Filter emails to only those classified as job-related by Claude."""
    if not emails:
        return []

    client = anthropic.Anthropic(api_key=config.anthropic_api_key)

    email_descriptions = "\n\n".join(
        f"ID: {e.id}\nSubject: {e.subject}\nFrom: {e.sender}\nSnippet: {e.body_text[:300]}" for e in emails
    )

    response = client.messages.create(
        model=config.claude_model,
        max_tokens=1024,
        system=SYSTEM_PROMPT,
        messages=[{"role": "user", "content": email_descriptions}],
    )

    response_text = _strip_code_fences(response.content[0].text)
    job_ids: list[str] = json.loads(response_text)

    job_id_set = set(job_ids)
    return [e for e in emails if e.id in job_id_set]


def _strip_code_fences(text: str) -> str:
    text = text.strip()
    text = re.sub(r"^```(?:json)?\s*\n?", "", text)
    text = re.sub(r"\n?```\s*$", "", text)
    return text.strip()
