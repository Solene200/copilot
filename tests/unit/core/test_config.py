"""Tests for environment-backed application settings."""

import pytest
from pydantic import SecretStr, ValidationError

from incident_copilot.core.config import LogLevel, MetricsBackend, RuntimeEnvironment, Settings


def test_settings_have_safe_offline_defaults() -> None:
    settings = Settings(_env_file=None)

    assert settings.environment is RuntimeEnvironment.DEVELOPMENT
    assert settings.debug is False
    assert settings.log_level is LogLevel.INFO
    assert settings.metrics_backend is MetricsBackend.FIXTURE
    assert settings.prometheus_base_url == "http://127.0.0.1:9090"
    assert settings.model_api_key is None


def test_settings_load_prefixed_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("INCIDENT_COPILOT_ENVIRONMENT", "test")
    monkeypatch.setenv("INCIDENT_COPILOT_LOG_LEVEL", "DEBUG")
    monkeypatch.setenv("INCIDENT_COPILOT_MODEL_API_KEY", "not-a-real-secret")
    monkeypatch.setenv("INCIDENT_COPILOT_METRICS_BACKEND", "prometheus")
    monkeypatch.setenv("INCIDENT_COPILOT_PROMETHEUS_BASE_URL", "http://prometheus:9090")

    settings = Settings(_env_file=None)

    assert settings.environment is RuntimeEnvironment.TEST
    assert settings.log_level is LogLevel.DEBUG
    assert settings.metrics_backend is MetricsBackend.PROMETHEUS
    assert settings.prometheus_base_url == "http://prometheus:9090"
    assert isinstance(settings.model_api_key, SecretStr)
    assert settings.model_api_key.get_secret_value() == "not-a-real-secret"
    assert "not-a-real-secret" not in repr(settings)


@pytest.mark.parametrize("prefix", ["api", "/", "/api/"])
def test_settings_reject_invalid_api_prefix(prefix: str) -> None:
    with pytest.raises(ValidationError):
        Settings(api_prefix=prefix, _env_file=None)
