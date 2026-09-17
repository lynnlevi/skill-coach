from dataclasses import dataclass, field
import os
from pathlib import Path
from typing import Mapping

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parents[1]


@dataclass(frozen=True)
class Settings:
    database_url: str = field(repr=False)
    api_key: str = field(default="", repr=False)
    model: str = "gpt-4.1-mini"
    transcription_model: str = "gpt-4o-mini-transcribe"
    admin_username: str = "coach_admin"
    admin_password: str = field(default="", repr=False)
    admin_name: str = "Coach"
    session_hours: int = 8

    @classmethod
    def load(cls, secrets: Mapping | None = None):
        load_dotenv(ROOT / ".env", override=False)
        secrets = secrets or {}

        def value(name, default=""):
            return str(os.environ.get(name, secrets.get(name, default)))

        return cls(
            database_url=value("DATABASE_URL", f"sqlite:///{ROOT / 'data/coaching.db'}"),
            api_key=value("OPENAI_API_KEY"),
            model=value("OPENAI_MODEL", "gpt-4.1-mini"),
            transcription_model=value("OPENAI_TRANSCRIPTION_MODEL", "gpt-4o-mini-transcribe"),
            admin_username=value("BOOTSTRAP_ADMIN_USERNAME", "coach_admin"),
            admin_password=value("BOOTSTRAP_ADMIN_PASSWORD"),
            admin_name=value("BOOTSTRAP_ADMIN_NAME", "Coach"),
            session_hours=max(1, min(24, int(value("SESSION_HOURS", "8")))),
        )
