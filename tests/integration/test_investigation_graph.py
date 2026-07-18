"""End-to-end Phase 4 graph behavior over deterministic local providers."""

import asyncio
from datetime import UTC, datetime

import pytest

from incident_copilot.domain.evidence import Evidence
from incident_copilot.domain.incident import IncidentContext
from incident_copilot.graph.bootstrap import build_offline_investigation_graph
from incident_copilot.graph.builder import build_investigation_graph, create_initial_state
from incident_copilot.graph.model import FakeModelProvider
from incident_copilot.graph.schemas import (
    InvestigationOptions,
    ModelContext,
    ModelResponse,
    StopReason,
)
from incident_copilot.tools.builtin import ProviderBundle, build_tool_registry
from incident_copilot.tools.exceptions import ProviderUnavailableError
from incident_copilot.tools.providers.fixture import FixtureProvider
from incident_copilot.tools.schemas import (
    GetRecentChangesInput,
    GetServiceTopologyInput,
    QueryContext,
    QueryMetricsInput,
    QueryTracesInput,
    SearchLogsInput,
    SearchRunbooksInput,
    SearchSimilarIncidentsInput,
)

TEST_NOW = datetime.now(UTC)


def fixed_clock() -> datetime:
    return TEST_NOW


def fixture_incident() -> IncidentContext:
    return FixtureProvider.payment_service().fixture.incident


@pytest.mark.asyncio
async def test_fixture_graph_generates_citable_evidence_report() -> None:
    graph = build_offline_investigation_graph(clock=fixed_clock)

    state = await graph.ainvoke(create_initial_state(fixture_incident(), clock=fixed_clock))

    report = state["final_report"]
    state_ids = {item.evidence_id for item in state["evidence"]}
    report_ids = {item.evidence_id for item in report.supporting_evidence}
    assert state["stop_reason"] is StopReason.EVIDENCE_SUFFICIENT
    assert len({item.source_type for item in state["evidence"]}) >= 2
    assert report_ids
    assert report_ids <= state_ids
    assert {item.citation_id for item in report.citations} == {
        item.citation.citation_id for item in report.supporting_evidence
    }
    assert all(set(item.evidence_ids) <= state_ids for item in report.timeline)


@pytest.mark.asyncio
async def test_graph_runs_a_second_investigation_round_without_repeating_queries() -> None:
    graph = build_offline_investigation_graph(
        model=FakeModelProvider(minimum_research_rounds=2),
        clock=fixed_clock,
    )

    state = await graph.ainvoke(create_initial_state(fixture_incident(), clock=fixed_clock))

    query_keys = [item.query_key for item in state["completed_steps"]]
    assert state["research_round"] == 2
    assert state["stop_reason"] is StopReason.EVIDENCE_SUFFICIENT
    assert state["tool_call_count"] == 10
    assert len(query_keys) == len(set(query_keys)) == 10


@pytest.mark.asyncio
async def test_graph_stops_exactly_at_maximum_rounds() -> None:
    graph = build_offline_investigation_graph(
        model=FakeModelProvider(minimum_research_rounds=5),
        clock=fixed_clock,
    )
    initial = create_initial_state(
        fixture_incident(),
        options=InvestigationOptions(max_research_rounds=2),
        clock=fixed_clock,
    )

    state = await graph.ainvoke(initial)

    assert state["research_round"] == 2
    assert state["stop_reason"] is StopReason.MAX_RESEARCH_ROUNDS
    assert state["tool_call_count"] == 10
    assert state["final_report"].investigation_stats.research_rounds == 2


@pytest.mark.asyncio
async def test_tool_budget_is_reserved_before_parallel_dispatch() -> None:
    graph = build_offline_investigation_graph(clock=fixed_clock)
    initial = create_initial_state(
        fixture_incident(),
        options=InvestigationOptions(max_tool_calls=3),
        clock=fixed_clock,
    )

    state = await graph.ainvoke(initial)

    assert state["tool_call_count"] == 3
    assert len(state["completed_steps"]) == 3
    assert state["stop_reason"] is StopReason.TOOL_BUDGET_EXHAUSTED


@pytest.mark.asyncio
async def test_model_budget_stops_additional_provider_calls() -> None:
    graph = build_offline_investigation_graph(clock=fixed_clock)
    initial = create_initial_state(
        fixture_incident(),
        options=InvestigationOptions(max_model_calls=2),
        clock=fixed_clock,
    )

    state = await graph.ainvoke(initial)

    assert state["model_call_count"] == 2
    assert state["stop_reason"] is StopReason.MODEL_BUDGET_EXHAUSTED
    assert state["final_report"].investigation_stats.model_call_count == 2


