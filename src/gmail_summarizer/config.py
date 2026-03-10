"""Configuration: shared .env settings + per-profile config.yaml overrides."""

import os
from dataclasses import dataclass
from pathlib import Path

import yaml
from dotenv import load_dotenv

load_dotenv()


@dataclass
class Config:
    # Shared (from .env)
    anthropic_api_key: str
    claude_model: str
    sender_email: str
    smtp_host: str
    smtp_port: int
    smtp_password: str

    # Per-profile
    profile: str
    profile_dir: Path
    credentials_path: Path
    token_path: Path
    cv_path: Path
    recipient_email: str
    min_fit_percentage: int
    linkedin_email: str
    linkedin_password: str
    xing_email: str
    xing_password: str


def load_config(profile: str) -> Config:
    """Load shared .env settings and merge with profile-specific config.yaml."""
    profile_dir = Path("profiles") / profile
    if not profile_dir.is_dir():
        raise FileNotFoundError(
            f"Profile directory not found: {profile_dir}\nCreate it with config.yaml, cv.md, and credentials.json"
        )

    config_file = profile_dir / "config.yaml"
    if not config_file.exists():
        raise FileNotFoundError(f"Profile config not found: {config_file}")

    with open(config_file) as f:
        profile_cfg = yaml.safe_load(f) or {}

    return Config(
        # Shared
        anthropic_api_key=os.getenv("ANTHROPIC_API_KEY", ""),
        claude_model=os.getenv("CLAUDE_MODEL", "claude-sonnet-4-20250514"),
        sender_email=os.getenv("SENDER_EMAIL", ""),
        smtp_host=os.getenv("SMTP_HOST", "smtp.migadu.com"),
        smtp_port=int(os.getenv("SMTP_PORT", "465")),
        smtp_password=os.getenv("SMTP_PASSWORD", ""),
        # Per-profile
        profile=profile,
        profile_dir=profile_dir,
        credentials_path=profile_dir / "credentials.json",
        token_path=profile_dir / "token.json",
        cv_path=profile_dir / "cv.md",
        recipient_email=profile_cfg.get("recipient_email", ""),
        min_fit_percentage=int(profile_cfg.get("min_fit_percentage", 30)),
        linkedin_email=profile_cfg.get("linkedin_email", ""),
        linkedin_password=profile_cfg.get("linkedin_password", ""),
        xing_email=profile_cfg.get("xing_email", ""),
        xing_password=profile_cfg.get("xing_password", ""),
    )
