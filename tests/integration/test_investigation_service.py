"""Application lifecycle tests over a real checkpointed offline graph."""

import asyncio
from collections.abc import AsyncIterator
from datetime import UTC, datetime
from types import SimpleNamespace
from typing import Any, cast

import pytest
from langgraph.checkpoint.memory import InMemorySaver

from incident_copilot.core.exceptions import ResourceConflictError
from incident_copilot.domain.common import SourceType
from incident_copilot.domain.hypothesis import VerificationQuery
from incident_copilot.domain.review import HumanFeedback, ReviewAction
from incident_copilot.graph.bootstrap import build_offline_investigation_graph
from incident_copilot.graph.schemas import InvestigationOptions
from incident_copilot.investigations.models import EventType, InvestigationStatus
from incident_copilot.investigations.repository import InMemoryInvestigationRepository
from incident_copilot.investigations.service import InvestigationService
from incident_copilot.tools.providers.fixture import FixtureProvider

TEST_NOW = datetime(2026, 7, 18, 12, 0, tzinfo=UTC)


def fixed_clock() -> datetime:
    return TEST_NOW


def build_service() -> InvestigationService:
    return InvestigationService(
        graph=build_offline_investigation_graph(
            clock=fixed_clock,
            checkpointer=InMemorySaver(),
            require_human_review=True,
        ),
        repository=InMemoryInvestigationRepository(),
        clock=fixed_clock,
    )


def build_service_with_saver(
    saver: InMemorySaver,
    repository: InMemoryInvestigationRepository,
) -> InvestigationService:
    return InvestigationService(
        graph=build_offline_investigation_graph(
            clock=fixed_clock,
            checkpointer=saver,
            require_human_review=True,
        ),
        repository=repository,
        clock=fixed_clock,
    )


class MissingReportGraph:
    """Minimal graph double that terminates without satisfying the report contract."""

    async def astream(self, *args: object, **kwargs: object) -> AsyncIterator[dict[str, object]]:
        del args
        if kwargs.get("emit_test_update"):
            yield {}

    async def aget_state(self, *args: object, **kwargs: object) -> SimpleNamespace:
        del args, kwargs
        return SimpleNamespace(values={}, tasks=(), next=())


class FailFirstUpdateRepository(InMemoryInvestigationRepository):
    """Fail once before graph execution so initialization recovery is exercised."""

    def __init__(self) -> None:
        super().__init__()
        self.update_calls = 0

    async def update(self, *args: Any, **kwargs: Any) -> Any:
        self.update_calls += 1
        if self.update_calls == 1:
            raise RuntimeError("simulated initialization failure")
        return await super().update(*args, **kwargs)


class BlockingGraph:
    """Graph double that exposes whether shutdown cancels an active stream."""

    def __init__(self) -> None:
        self.started = asyncio.Event()
        self.cancelled = asyncio.Event()
        self.release = asyncio.Event()

    async def astream(self, *args: object, **kwargs: object) -> AsyncIterator[dict[str, object]]:
        del args
        self.started.set()
        try:
            await self.release.wait()
        finally:
            self.cancelled.set()
        if kwargs.get("emit_test_update"):
            yield {}


@pytest.mark.asyncio
async def test_service_pauses_streams_and_accepts_exactly_once() -> None:
    service = build_service()
    record, created = await service.create(
        incident=FixtureProvider.payment_service().fixture.incident,
        options=InvestigationOptions(),
        request_fingerprint="a" * 64,
        idempotency_key="create-once",
    )

    paused = await service.wait_until_quiescent(record.investigation_id)
    events = await service.repository.list_events(record.investigation_id)

    assert created is True
    assert paused.status is InvestigationStatus.WAITING_REVIEW
    assert paused.review_request is not None
    assert [event.sequence for event in events] == list(range(1, len(events) + 1))
    assert EventType.EVIDENCE_ADDED in {event.event_type for event in events}
    assert events[-1].event_type is EventType.REVIEW_REQUIRED
    assert all(event.thread_id == paused.thread_id for event in events)

    await service.resume(
        record.investigation_id,
        HumanFeedback(action=ReviewAction.ACCEPT),
    )
    completed = await service.wait_until_quiescent(record.investigation_id)

    assert completed.status is InvestigationStatus.COMPLETED
    assert completed.report is not None
    assert completed.run_id != record.run_id
    with pytest.raises(ResourceConflictError):
        await service.resume(
            record.investigation_id,
            HumanFeedback(action=ReviewAction.ACCEPT),
        )


@pytest.mark.asyncio
async def test_service_can_request_more_research_and_pause_again() -> None:
    service = build_service()
    record, _ = await service.create(
        incident=FixtureProvider.payment_service().fixture.incident,
        options=InvestigationOptions(max_research_rounds=2),
        request_fingerprint="b" * 64,
        idempotency_key=None,
    )
    first_pause = await service.wait_until_quiescent(record.investigation_id)

    await service.resume(
        record.investigation_id,
        HumanFeedback(
            action=ReviewAction.REQUEST_MORE_RESEARCH,
            requested_queries=(
                VerificationQuery(
                    query="verify database saturation",
                    source_types=(SourceType.METRIC,),
                    service="payment-service",
                ),
            ),
        ),
    )
    second_pause = await service.wait_until_quiescent(record.investigation_id)
    events = await service.repository.list_events(record.investigation_id)

    assert second_pause.status is InvestigationStatus.WAITING_REVIEW
    assert second_pause.run_id != first_pause.run_id
    assert sum(event.event_type is EventType.REVIEW_REQUIRED for event in events) == 2


