"""
config.py — Единая точка конфигурации.
Все настройки берутся из .env файла.
"""
import os
from dataclasses import dataclass, field
from dotenv import load_dotenv

load_dotenv()


@dataclass
class Config:
    # ─── База данных ────────────────────────────────────────────
    DB_HOST: str     = field(default_factory=lambda: os.getenv("DB_HOST", "localhost"))
    DB_PORT: int     = field(default_factory=lambda: int(os.getenv("DB_PORT", "5432")))
    DB_NAME: str     = field(default_factory=lambda: os.getenv("DB_NAME", "bfl_leads"))
    DB_USER: str     = field(default_factory=lambda: os.getenv("DB_USER", "postgres"))
    DB_PASSWORD: str = field(default_factory=lambda: os.getenv("DB_PASSWORD", ""))

    # ─── Telegram ────────────────────────────────────────────────
    BOT_TOKEN: str       = field(default_factory=lambda: os.getenv("BOT_TOKEN", ""))
    MANAGER_CHAT_ID: int = field(default_factory=lambda: int(os.getenv("MANAGER_CHAT_ID", "0")))
    BOT_USERNAME: str    = field(default_factory=lambda: os.getenv("BOT_USERNAME", ""))
    COMPANY_NAME: str    = field(default_factory=lambda: os.getenv("COMPANY_NAME", "БФЛ"))

    OUTREACH_MIN_SCORE: int   = field(default_factory=lambda: int(os.getenv("OUTREACH_MIN_SCORE", "40")))
    OUTREACH_BATCH_LIMIT: int = field(default_factory=lambda: int(os.getenv("OUTREACH_BATCH_LIMIT", "50")))

    # ─── Парсинг ─────────────────────────────────────────────────
    AVITO_CITIES: list   = field(default_factory=lambda: os.getenv(
        "AVITO_CITIES", "moskva"
    ).split(","))
    AVITO_MAX_PAGES: int = field(default_factory=lambda: int(os.getenv("AVITO_MAX_PAGES", "5")))
    AVITO_DELAY_MIN: int = field(default_factory=lambda: int(os.getenv("AVITO_DELAY_MIN", "3")))
    AVITO_DELAY_MAX: int = field(default_factory=lambda: int(os.getenv("AVITO_DELAY_MAX", "8")))

    @property
    def db_dsn(self) -> str:
        """Строка подключения для asyncpg."""
        return (
            f"postgresql://{self.DB_USER}:{self.DB_PASSWORD}"
            f"@{self.DB_HOST}:{self.DB_PORT}/{self.DB_NAME}"
        )

    @property
    def db_dsn_sync(self) -> str:
        """Строка подключения для psycopg2 (синхронный — для Streamlit)."""
        return (
            f"host={self.DB_HOST} port={self.DB_PORT} dbname={self.DB_NAME} "
            f"user={self.DB_USER} password={self.DB_PASSWORD}"
        )


config = Config()
