import pytest

from gmail_summarizer.config import load_config


@pytest.fixture()
def profile_dir(tmp_path, monkeypatch):
    """Create a minimal profile directory and patch profiles/ to point at tmp_path."""
    profile_name = "testprofile"
    pdir = tmp_path / profile_name
    pdir.mkdir()

    config_yaml = pdir / "config.yaml"
    config_yaml.write_text(
        "recipient_email: user@example.com\nmin_fit_percentage: 50\nlinkedin_email: li@example.com\n"
    )

    # Patch the profiles base path by changing cwd
    monkeypatch.chdir(tmp_path / "..")
    # Create a profiles/ symlink pointing to tmp_path
    profiles_link = tmp_path / ".." / "profiles"
    if not profiles_link.exists():
        profiles_link.symlink_to(tmp_path)

    # Set env vars for shared config
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test-123")
    monkeypatch.setenv("SENDER_EMAIL", "sender@example.com")
    monkeypatch.setenv("SMTP_PASSWORD", "secret")

    yield profile_name

    # Cleanup symlink
    if profiles_link.is_symlink():
        profiles_link.unlink()


class TestLoadConfig:
    def test_loads_profile_values(self, profile_dir):
        config = load_config(profile_dir)
        assert config.recipient_email == "user@example.com"
        assert config.min_fit_percentage == 50
        assert config.linkedin_email == "li@example.com"
        assert config.linkedin_password == ""

    def test_loads_env_values(self, profile_dir):
        config = load_config(profile_dir)
        assert config.anthropic_api_key == "sk-test-123"
        assert config.sender_email == "sender@example.com"
        assert config.smtp_password == "secret"

    def test_missing_profile_raises(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        (tmp_path / "profiles").mkdir()
        with pytest.raises(FileNotFoundError):
            load_config("nonexistent")
