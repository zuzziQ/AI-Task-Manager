"""
Configuration module — loads all environment variables via pydantic-settings.
Copy .env.example to .env and fill in your values.
"""
from typing import List
from pydantic import field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # ── Telegram ───────────────────────────────────────────────────────────────
    telegram_bot_token: str = ""
    telegram_allowed_chat_ids: str = ""  # comma-separated
    telegram_admin_chat_id: str = ""

    # ── Plane.so ───────────────────────────────────────────────────────────────
    plane_api_key: str = ""
    plane_workspace_slug: str = ""
    plane_project_id: str = ""
    plane_base_url: str = "https://api.plane.so/api/v1"

    # ── Google Sheets ──────────────────────────────────────────────────────────
    google_sheet_id: str = ""
    google_service_account_file: str = "service_account.json"

    # ── Gemini AI ──────────────────────────────────────────────────────────────
    gemini_api_key: str = ""
    # Gemma 4 31B via Gemini API — model name as available on AI Studio
    gemini_model: str = "gemma-4-31b"

    # ── Backend API ────────────────────────────────────────────────────────────
    backend_api_url: str = "http://localhost:4500"

    # ── Scheduler ──────────────────────────────────────────────────────────────
    timezone: str = "Asia/Ho_Chi_Minh"
    morning_reminder_hour: int = 8
    morning_reminder_minute: int = 30
    afternoon_reminder_hour: int = 17
    afternoon_reminder_minute: int = 0

    # ── Team members (JSON config loaded separately) ───────────────────────────

    @property
    def allowed_chat_ids(self) -> List[int]:
        """Parse comma-separated chat IDs into a list of ints."""
        if not self.telegram_allowed_chat_ids:
            return []
        return [
            int(cid.strip())
            for cid in self.telegram_allowed_chat_ids.split(",")
            if cid.strip()
        ]

    @property
    def admin_chat_id(self) -> int | None:
        if self.telegram_admin_chat_id:
            return int(self.telegram_admin_chat_id)
        return None


settings = Settings()
