"""Tests for deterministic transformations used by the real-source demo."""

import asyncio
from datetime import UTC, datetime, timedelta
from typing import cast

import pytest

from incident_copilot.demo import shift_fixture_to_now, wait_for_metric
from incident_copilot.domain.common import SourceType
from incident_copilot.domain.evidence import Evidence
from incident_copilot.tools.providers import FixtureProvider
from incident_copilot.tools.providers.prometheus import PrometheusMetricsProvider
from incident_copilot.tools.schemas import QueryContext, QueryMetricsInput


def test_live_demo_shift_preserves_relative_times_and_citation_integrity() -> None:
    original = FixtureProvider.payment_service().fixture
    reference_end = datetime(2026, 7, 18, 12, 0, tzinfo=UTC)

    shifted = shift_fixture_to_now(original, reference_end)

    assert shifted.incident.end_time == reference_end
    assert shifted.incident.end_time - shifted.incident.start_time == timedelta(minutes=20)
    assert shifted.incident.incident_id.startswith("inc_payment_live_")
    assert [item.evidence_id for item in shifted.evidence] == [
        item.evidence_id for item in original.evidence
    ]
    for before, after in zip(original.evidence, shifted.evidence, strict=True):
        assert before.content_hash == after.content_hash
        assert after.citation.content_hash == after.content_hash
        if before.timestamp is not None:
            assert after.timestamp is not None
            assert after.timestamp - before.timestamp == (
                reference_end - original.incident.end_time
            )


@pytest.mark.asyncio
async def test_metric_readiness_requires_two_consecutive_successes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    metric = next(
        item
        for item in FixtureProvider.payment_service().fixture.evidence
        if item.source_type is SourceType.METRIC
    )

    class CountingProvider:
        def __init__(self) -> None:
            self.calls = 0

        async def query(
            self,
            query: QueryMetricsInput,
            context: QueryContext,
        ) -> tuple[Evidence, ...]:
            del query, context
            self.calls += 1
            return (metric,)

    async def no_sleep(delay: float) -> None:
        del delay

    provider = CountingProvider()
    monkeypatch.setattr(asyncio, "sleep", no_sleep)

    result = await wait_for_metric(
        cast(PrometheusMetricsProvider, provider),
        timeout_seconds=1,
    )

    assert result == (metric,)
    assert provider.calls == 2
