import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv

PROJECT_ROOT = Path(__file__).resolve().parents[2]
load_dotenv(PROJECT_ROOT / ".env")


def _csv(name: str) -> list[str]:
    return [x.strip() for x in os.getenv(name, "").split(",") if x.strip()]


@dataclass(frozen=True)
class Settings:
    source_database_url: str = os.getenv("SOURCE_DATABASE_URL", "")
    analytics_db_path: Path = PROJECT_ROOT / os.getenv("ANALYTICS_DB_PATH", "data/analytics.duckdb")
    internal_user_ids: tuple[str, ...] = tuple(_csv("INTERNAL_USER_IDS"))
    internal_email_domains: tuple[str, ...] = tuple(d.lower() for d in _csv("INTERNAL_EMAIL_DOMAINS"))
    tz_offset_minutes: int = int(os.getenv("TZ_OFFSET_MINUTES", "330"))
    # "YYYY-MM-DD" or "YYYY-MM-DD HH:MM" in local time (TZ_OFFSET_MINUTES). Empty = copy all history.
    sync_since: str = os.getenv("SYNC_SINCE", "").strip()

    @property
    def sync_since_utc(self):
        if not self.sync_since:
            return None
        from datetime import datetime, timedelta
        fmt = "%Y-%m-%d %H:%M" if " " in self.sync_since else "%Y-%m-%d"
        return datetime.strptime(self.sync_since, fmt) - timedelta(minutes=self.tz_offset_minutes)
    # Friction Finder
    friction_db_path: Path = PROJECT_ROOT / os.getenv("FRICTION_DB_PATH", "data/friction.sqlite")
    friction_config_path: Path = PROJECT_ROOT / "config" / "friction.json"
    exports_dir: Path = PROJECT_ROOT / "data" / "exports"
    # "HH:MM" in IST: while the server runs, sync + analyse yesterday's chats daily at this time. Empty = off.
    friction_daily_at: str = os.getenv("FRICTION_DAILY_AT", "").strip()
    # LLM judge is enabled when an Anthropic credential is configured.
    anthropic_api_key: str = os.getenv("ANTHROPIC_API_KEY", "").strip()
    friction_use_llm: str = os.getenv("FRICTION_USE_LLM", "auto").strip().lower()  # auto | on | off
    # Which account pays for the judge: auto | api (ANTHROPIC_API_KEY) | subscription (Claude Code CLI login, Pro/Max)
    friction_llm_provider: str = os.getenv("FRICTION_LLM_PROVIDER", "auto").strip().lower()
    claude_cli_path: str = os.getenv("CLAUDE_CLI_PATH", "").strip()
    # Hosted deployments: when BASIC_AUTH_PASSWORD is set, every page and API call needs this login.
    basic_auth_user: str = os.getenv("BASIC_AUTH_USER", "bookr").strip()
    basic_auth_password: str = os.getenv("BASIC_AUTH_PASSWORD", "").strip()


settings = Settings()