@pytest.mark.asyncio
async def test_service_rejects_more_research_when_round_budget_is_exhausted() -> None:
    service = build_service()
    record, _ = await service.create(
        incident=FixtureProvider.payment_service().fixture.incident,
        options=InvestigationOptions(max_research_rounds=1),
        request_fingerprint="c" * 64,
        idempotency_key=None,
    )
    await service.wait_until_quiescent(record.investigation_id)

    with pytest.raises(ResourceConflictError, match="No investigation budget"):
        await service.resume(
            record.investigation_id,
            HumanFeedback(
                action=ReviewAction.REQUEST_MORE_RESEARCH,
                requested_queries=(
                    VerificationQuery(
                        query="one more query",
                        source_types=(SourceType.LOG,),
                    ),
                ),
            ),
        )


@pytest.mark.asyncio
async def test_service_creation_is_idempotent_and_detects_payload_conflicts() -> None:
    service = build_service()
    incident = FixtureProvider.payment_service().fixture.incident
    options = InvestigationOptions()
    first, first_created = await service.create(
        incident=incident,
        options=options,
        request_fingerprint="d" * 64,
        idempotency_key="stable-request",
    )
    second, second_created = await service.create(
        incident=incident,
        options=options,
        request_fingerprint="d" * 64,
        idempotency_key="stable-request",
    )

    assert first_created is True
    assert second_created is False
    assert second.investigation_id == first.investigation_id

    with pytest.raises(ResourceConflictError):
        await service.create(
            incident=incident,
            options=options,
            request_fingerprint="e" * 64,
            idempotency_key="stable-request",
        )


@pytest.mark.asyncio
async def test_rebuilt_service_recovers_paused_metadata_from_thread_checkpoint() -> None:
    saver = InMemorySaver()
    original = build_service_with_saver(saver, InMemoryInvestigationRepository())
    record, _ = await original.create(
        incident=FixtureProvider.payment_service().fixture.incident,
        options=InvestigationOptions(),
        request_fingerprint="f" * 64,
        idempotency_key=None,
    )
    await original.wait_until_quiescent(record.investigation_id)

    rebuilt = build_service_with_saver(saver, InMemoryInvestigationRepository())
    recovered = await rebuilt.get(record.investigation_id)

    assert recovered.status is InvestigationStatus.WAITING_REVIEW
    assert recovered.thread_id == record.thread_id
    assert recovered.report is not None
    await rebuilt.resume(
        record.investigation_id,
        HumanFeedback(action=ReviewAction.ACCEPT),
    )
    completed = await rebuilt.wait_until_quiescent(record.investigation_id)
    assert completed.status is InvestigationStatus.COMPLETED


@pytest.mark.asyncio
async def test_graph_without_final_report_is_failed_not_falsely_completed() -> None:
    service = InvestigationService(
        graph=cast(Any, MissingReportGraph()),
        repository=InMemoryInvestigationRepository(),
        clock=fixed_clock,
    )
    record, _ = await service.create(
        incident=FixtureProvider.payment_service().fixture.incident,
        options=InvestigationOptions(),
        request_fingerprint="1" * 64,
        idempotency_key=None,
    )

    failed = await service.wait_until_quiescent(record.investigation_id)
    events = await service.repository.list_events(record.investigation_id)

    assert failed.status is InvestigationStatus.FAILED
    assert failed.report is None
    assert events[-1].event_type is EventType.INVESTIGATION_FAILED


@pytest.mark.asyncio
async def test_initialization_failure_reaches_explicit_failed_state() -> None:
    repository = FailFirstUpdateRepository()
    service = InvestigationService(
        graph=build_offline_investigation_graph(
            clock=fixed_clock,
            checkpointer=InMemorySaver(),
            require_human_review=True,
        ),
        repository=repository,
        clock=fixed_clock,
    )
    record, _ = await service.create(
        incident=FixtureProvider.payment_service().fixture.incident,
        options=InvestigationOptions(),
        request_fingerprint="2" * 64,
        idempotency_key=None,
    )

    failed = await service.wait_until_quiescent(record.investigation_id)

    assert failed.status is InvestigationStatus.FAILED
    assert repository.update_calls == 2


@pytest.mark.asyncio
async def test_service_close_cancels_and_observes_active_graph_tasks() -> None:
    graph = BlockingGraph()
    service = InvestigationService(
        graph=cast(Any, graph),
        repository=InMemoryInvestigationRepository(),
        clock=fixed_clock,
    )
    record, _ = await service.create(
        incident=FixtureProvider.payment_service().fixture.incident,
        options=InvestigationOptions(),
        request_fingerprint="3" * 64,
        idempotency_key=None,
    )
    await asyncio.wait_for(graph.started.wait(), timeout=1)

    await service.aclose()

    assert graph.cancelled.is_set()
    assert not any(
        task.get_name() == f"investigation:{record.investigation_id}"
        for task in asyncio.all_tasks()
    )
