"""ThinkTank configuration system with env-var precedence.

Uses pydantic-settings for type-safe configuration loading.
Precedence: environment variables > .env file > code defaults.
"""

from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Application settings loaded from environment variables.

    All fields have sensible defaults for local development.
    Production values come from environment variables.
    """

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
    )

    # Database
    database_url: str = "postgresql+asyncpg://thinktank:thinktank@localhost:5432/thinktank"
    db_pool_size: int = 10
    db_max_overflow: int = 5

    # Application
    debug: bool = False
    service_name: str = "thinktank-api"
    log_level: str = "INFO"

    # CORS
    cors_origins: list[str] = ["*"]


@lru_cache
def get_settings() -> Settings:
    """Return cached Settings singleton.

    Uses lru_cache to ensure settings are loaded once and reused.
    """
    return Settings()
