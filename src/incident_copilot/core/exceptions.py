"""不依赖传输框架的应用异常层次。"""

from enum import StrEnum
from typing import ClassVar

from pydantic import JsonValue


class ErrorCode(StrEnum):
    """传输 Adapter 对外公开的稳定错误码。"""

    DOMAIN_VALIDATION = "domain_validation_error"
    CONFIGURATION = "configuration_error"
    NOT_FOUND = "resource_not_found"
    CONFLICT = "resource_conflict"
    INTERNAL = "internal_error"


class IncidentCopilotError(Exception):
    """携带安全公开消息和结构化详情的基础异常。"""

    code: ClassVar[ErrorCode] = ErrorCode.INTERNAL
    status_code: ClassVar[int] = 500

    def __init__(self, message: str, *, details: dict[str, JsonValue] | None = None) -> None:
        super().__init__(message)
        self.message = message
        self.details = details or {}


class DomainValidationError(IncidentCopilotError):
    """传输请求格式有效但违反领域规则时抛出。"""

    code: ClassVar[ErrorCode] = ErrorCode.DOMAIN_VALIDATION
    status_code: ClassVar[int] = 400


class ConfigurationError(IncidentCopilotError):
    """运行时配置无法支持某项操作时抛出。"""

    code: ClassVar[ErrorCode] = ErrorCode.CONFIGURATION
    status_code: ClassVar[int] = 500


class ResourceNotFoundError(IncidentCopilotError):
    """请求的应用资源不存在时抛出。"""

    code: ClassVar[ErrorCode] = ErrorCode.NOT_FOUND
    status_code: ClassVar[int] = 404


class ResourceConflictError(IncidentCopilotError):
    """状态转换或幂等键无法应用时抛出。"""

    code: ClassVar[ErrorCode] = ErrorCode.CONFLICT
    status_code: ClassVar[int] = 409
