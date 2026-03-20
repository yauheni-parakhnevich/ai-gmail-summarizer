from gmail_summarizer.config import Config
from gmail_summarizer.main import (
    _delete_state,
    _load_state,
    _match_from_dict,
    _match_to_dict,
    _save_state,
    _vacancy_from_dict,
    _vacancy_to_dict,
)
from gmail_summarizer.matcher import MatchResult
from gmail_summarizer.scraper import VacancyInfo


def _sample_vacancy():
    return VacancyInfo(
        url="https://example.com/job/1",
        title="Engineer",
        company="Acme",
        location="Zurich",
        description="Build things",
        requirements=["Python", "SQL"],
        salary="100k",
    )


def _sample_match():
    return MatchResult(
        vacancy=_sample_vacancy(),
        fit_percentage=85,
        summary="Strong fit",
        key_matches=["Python"],
        gaps=["Java"],
    )


def _make_config(tmp_path):
    """Create a minimal Config pointing at tmp_path as profile_dir."""
    return Config(
        anthropic_api_key="",
        claude_model="",
        sender_email="",
        smtp_host="",
        smtp_port=465,
        smtp_password="",
        profile="test",
        profile_dir=tmp_path,
        credentials_path=tmp_path / "credentials.json",
        token_path=tmp_path / "token.json",
        cv_path=tmp_path / "cv.md",
        matcher_instructions_path=tmp_path / "matcher_instructions.md",
        recipient_email="",
        min_fit_percentage=30,
        linkedin_email="",
        linkedin_password="",
        xing_email="",
        xing_password="",
    )


class TestVacancyRoundTrip:
    def test_round_trip(self):
        v = _sample_vacancy()
        d = _vacancy_to_dict(v)
        v2 = _vacancy_from_dict(d)
        assert v == v2

    def test_dict_keys(self):
        d = _vacancy_to_dict(_sample_vacancy())
        assert set(d.keys()) == {"url", "title", "company", "location", "description", "requirements", "salary"}


class TestMatchRoundTrip:
    def test_round_trip(self):
        m = _sample_match()
        d = _match_to_dict(m)
        m2 = _match_from_dict(d)
        assert m == m2

    def test_dict_keys(self):
        d = _match_to_dict(_sample_match())
        assert set(d.keys()) == {"vacancy", "fit_percentage", "summary", "key_matches", "gaps"}


class TestStateIO:
    def test_save_and_load(self, tmp_path):
        config = _make_config(tmp_path)
        data = {"links": ["https://example.com/1"], "job_email_ids": ["abc"]}
        _save_state(config, data)
        loaded = _load_state(config)
        assert loaded == data

    def test_load_missing_returns_none(self, tmp_path):
        config = _make_config(tmp_path)
        assert _load_state(config) is None

    def test_delete(self, tmp_path):
        config = _make_config(tmp_path)
        _save_state(config, {"test": True})
        _delete_state(config)
        assert _load_state(config) is None

    def test_delete_missing_no_error(self, tmp_path):
        config = _make_config(tmp_path)
        _delete_state(config)  # should not raise
