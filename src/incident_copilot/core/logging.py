"""采用保守秘密脱敏策略的结构化 JSON 日志。"""

import json
import logging
import logging.config
import re
from collections.abc import Mapping, Sequence
from datetime import UTC, datetime
from typing import Any, Final

from incident_copilot.core.config import LogLevel

REDACTED: Final = "***REDACTED***"
_SENSITIVE_KEYS: Final = frozenset(
    {
        "api_key",
        "apikey",
        "authorization",
        "client_secret",
        "password",
        "secret",
        "token",
    }
)
_KEY_VALUE_PATTERN: Final = re.compile(
    r"(?i)([\"']?(?:api[_-]?key|client[_-]?secret|password|secret|"
    r"(?:access|auth|bearer|id|refresh|session)?[_-]?token)[\"']?\s*[=:]\s*[\"']?)"
    r"([^\s,;}\"']+)"
)
_AUTHORIZATION_PATTERN: Final = re.compile(
    r"(?i)([\"']?authorization[\"']?\s*[=:]\s*[\"']?)"
    r"(?:(?:basic|bearer)\s+)?[^\s,;}\"']+"
)
_BEARER_PATTERN: Final = re.compile(r"(?i)(bearer\s+)[a-z0-9._~+/=-]+")
_STANDARD_LOG_RECORD_FIELDS: Final = frozenset(logging.makeLogRecord({}).__dict__)


def redact_text(value: str) -> str:
    """从字符串中脱敏常见的内联凭据格式。"""
    value = _AUTHORIZATION_PATTERN.sub(rf"\1{REDACTED}", value)
    value = _BEARER_PATTERN.sub(rf"\1{REDACTED}", value)
    return _KEY_VALUE_PATTERN.sub(rf"\1{REDACTED}", value)


def _is_sensitive_key(key: str) -> bool:
    normalized = key.casefold().replace("-", "_")
    return normalized in _SENSITIVE_KEYS or normalized.endswith(
        ("_api_key", "_authorization", "_password", "_secret", "_token")
    )


def redact_value(value: Any, *, key: str | None = None) -> Any:
    """递归脱敏键名或内容疑似敏感的数据。"""
    if key is not None and _is_sensitive_key(key):
        return REDACTED
    if isinstance(value, str):
        return redact_text(value)
    if isinstance(value, Mapping):
        return {
            str(item_key): redact_value(item, key=str(item_key)) for item_key, item in value.items()
        }
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        return [redact_value(item) for item in value]
    return value


class JsonFormatter(logging.Formatter):
    """把标准日志记录和安全扩展字段序列化为一个 JSON 对象。"""

    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, Any] = {
            "timestamp": datetime.now(UTC).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "message": redact_text(record.getMessage()),
        }
        for key, value in record.__dict__.items():
            if key not in _STANDARD_LOG_RECORD_FIELDS and key not in {"message", "asctime"}:
                payload[key] = redact_value(value, key=key)
        if record.exc_info:
            payload["exception"] = redact_text(self.formatException(record.exc_info))
        return json.dumps(payload, ensure_ascii=False, default=str)


def configure_logging(level: LogLevel | str = LogLevel.INFO) -> None:
    """使用 JSON 控制台处理器幂等配置进程日志。"""
    resolved_level = level.value if isinstance(level, LogLevel) else level.upper()
    logging.config.dictConfig(
        {
            "version": 1,
            "disable_existing_loggers": False,
            "formatters": {"json": {"()": JsonFormatter}},
            "handlers": {
                "console": {
                    "class": "logging.StreamHandler",
                    "formatter": "json",
                    "level": resolved_level,
                }
            },
            "root": {"handlers": ["console"], "level": resolved_level},
        }
    )
