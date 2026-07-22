"""FastAPI 异常到响应的映射。"""

import logging
from typing import cast
from uuid import uuid4

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from pydantic import JsonValue
from starlette.responses import JSONResponse

from incident_copilot.api.schemas import ErrorDetail, ErrorResponse
from incident_copilot.core.exceptions import IncidentCopilotError
from incident_copilot.core.logging import redact_text, redact_value

logger = logging.getLogger(__name__)


def _request_id(request: Request) -> str:
    return request.headers.get("X-Request-ID") or f"req_{uuid4().hex}"


async def handle_application_error(request: Request, exc: Exception) -> JSONResponse:
    """把已知应用异常映射为公开错误响应。"""
    # FastAPI 按异常类型注册处理器, 此检查同时保护直接调用测试和错误注册配置。
    if not isinstance(exc, IncidentCopilotError):
        raise exc
    # 应用异常的 message/details 仍经过脱敏, 不能因为“已知异常”就默认内容安全。
    response = ErrorResponse(
        error=ErrorDetail(
            code=exc.code.value,
            message=redact_text(exc.message),
            details=cast(dict[str, JsonValue], redact_value(exc.details)),
        ),
        request_id=_request_id(request),
    )
    return JSONResponse(status_code=exc.status_code, content=response.model_dump(mode="json"))


async def handle_request_validation_error(request: Request, exc: Exception) -> JSONResponse:
    """返回校验失败信息,但不回显可能敏感的输入。"""
    if not isinstance(exc, RequestValidationError):
        raise exc
    issues: list[dict[str, JsonValue]] = []
    for error in exc.errors():
        # 只返回位置、类型和安全消息, 刻意丢弃 Pydantic error 中可能包含的原始 input。
        location = [
            str(part) if not isinstance(part, int) else part for part in error.get("loc", ())
        ]
        issues.append(
            {
                "type": str(error.get("type", "validation_error")),
                "loc": cast(JsonValue, location),
                "msg": str(error.get("msg", "Invalid value")),
            }
        )
    response = ErrorResponse(
        error=ErrorDetail(
            code="request_validation_error",
            message="Request validation failed",
            details={"issues": cast(JsonValue, issues)},
        ),
        request_id=_request_id(request),
    )
    return JSONResponse(status_code=422, content=response.model_dump(mode="json"))


async def handle_unexpected_error(request: Request, exc: Exception) -> JSONResponse:
    """记录意外故障并返回稳定且不含敏感信息的响应。"""
    request_id = _request_id(request)
    # 详细异常只写服务端日志, 客户端得到稳定通用消息和可关联 request_id。
    logger.error(
        "Unhandled application error",
        exc_info=exc,
        extra={"request_id": request_id, "path": request.url.path},
    )
    response = ErrorResponse(
        error=ErrorDetail(code="internal_error", message="Internal server error"),
        request_id=request_id,
    )
    return JSONResponse(status_code=500, content=response.model_dump(mode="json"))


def register_exception_handlers(app: FastAPI) -> None:
    """注册应用负责的全部异常处理器。"""
    app.add_exception_handler(IncidentCopilotError, handle_application_error)
    app.add_exception_handler(RequestValidationError, handle_request_validation_error)
    app.add_exception_handler(Exception, handle_unexpected_error)
