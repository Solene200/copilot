"""默认关闭的 Graph、工具和模型边界 OpenTelemetry Span。"""

import os
from collections.abc import Awaitable, Callable
from contextlib import nullcontext
from functools import wraps
from importlib import import_module
from typing import ParamSpec, TypeVar

ParamT = ParamSpec("ParamT")
ReturnT = TypeVar("ReturnT")

OTEL_ENABLED_ENV = "INCIDENT_COPILOT_OTEL_ENABLED"


def telemetry_enabled() -> bool:
    """要求显式启用开关,导入应用不会自动开启遥测导出。"""
    return os.getenv(OTEL_ENABLED_ENV, "").strip().casefold() in {"1", "true", "yes", "on"}


def trace_async(
    name: str, *, component: str
) -> Callable[[Callable[ParamT, Awaitable[ReturnT]]], Callable[ParamT, Awaitable[ReturnT]]]:
    """仅在启用可选 OTel 集成时追踪异步边界。"""

    def decorator(
        function: Callable[ParamT, Awaitable[ReturnT]],
    ) -> Callable[ParamT, Awaitable[ReturnT]]:
        @wraps(function)
        async def wrapped(*args: ParamT.args, **kwargs: ParamT.kwargs) -> ReturnT:
            context = nullcontext()
            if telemetry_enabled():
                try:
                    trace = import_module("opentelemetry.trace")
                except ImportError:
                    raise RuntimeError(
                        "OpenTelemetry is enabled but the observability extra is not installed"
                    ) from None
                tracer = trace.get_tracer("incident_copilot", "0.1.0")
                context = tracer.start_as_current_span(
                    name,
                    attributes={
                        "incident_copilot.component": component,
                        "code.function.name": function.__qualname__,
                    },
                )
            with context:
                return await function(*args, **kwargs)

        return wrapped

    return decorator
