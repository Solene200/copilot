"""Offline structured model contract tests."""

from datetime import UTC, datetime

import pytest

from incident_copilot.graph.model import FakeModelProvider
from incident_copilot.graph.schemas import ModelContext, ModelTask, PlanOutput


@pytest.mark.asyncio
async def test_fake_model_returns_schema_valid_plan_without_online_api() -> None:
    context = ModelContext(
        task=ModelTask.PLAN,
        incident_id="inc_test",
        service="payment-service",
        raw_query="payment requests timed out",
        start_time=datetime(2026, 7, 18, 2, 20, tzinfo=UTC),
        end_time=datetime(2026, 7, 18, 2, 40, tzinfo=UTC),
        research_round=1,
    )

    response = await FakeModelProvider().complete(context)
    output = PlanOutput.model_validate(response.payload)

    assert len(output.steps) == 7
    assert len({step.query_key for step in output.steps}) == 7
    assert response.usage.estimated is True
