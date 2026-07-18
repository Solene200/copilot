"""Application lifecycle tests over a real checkpointed offline graph."""

from datetime import UTC, datetime

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
