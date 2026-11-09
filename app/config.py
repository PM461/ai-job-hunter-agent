import json
import os
from pathlib import Path
from pydantic_settings import BaseSettings, SettingsConfigDict


BASE_DIR = Path(__file__).resolve().parent.parent
DATA_DIR = BASE_DIR / "data"
BROWSER_STATE_DIR = DATA_DIR / "browser_state"

DATA_DIR.mkdir(parents=True, exist_ok=True)
BROWSER_STATE_DIR.mkdir(parents=True, exist_ok=True)


class Settings(BaseSettings):
    # Gemini AI
    GEMINI_API_KEY: str = ""
    GEMINI_MODEL: str = "gemini-3.5-flash-lite"

    # Telegram Bot
    TELEGRAM_BOT_TOKEN: str = ""
    TELEGRAM_CHAT_ID: str = ""

    # Email SMTP
    SMTP_ENABLED: bool = False
    SMTP_SERVER: str = "smtp.gmail.com"
    SMTP_PORT: int = 587
    SMTP_USER: str = ""
    SMTP_PASSWORD: str = ""
    NOTIFICATION_EMAIL: str = ""

    # LinkedIn Credentials
    LINKEDIN_EMAIL: str = ""
    LINKEDIN_PASSWORD: str = ""

    # Matching & Scheduler
    AUTO_APPLY_THRESHOLD: int = 85
    REVIEW_THRESHOLD: int = 65
    CHECK_INTERVAL_MINUTES: int = 60
    HEADLESS_BROWSER: bool = True

    model_config = SettingsConfigDict(
        env_file=str(BASE_DIR / ".env"),
        env_file_encoding="utf-8",
        extra="ignore",
    )


settings = Settings()

# Pulizia automatica: rimuove virgolette o spazi accidentali inseriti nelle dashboard cloud
if settings.TELEGRAM_BOT_TOKEN:
    settings.TELEGRAM_BOT_TOKEN = settings.TELEGRAM_BOT_TOKEN.replace('"', '').replace("'", "").replace(" ", "").strip()

if settings.TELEGRAM_CHAT_ID:
    settings.TELEGRAM_CHAT_ID = settings.TELEGRAM_CHAT_ID.replace('"', '').replace("'", "").replace(" ", "").strip()

if settings.GEMINI_API_KEY:
    settings.GEMINI_API_KEY = settings.GEMINI_API_KEY.replace('"', '').replace("'", "").strip()


def load_candidate_config() -> dict:
    config_path = DATA_DIR / "candidate_profile.json"
    example_path = DATA_DIR / "candidate_profile.example.json"
    if not config_path.exists():
        if example_path.exists():
            with open(example_path, "r", encoding="utf-8") as f:
                data = json.load(f)
            save_candidate_config(data)
            return data
        return {}
    with open(config_path, "r", encoding="utf-8") as f:
        return json.load(f)


def save_candidate_config(cfg: dict) -> None:
    config_path = DATA_DIR / "candidate_profile.json"
    with open(config_path, "w", encoding="utf-8") as f:
        json.dump(cfg, f, indent=2, ensure_ascii=False)

