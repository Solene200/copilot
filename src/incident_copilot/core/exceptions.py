"""Application exception hierarchy independent from transport frameworks."""

from enum import StrEnum
from typing import ClassVar

from pydantic import JsonValue


class ErrorCode(StrEnum):
    """Stable error codes exposed by transport adapters."""

    DOMAIN_VALIDATION = "domain_validation_error"
    CONFIGURATION = "configuration_error"
    NOT_FOUND = "resource_not_found"
    CONFLICT = "resource_conflict"
    INTERNAL = "internal_error"


class IncidentCopilotError(Exception):
    """Base exception carrying a safe public message and structured details."""

    code: ClassVar[ErrorCode] = ErrorCode.INTERNAL
    status_code: ClassVar[int] = 500

    def __init__(self, message: str, *, details: dict[str, JsonValue] | None = None) -> None:
        super().__init__(message)
        self.message = message
        self.details = details or {}


class DomainValidationError(IncidentCopilotError):
    """Raised when a valid transport request violates domain rules."""

    code: ClassVar[ErrorCode] = ErrorCode.DOMAIN_VALIDATION
    status_code: ClassVar[int] = 400


class ConfigurationError(IncidentCopilotError):
    """Raised when runtime configuration cannot support an operation."""

    code: ClassVar[ErrorCode] = ErrorCode.CONFIGURATION
    status_code: ClassVar[int] = 500


class ResourceNotFoundError(IncidentCopilotError):
    """Raised when a requested application resource does not exist."""

    code: ClassVar[ErrorCode] = ErrorCode.NOT_FOUND
    status_code: ClassVar[int] = 404


class ResourceConflictError(IncidentCopilotError):
    """Raised when a state transition or idempotency key cannot be applied."""

    code: ClassVar[ErrorCode] = ErrorCode.CONFLICT
    status_code: ClassVar[int] = 409
