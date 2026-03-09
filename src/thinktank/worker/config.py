"""Worker-specific settings.

Uses pydantic-settings for env-var configuration, following the same
pattern as thinktank.config.Settings.

Spec reference: Section 6.2 (worker configuration).
Environment prefix: WORKER_ (e.g., WORKER_POLL_INTERVAL=2.0).
"""

from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class WorkerSettings(BaseSettings):
    """Worker process configuration.

    All fields have sensible defaults for CPU workers.
    Override via environment variables with WORKER_ prefix.
    """

    model_config = SettingsConfigDict(env_prefix="WORKER_", case_sensitive=False)

    # Polling
    poll_interval: float = 2.0  # seconds between polls when active
    max_idle_backoff: float = 30.0  # max seconds between polls when idle
    idle_backoff_multiplier: float = 1.5

    # Concurrency
    max_concurrency: int = 4  # max concurrent job tasks

    # Reclamation
    reclaim_interval: float = 300.0  # 5 minutes in seconds

    # Worker identity
    service_type: str = "cpu"  # "cpu" or "gpu"

    # Job type filter (None = claim all types)
    job_types: list[str] | None = None


@lru_cache
def get_worker_settings() -> WorkerSettings:
    """Return cached WorkerSettings singleton.

    Uses lru_cache to ensure settings are loaded once and reused.
    """
    return WorkerSettings()
