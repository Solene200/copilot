"""从环境变量加载的强类型应用配置。"""

from enum import StrEnum
from functools import cache

from pydantic import Field, SecretStr, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

from incident_copilot import __version__


class RuntimeEnvironment(StrEnum):
    """应用配置支持的运行环境。"""

    DEVELOPMENT = "development"
    TEST = "test"
    STAGING = "staging"
    PRODUCTION = "production"


class LogLevel(StrEnum):
    """标准库日志系统接受的日志级别。"""

    DEBUG = "DEBUG"
    INFO = "INFO"
    WARNING = "WARNING"
    ERROR = "ERROR"
    CRITICAL = "CRITICAL"


class CheckpointBackend(StrEnum):
    """支持的 LangGraph 持久化 Adapter。"""

    MEMORY = "memory"
    POSTGRES = "postgres"


class MetricsBackend(StrEnum):
    """支持的指标 Provider Adapter。"""

    FIXTURE = "fixture"
    PROMETHEUS = "prometheus"


class Settings(BaseSettings):
    """带有安全离线默认值的应用配置。"""

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
        """要求后续路由使用规范化且非根路径的 API 前缀。"""
        value = value.strip()
        if not value.startswith("/"):
            raise ValueError("api_prefix must start with '/'")
        if value == "/" or value.endswith("/"):
            raise ValueError("api_prefix must not be '/' or end with '/'")
        return value


@cache
def get_settings() -> Settings:
    """为每个进程返回一个配置实例。"""
    return Settings()
