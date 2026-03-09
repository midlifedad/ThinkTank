"""Unit tests for the configuration system.

Tests verify:
1. Settings loads env vars with correct types and defaults
2. Environment variables override code defaults
3. get_settings() returns a Settings instance
"""

import os

import pytest


class TestSettingsDefaults:
    """Verify Settings() returns correct default values without env vars."""

    def test_default_database_url(self, monkeypatch):
        """Settings() without DATABASE_URL env var has the default PostgreSQL URL."""
        monkeypatch.delenv("DATABASE_URL", raising=False)
        # Clear lru_cache so fresh Settings is created
        from thinktank.config import Settings

        settings = Settings()
        assert settings.database_url == "postgresql+asyncpg://thinktank:thinktank@localhost:5432/thinktank"

    def test_debug_default_false(self, monkeypatch):
        """Settings().debug is False by default."""
        monkeypatch.delenv("DEBUG", raising=False)
        from thinktank.config import Settings

        settings = Settings()
        assert settings.debug is False

    def test_service_name_default(self, monkeypatch):
        """Settings().service_name defaults to 'thinktank-api'."""
        monkeypatch.delenv("SERVICE_NAME", raising=False)
        from thinktank.config import Settings

        settings = Settings()
        assert settings.service_name == "thinktank-api"

    def test_pool_size_default(self, monkeypatch):
        """Settings().db_pool_size defaults to 10."""
        monkeypatch.delenv("DB_POOL_SIZE", raising=False)
        from thinktank.config import Settings

        settings = Settings()
        assert settings.db_pool_size == 10

    def test_max_overflow_default(self, monkeypatch):
        """Settings().db_max_overflow defaults to 5."""
        monkeypatch.delenv("DB_MAX_OVERFLOW", raising=False)
        from thinktank.config import Settings

        settings = Settings()
        assert settings.db_max_overflow == 5

    def test_log_level_default(self, monkeypatch):
        """Settings().log_level defaults to 'INFO'."""
        monkeypatch.delenv("LOG_LEVEL", raising=False)
        from thinktank.config import Settings

        settings = Settings()
        assert settings.log_level == "INFO"

    def test_cors_origins_default(self, monkeypatch):
        """Settings().cors_origins defaults to ['*']."""
        monkeypatch.delenv("CORS_ORIGINS", raising=False)
        from thinktank.config import Settings

        settings = Settings()
        assert settings.cors_origins == ["*"]


class TestSettingsEnvOverrides:
    """Verify environment variables override code defaults."""

    def test_env_var_overrides_database_url(self, monkeypatch):
        """With DATABASE_URL env var set, Settings loads the override."""
        monkeypatch.setenv("DATABASE_URL", "postgresql+asyncpg://other:other@db:5432/other")
        from thinktank.config import Settings

        settings = Settings()
        assert settings.database_url == "postgresql+asyncpg://other:other@db:5432/other"

    def test_env_var_overrides_debug(self, monkeypatch):
        """DEBUG env var True overrides the default False."""
        monkeypatch.setenv("DEBUG", "true")
        from thinktank.config import Settings

        settings = Settings()
        assert settings.debug is True

    def test_env_var_overrides_log_level(self, monkeypatch):
        """LOG_LEVEL env var overrides the default."""
        monkeypatch.setenv("LOG_LEVEL", "DEBUG")
        from thinktank.config import Settings

        settings = Settings()
        assert settings.log_level == "DEBUG"

    def test_env_var_overrides_pool_size(self, monkeypatch):
        """DB_POOL_SIZE env var overrides the default with correct int type."""
        monkeypatch.setenv("DB_POOL_SIZE", "20")
        from thinktank.config import Settings

        settings = Settings()
        assert settings.db_pool_size == 20
        assert isinstance(settings.db_pool_size, int)


class TestGetSettings:
    """Verify get_settings() returns a valid Settings instance."""

    def test_get_settings_returns_settings(self):
        """get_settings() returns a Settings instance."""
        from thinktank.config import Settings, get_settings

        settings = get_settings()
        assert isinstance(settings, Settings)
