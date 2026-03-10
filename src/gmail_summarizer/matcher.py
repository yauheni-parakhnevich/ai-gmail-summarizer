"""Compare vacancy against CV using Claude and return fit score + summary."""

import json
import re
from dataclasses import dataclass

import anthropic

from gmail_summarizer.config import Config
from gmail_summarizer.scraper import VacancyInfo

SYSTEM_PROMPT = """\
You are a career matching expert. Compare the given job vacancy against the \
candidate's CV and evaluate the fit.

Return a JSON object with:
- fit_percentage: integer 0-100 representing how well the candidate fits
- summary: 2-3 sentence summary of why this is or isn't a good fit
- key_matches: list of skills/experiences that match
- gaps: list of requirements the candidate doesn't meet

Only output the JSON object, nothing else."""


@dataclass
class MatchResult:
    vacancy: VacancyInfo
    fit_percentage: int
    summary: str
    key_matches: list[str]
    gaps: list[str]


def match_vacancy(config: Config, vacancy: VacancyInfo, cv_text: str) -> MatchResult:
    """Compare a vacancy against the CV and return a match result."""
    client = anthropic.Anthropic(api_key=config.anthropic_api_key)

    vacancy_text = (
        f"Title: {vacancy.title}\n"
        f"Company: {vacancy.company}\n"
        f"Location: {vacancy.location}\n"
        f"Description: {vacancy.description}\n"
        f"Requirements: {', '.join(vacancy.requirements)}\n"
        f"Salary: {vacancy.salary or 'Not specified'}"
    )

    prompt = f"## Job Vacancy\n{vacancy_text}\n\n## Candidate CV\n{cv_text}"

    response = client.messages.create(
        model=config.claude_model,
        max_tokens=1024,
        system=SYSTEM_PROMPT,
        messages=[{"role": "user", "content": prompt}],
    )

    data = json.loads(_strip_code_fences(response.content[0].text))

    return MatchResult(
        vacancy=vacancy,
        fit_percentage=int(data.get("fit_percentage", 0)),
        summary=data.get("summary", ""),
        key_matches=data.get("key_matches", []),
        gaps=data.get("gaps", []),
    )


def _strip_code_fences(text: str) -> str:
    text = text.strip()
    text = re.sub(r"^```(?:json)?\s*\n?", "", text)
    text = re.sub(r"\n?```\s*$", "", text)
    return text.strip()
