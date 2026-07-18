"""Default-off and explicit-enable tests for optional OpenTelemetry spans."""

from contextlib import AbstractContextManager
from types import TracebackType
from typing import Any

import pytest

from incident_copilot.core import telemetry


class RecordingSpan(AbstractContextManager[None]):
    def __init__(self) -> None:
        self.entered = False
        self.exited = False

    def __enter__(self) -> None:
        self.entered = True

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc_value: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        del exc_type, exc_value, traceback
        self.exited = True


class RecordingTracer:
    def __init__(self, span: RecordingSpan) -> None:
        self.span = span
        self.name: str | None = None
        self.attributes: dict[str, str] | None = None

    def start_as_current_span(self, name: str, *, attributes: dict[str, str]) -> RecordingSpan:
        self.name = name
        self.attributes = attributes
        return self.span


class RecordingTraceModule:
    def __init__(self, tracer: RecordingTracer) -> None:
        self.tracer = tracer

    def get_tracer(self, name: str, version: str) -> RecordingTracer:
        assert name == "incident_copilot"
        assert version == "0.1.0"
        return self.tracer


@pytest.mark.asyncio
async def test_disabled_telemetry_does_not_import_optional_dependency(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv(telemetry.OTEL_ENABLED_ENV, raising=False)

    def reject_import(name: str) -> Any:
        raise AssertionError(f"unexpected optional import: {name}")

    monkeypatch.setattr(telemetry, "import_module", reject_import)

    @telemetry.trace_async("test.disabled", component="test")
    async def operation(value: int) -> int:
        return value + 1

    assert await operation(1) == 2


@pytest.mark.asyncio
async def test_enabled_telemetry_emits_named_component_span(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv(telemetry.OTEL_ENABLED_ENV, "true")
    span = RecordingSpan()
    tracer = RecordingTracer(span)
    module = RecordingTraceModule(tracer)
    monkeypatch.setattr(telemetry, "import_module", lambda name: module)

    @telemetry.trace_async("test.enabled", component="model")
    async def operation() -> str:
        return "ok"

    assert await operation() == "ok"
    assert tracer.name == "test.enabled"
    assert tracer.attributes is not None
    assert tracer.attributes["incident_copilot.component"] == "model"
    assert span.entered is True
    assert span.exited is True


@pytest.mark.asyncio
async def test_explicit_enable_without_extra_fails_clearly(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv(telemetry.OTEL_ENABLED_ENV, "true")

    def missing_dependency(name: str) -> Any:
        del name
        raise ImportError

    monkeypatch.setattr(telemetry, "import_module", missing_dependency)

    @telemetry.trace_async("test.missing", component="tool")
    async def operation() -> None:
        return None

    with pytest.raises(RuntimeError, match="observability extra"):
        await operation()
