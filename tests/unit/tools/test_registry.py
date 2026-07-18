"""Failure, retry, validation, and registration tests for ToolRegistry."""

import asyncio
from collections.abc import Awaitable, Callable, Sequence
from datetime import UTC, datetime, timedelta

import pytest

from incident_copilot.domain.common import SourceType
from incident_copilot.domain.evidence import Evidence
from incident_copilot.tools.exceptions import (
    ProviderErrorCategory,
    ProviderInvalidQueryError,
    ProviderUnavailableError,
    ToolBudgetExceededError,
    ToolExecutionError,
    ToolInvalidArgumentsError,
    ToolNotFoundError,
    ToolRegistrationError,
)
from incident_copilot.tools.providers import FixtureProvider
from incident_copilot.tools.registry import ToolDefinition, ToolRegistry
from incident_copilot.tools.schemas import (
    GetServiceTopologyInput,
    QueryContext,
    SearchLogsInput,
    SearchSimilarIncidentsInput,
)


def make_context(*, remaining_tool_calls: int = 3, expired: bool = False) -> QueryContext:
    offset = timedelta(seconds=-1 if expired else 5)
    return QueryContext(
        correlation_id="registry-test",
        deadline=datetime.now(UTC) + offset,
        remaining_tool_calls=remaining_tool_calls,
    )


def make_definition(
    handler: Callable[[SearchLogsInput, QueryContext], Awaitable[Sequence[Evidence]]],
    *,
    name: str = "search_logs",
    timeout_seconds: float = 0.1,
    max_retries: int = 1,
) -> ToolDefinition[SearchLogsInput]:
    return ToolDefinition(
        name=name,
        input_model=SearchLogsInput,
        handler=handler,
        expected_sources=frozenset({SourceType.LOG}),
        timeout_seconds=timeout_seconds,
        max_retries=max_retries,
    )


def valid_arguments() -> dict[str, object]:
    return {
        "service": "payment-service",
        "start_time": "2026-07-18T10:20:00+08:00",
        "end_time": "2026-07-18T10:40:00+08:00",
        "limit": 5,
    }


def sample_log() -> Evidence:
    return FixtureProvider.payment_service().fixture.evidence[0]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "invalid_arguments",
    [
        {**valid_arguments(), "start_time": "2026-07-18T10:20:00"},
        {
            **valid_arguments(),
            "start_time": "2026-07-18T10:40:00+08:00",
            "end_time": "2026-07-18T10:20:00+08:00",
        },
        {
            **valid_arguments(),
            "start_time": "2026-07-17T09:00:00+08:00",
        },
        {**valid_arguments(), "limit": 0},
        {**valid_arguments(), "vendor_query": "unsafe raw syntax"},
    ],
)
async def test_registry_rejects_invalid_parameters_before_provider_call(
    invalid_arguments: dict[str, object],
) -> None:
    calls = 0

    async def handler(query: SearchLogsInput, context: QueryContext) -> Sequence[Evidence]:
        nonlocal calls
        del query, context
        calls += 1
        return (sample_log(),)

    registry = ToolRegistry(retry_backoff_seconds=0)
    registry.register(make_definition(handler))

    with pytest.raises(ToolInvalidArgumentsError, match="invalid arguments"):
        await registry.execute("search_logs", invalid_arguments, make_context())

    assert calls == 0


@pytest.mark.asyncio
async def test_registry_rejects_duplicate_unknown_invalid_and_exhausted_calls() -> None:
    calls = 0

    async def handler(query: SearchLogsInput, context: QueryContext) -> Sequence[Evidence]:
        nonlocal calls
        del query, context
        calls += 1
        return (sample_log(),)

    registry = ToolRegistry(retry_backoff_seconds=0)
    registry.register(make_definition(handler))

    with pytest.raises(ToolRegistrationError, match="already registered"):
        registry.register(make_definition(handler))
    with pytest.raises(ToolNotFoundError, match="unknown tool"):
        await registry.execute("delete_database", {}, make_context())
    with pytest.raises(ToolInvalidArgumentsError, match="invalid arguments"):
        await registry.execute(
            "search_logs",
            {**valid_arguments(), "service": "bad service!"},
            make_context(),
        )
    with pytest.raises(ToolBudgetExceededError, match="budget exhausted"):
        await registry.execute(
            "search_logs", valid_arguments(), make_context(remaining_tool_calls=0)
        )

    assert calls == 0


