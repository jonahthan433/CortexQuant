from datetime import datetime
from pathlib import Path
from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

BASE_DIR = Path(__file__).resolve().parent.parent

class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=str(BASE_DIR / ".env"), env_file_encoding="utf-8", extra="ignore", case_sensitive=False)
    BASE_DIR: Path = BASE_DIR
    DATA_DIR: Path = Path("/app/data")
    LOG_DIR: Path = Path("/app/logs")
    LIVE_MODE: str = "false"
    BINGX_DEMO: bool = True
    BINGX_API_KEY: str = ""
    BINGX_API_SECRET: str = ""
    TELEGRAM_API_ID: int = 0
    TELEGRAM_API_HASH: str = ""
    TELEGRAM_PHONE: str = ""
    TELEGRAM_CHANNEL: str = "@CKPscanner"
    TELEGRAM_SESSION: str = "ckpscanner_session"
    RISK_PER_TRADE: float = 0.02
    MAX_CONCURRENT_POSITIONS: int = 3
    MAX_DAILY_LOSS: float = 0.05
    MAX_POSITION_NOTIONAL: float = 100.0
    MAX_AGGREGATE_NOTIONAL: float = 300.0
    MAX_CORRELATION: float = 0.85
    DEFAULT_LEVERAGE: int = 5
    PAPER_DAYS: int = 7
    PAPER_START_TS: datetime | None = None
    DATABASE_URL: str = "postgresql://cortexquant@127.0.0.1:5432/cortexquant"
    KRONOS_MODEL: str = "NeoQuasar/Kronos-small"
    KRONOS_TOKENIZER: str = "NeoQuasar/Kronos-Tokenizer-base"
    KRONOS_DEVICE: str = "auto"
    KRONOS_MAX_CONTEXT: int = 512
    KRONOS_REQUIRE_ALIGNMENT: bool = False
    HEALTH_HOST: str = "127.0.0.1"
    HEALTH_PORT: int = 8080
    LOG_LEVEL: str = "INFO"
    TIMEZONE: str = "UTC"

    @field_validator("LIVE_MODE", mode="before")
    @classmethod
    def live_exact_string(cls, value):
        # Preserve exact opt-in semantics; e.g. True, 1, yes, and whitespace are not live.
        return "true" if value == "true" else "false"

    @field_validator("PAPER_DAYS")
    @classmethod
    def positive_paper_days(cls, value):
        if value < 1:
            raise ValueError("PAPER_DAYS must be at least 1")
        return value

    @property
    def live_opt_in(self) -> bool:
        return self.LIVE_MODE == "true"

    def ensure_dirs(self):
        self.DATA_DIR.mkdir(parents=True, exist_ok=True)
        self.LOG_DIR.mkdir(parents=True, exist_ok=True)
        (self.DATA_DIR / "telegram_session").mkdir(parents=True, exist_ok=True)

settings = Settings()
settings.ensure_dirs()
