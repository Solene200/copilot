"""Typed application settings loaded from environment variables."""

from enum import StrEnum
from functools import cache

from pydantic import Field, SecretStr, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

from incident_copilot import __version__


class RuntimeEnvironment(StrEnum):
    """Supported runtime environments for application configuration."""

    DEVELOPMENT = "development"
    TEST = "test"
    STAGING = "staging"
    PRODUCTION = "production"


class LogLevel(StrEnum):
    """Log levels accepted by the standard-library logging system."""

    DEBUG = "DEBUG"
    INFO = "INFO"
    WARNING = "WARNING"
    ERROR = "ERROR"
    CRITICAL = "CRITICAL"


class CheckpointBackend(StrEnum):
    """Supported LangGraph persistence adapters."""

    MEMORY = "memory"
    POSTGRES = "postgres"


class MetricsBackend(StrEnum):
    """Supported metric provider adapters."""

    FIXTURE = "fixture"
    PROMETHEUS = "prometheus"


class Settings(BaseSettings):
    """Application configuration with safe offline defaults."""

    model_config = SettingsConfigDict(
        env_prefix="INCIDENT_COPILOT_",
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    app_name: str = Field(default="IncidentCopilot", min_length=1, max_length=100)
    app_version: str = Field(default=__version__, min_length=1, max_length=50)
    environment: RuntimeEnvironment = RuntimeEnvironment.DEVELOPMENT
    debug: bool = False
    log_level: LogLevel = LogLevel.INFO
    api_prefix: str = "/api"
    sse_heartbeat_seconds: float = Field(default=15.0, gt=0, le=60)
    checkpoint_backend: CheckpointBackend = CheckpointBackend.MEMORY
    postgres_dsn: SecretStr | None = Field(default=None, repr=False)
    metrics_backend: MetricsBackend = MetricsBackend.FIXTURE
    prometheus_base_url: str = "http://127.0.0.1:9090"
    prometheus_timeout_seconds: float = Field(default=2.0, gt=0, le=30)
    model_api_key: SecretStr | None = Field(default=None, repr=False)

    @field_validator("api_prefix")
    @classmethod
    def validate_api_prefix(cls, value: str) -> str:
        """Require a normalized non-root API prefix for future routes."""
        value = value.strip()
        if not value.startswith("/"):
            raise ValueError("api_prefix must start with '/'")
        if value == "/" or value.endswith("/"):
            raise ValueError("api_prefix must not be '/' or end with '/'")
        return value


@cache
def get_settings() -> Settings:
    """Return one settings instance per process."""
    return Settings()