@pytest.mark.asyncio
async def test_registry_retries_transient_provider_failure_then_succeeds() -> None:
    calls = 0

    async def handler(query: SearchLogsInput, context: QueryContext) -> Sequence[Evidence]:
        nonlocal calls
        del query, context
        calls += 1
        if calls == 1:
            raise ProviderUnavailableError(
                "fixture temporarily unavailable",
                provider_name="test-provider",
                operation="search",
            )
        return (sample_log(),)

    registry = ToolRegistry(retry_backoff_seconds=0)
    registry.register(make_definition(handler))

    result = await registry.execute("search_logs", valid_arguments(), make_context())

    assert result.attempts == 2
    assert result.evidence[0].evidence_id == "ev_payment_log_pool_timeout"
    assert calls == 2


@pytest.mark.asyncio
async def test_registry_never_retries_past_callers_remaining_budget() -> None:
    calls = 0

    async def handler(query: SearchLogsInput, context: QueryContext) -> Sequence[Evidence]:
        nonlocal calls
        del query, context
        calls += 1
        raise ProviderUnavailableError(
            "temporary outage",
            provider_name="bounded-provider",
            operation="search",
        )

    registry = ToolRegistry(retry_backoff_seconds=0)
    registry.register(make_definition(handler, max_retries=3))

    with pytest.raises(ToolExecutionError) as captured:
        await registry.execute(
            "search_logs",
            valid_arguments(),
            make_context(remaining_tool_calls=1),
        )

    assert captured.value.attempts == 1
    assert calls == 1


@pytest.mark.asyncio
async def test_registry_does_not_retry_permanent_provider_failure() -> None:
    calls = 0

    async def handler(query: SearchLogsInput, context: QueryContext) -> Sequence[Evidence]:
        nonlocal calls
        del query, context
        calls += 1
        raise ProviderInvalidQueryError(
            "unsupported provider filter",
            provider_name="test-provider",
            operation="search",
        )

    registry = ToolRegistry(retry_backoff_seconds=0)
    registry.register(make_definition(handler, max_retries=3))

    with pytest.raises(ToolExecutionError) as captured:
        await registry.execute("search_logs", valid_arguments(), make_context())

    assert captured.value.category is ProviderErrorCategory.INVALID_QUERY
    assert captured.value.attempts == 1
    assert captured.value.retryable is False
    assert calls == 1


@pytest.mark.asyncio
async def test_registry_bounds_timeout_retries() -> None:
    calls = 0

    async def handler(query: SearchLogsInput, context: QueryContext) -> Sequence[Evidence]:
        nonlocal calls
        del query, context
        calls += 1
        await asyncio.sleep(0.05)
        return (sample_log(),)

    registry = ToolRegistry(retry_backoff_seconds=0)
    registry.register(make_definition(handler, timeout_seconds=0.005, max_retries=1))

    with pytest.raises(ToolExecutionError) as captured:
        await registry.execute("search_logs", valid_arguments(), make_context())

    assert captured.value.category is ProviderErrorCategory.TIMEOUT
    assert captured.value.attempts == 2
    assert captured.value.retryable is True
    assert calls == 2


@pytest.mark.asyncio
async def test_provider_failure_does_not_poison_another_registered_tool() -> None:
    async def failing(query: SearchLogsInput, context: QueryContext) -> Sequence[Evidence]:
        del query, context
        raise ProviderUnavailableError(
            "offline",
            provider_name="failed-provider",
            operation="search",
        )

    async def healthy(query: SearchLogsInput, context: QueryContext) -> Sequence[Evidence]:
        del query, context
        return (sample_log(),)

    registry = ToolRegistry(retry_backoff_seconds=0)
    registry.register(make_definition(failing, name="failing_logs", max_retries=0))
    registry.register(make_definition(healthy, name="healthy_logs"))

    with pytest.raises(ToolExecutionError) as captured:
        await registry.execute("failing_logs", valid_arguments(), make_context())
    healthy_result = await registry.execute("healthy_logs", valid_arguments(), make_context())

    assert captured.value.category is ProviderErrorCategory.UNAVAILABLE
    assert healthy_result.evidence[0].source_type is SourceType.LOG


@pytest.mark.asyncio
async def test_registry_rejects_evidence_without_required_provenance() -> None:
    async def handler(query: SearchLogsInput, context: QueryContext) -> Sequence[Evidence]:
        del query, context
        return (sample_log().model_copy(update={"service": None, "timestamp": None}),)

    registry = ToolRegistry(retry_backoff_seconds=0)
    registry.register(make_definition(handler))

    with pytest.raises(ToolExecutionError) as captured:
        await registry.execute("search_logs", valid_arguments(), make_context())

    assert captured.value.category is ProviderErrorCategory.MALFORMED_RESPONSE
    assert captured.value.attempts == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("violation", ["service", "time", "limit"])