class InvalidStructuredModel:
    """Return JSON that fails every task-specific Pydantic Schema."""

    async def complete(self, context: ModelContext) -> ModelResponse:
        del context
        return ModelResponse(payload={"unexpected": "value"})


@pytest.mark.asyncio
async def test_invalid_structured_model_output_retries_then_degrades() -> None:
    graph = build_offline_investigation_graph(
        model=InvalidStructuredModel(),
        clock=fixed_clock,
    )

    state = await graph.ainvoke(create_initial_state(fixture_incident(), clock=fixed_clock))

    assert state["final_report"].supporting_evidence
    assert state["model_call_count"] == 8
    assert len(state["errors"]) == 8
    assert all(error.component == "model-provider" for error in state["errors"])


class FailingChanges:
    """One failing provider used to prove sibling branch degradation."""

    async def recent(
        self, query: GetRecentChangesInput, context: QueryContext
    ) -> tuple[Evidence, ...]:
        del query, context
        raise ProviderUnavailableError(
            "fixture change backend unavailable",
            provider_name="failing-changes",
            operation="recent",
        )


@pytest.mark.asyncio
async def test_one_provider_failure_preserves_other_evidence_and_reports_gap() -> None:
    fixture = FixtureProvider.payment_service()
    registry = build_tool_registry(
        ProviderBundle(
            logs=fixture,
            metrics=fixture,
            traces=fixture,
            changes=FailingChanges(),
            topology=fixture,
            knowledge=fixture,
        ),
        max_retries=0,
        retry_backoff_seconds=0,
    )
    graph = build_investigation_graph(
        registry=registry,
        model=FakeModelProvider(),
        clock=fixed_clock,
    )

    state = await graph.ainvoke(create_initial_state(fixture_incident(), clock=fixed_clock))

    assert state["tool_failure_count"] == 1
    assert state["tool_success_count"] == 6
    assert len(state["errors"]) == 1
    assert state["final_report"].supporting_evidence
    assert any("error" in item for item in state["final_report"].limitations)


class ParallelBarrierProvider:
    """Delegate only after every initial Send branch has started."""

    def __init__(self) -> None:
        self._fixture = FixtureProvider.payment_service()
        self._lock = asyncio.Lock()
        self._release = asyncio.Event()
        self.arrivals = 0

    async def _arrive(self) -> None:
        async with self._lock:
            self.arrivals += 1
            if self.arrivals == 7:
                self._release.set()
        await self._release.wait()

    async def search(self, query: SearchLogsInput, context: QueryContext) -> tuple[Evidence, ...]:
        await self._arrive()
        return await self._fixture.search(query, context)

    async def query(
        self, query: QueryMetricsInput | QueryTracesInput, context: QueryContext
    ) -> tuple[Evidence, ...]:
        await self._arrive()
        return await self._fixture.query(query, context)

    async def recent(
        self, query: GetRecentChangesInput, context: QueryContext
    ) -> tuple[Evidence, ...]:
        await self._arrive()
        return await self._fixture.recent(query, context)

    async def get(
        self, query: GetServiceTopologyInput, context: QueryContext
    ) -> tuple[Evidence, ...]:
        await self._arrive()
        return await self._fixture.get(query, context)

    async def search_runbooks(
        self, query: SearchRunbooksInput, context: QueryContext
    ) -> tuple[Evidence, ...]:
        await self._arrive()
        return await self._fixture.search_runbooks(query, context)

    async def search_similar_incidents(
        self, query: SearchSimilarIncidentsInput, context: QueryContext
    ) -> tuple[Evidence, ...]:
        await self._arrive()
        return await self._fixture.search_similar_incidents(query, context)


@pytest.mark.asyncio
async def test_send_dispatch_is_truly_parallel_via_async_barrier() -> None:
    provider = ParallelBarrierProvider()
    registry = build_tool_registry(
        ProviderBundle(
            logs=provider,
            metrics=provider,
            traces=provider,
            changes=provider,
            topology=provider,
            knowledge=provider,
        ),
        timeout_seconds=1,
        max_retries=0,
        retry_backoff_seconds=0,
    )
    graph = build_investigation_graph(
        registry=registry,
        model=FakeModelProvider(),
        clock=fixed_clock,
    )

    state = await graph.ainvoke(create_initial_state(fixture_incident(), clock=fixed_clock))

    assert provider.arrivals == 7
    assert state["tool_success_count"] == 7
    assert state["tool_failure_count"] == 0
