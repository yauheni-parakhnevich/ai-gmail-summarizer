from gmail_summarizer.matcher import MatchResult
from gmail_summarizer.report import _fit_color, format_report
from gmail_summarizer.scraper import VacancyInfo


def _make_match(fit=75, title="Engineer", company="Acme", location="Zurich", gaps=None, key_matches=None):
    return MatchResult(
        vacancy=VacancyInfo(
            url="https://example.com/job/1",
            title=title,
            company=company,
            location=location,
            description="A great role",
            requirements=["Python"],
            salary=None,
        ),
        fit_percentage=fit,
        summary="Good fit overall.",
        key_matches=key_matches or ["Python"],
        gaps=gaps or [],
    )


class TestFitColor:
    def test_high(self):
        assert _fit_color(70) == "#34a853"
        assert _fit_color(100) == "#34a853"

    def test_medium(self):
        assert _fit_color(50) == "#fbbc04"
        assert _fit_color(69) == "#fbbc04"

    def test_low(self):
        assert _fit_color(49) == "#ea4335"
        assert _fit_color(0) == "#ea4335"


class TestFormatReport:
    def test_contains_vacancy_info(self):
        html = format_report([_make_match()])
        assert "Engineer" in html
        assert "Acme" in html
        assert "Zurich" in html
        assert "75%" in html
        assert "https://example.com/job/1" in html

    def test_multiple_matches(self):
        matches = [_make_match(fit=80, title="Senior Dev"), _make_match(fit=60, title="Junior Dev")]
        html = format_report(matches)
        assert "Senior Dev" in html
        assert "Junior Dev" in html
        assert "2" in html  # count

    def test_gaps_and_key_matches(self):
        html = format_report([_make_match(gaps=["Java", "K8s"], key_matches=["Python", "SQL"])])
        assert "Java" in html
        assert "K8s" in html
        assert "Python" in html
        assert "SQL" in html

    def test_empty_gaps_shows_none(self):
        html = format_report([_make_match(gaps=[])])
        assert "None identified" in html

    def test_empty_matches_list(self):
        html = format_report([])
        assert "<strong>0</strong>" in html