async def test_registry_rejects_provider_results_outside_request_scope(
    violation: str,
) -> None:
    evidence = sample_log()
    returned: tuple[Evidence, ...]
    if violation == "service":
        returned = (evidence.model_copy(update={"service": "payment-gateway"}),)
    elif violation == "time":
        returned = (
            evidence.model_copy(update={"timestamp": datetime(2026, 7, 19, 10, 0, tzinfo=UTC)}),
        )
    else:
        returned = (evidence, evidence)

    async def handler(query: SearchLogsInput, context: QueryContext) -> Sequence[Evidence]:
        del query, context
        return returned

    registry = ToolRegistry(retry_backoff_seconds=0)
    registry.register(make_definition(handler))

    with pytest.raises(ToolExecutionError) as captured:
        await registry.execute(
            "search_logs",
            {**valid_arguments(), "limit": 1},
            make_context(),
        )

    assert captured.value.category is ProviderErrorCategory.MALFORMED_RESPONSE
    assert captured.value.attempts == 1


@pytest.mark.asyncio
async def test_registry_rejects_topology_and_incident_specific_time_scope() -> None:
    fixture = FixtureProvider.payment_service().fixture.evidence
    topology = next(item for item in fixture if item.source_type is SourceType.TOPOLOGY)
    incident = next(
        item
        for item in fixture
        if item.source_type is SourceType.KNOWLEDGE
        and item.metadata.get("knowledge_kind") == "incident"
    )

    async def topology_handler(
        query: GetServiceTopologyInput, context: QueryContext
    ) -> Sequence[Evidence]:
        del query, context
        return (
            topology.model_copy(update={"timestamp": datetime(2026, 7, 19, 10, 0, tzinfo=UTC)}),
        )

    async def incident_handler(
        query: SearchSimilarIncidentsInput, context: QueryContext
    ) -> Sequence[Evidence]:
        del query, context
        return (
            incident.model_copy(update={"timestamp": datetime(2026, 7, 18, 10, 0, tzinfo=UTC)}),
        )

    topology_registry = ToolRegistry(retry_backoff_seconds=0)
    topology_registry.register(
        ToolDefinition(
            name="get_service_topology",
            input_model=GetServiceTopologyInput,
            handler=topology_handler,
            expected_sources=frozenset({SourceType.TOPOLOGY}),
            max_retries=0,
        )
    )
    incident_registry = ToolRegistry(retry_backoff_seconds=0)
    incident_registry.register(
        ToolDefinition(
            name="search_similar_incidents",
            input_model=SearchSimilarIncidentsInput,
            handler=incident_handler,
            expected_sources=frozenset({SourceType.KNOWLEDGE}),
            max_retries=0,
        )
    )

    with pytest.raises(ToolExecutionError) as topology_error:
        await topology_registry.execute(
            "get_service_topology",
            {
                "service": "payment-service",
                "at_time": "2026-07-18T10:30:00+08:00",
            },
            make_context(),
        )
    with pytest.raises(ToolExecutionError) as incident_error:
        await incident_registry.execute(
            "search_similar_incidents",
            {
                "service": "payment-service",
                "query": "connection pool incident",
                "before_time": "2026-07-18T10:00:00+08:00",
                "lookback_days": 90,
            },
            make_context(),
        )

    assert topology_error.value.category is ProviderErrorCategory.MALFORMED_RESPONSE
    assert incident_error.value.category is ProviderErrorCategory.MALFORMED_RESPONSE


@pytest.mark.asyncio
async def test_expired_deadline_fails_without_calling_provider() -> None:
    calls = 0

    async def handler(query: SearchLogsInput, context: QueryContext) -> Sequence[Evidence]:
        nonlocal calls
        del query, context
        calls += 1
        return (sample_log(),)

    registry = ToolRegistry(retry_backoff_seconds=0)
    registry.register(make_definition(handler))

    with pytest.raises(ToolExecutionError) as captured:
        await registry.execute("search_logs", valid_arguments(), make_context(expired=True))

    assert captured.value.category is ProviderErrorCategory.TIMEOUT
    assert captured.value.attempts == 1
    assert calls == 0
