"""Checkpoint and interrupt behavior of the Phase 5 investigation graph."""

from datetime import UTC, datetime
from typing import Any

import pytest
from langchain_core.runnables import RunnableConfig
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.types import Command

from incident_copilot.domain.common import SourceType
from incident_copilot.domain.review import ReviewAction
from incident_copilot.graph.bootstrap import build_offline_investigation_graph
from incident_copilot.graph.builder import create_initial_state
from incident_copilot.tools.providers.fixture import FixtureProvider

TEST_NOW = datetime(2026, 7, 18, 12, 0, tzinfo=UTC)


def fixed_clock() -> datetime:
    return TEST_NOW


@pytest.mark.asyncio
async def test_high_risk_report_pauses_and_resumes_on_the_same_thread() -> None:
    graph = build_offline_investigation_graph(
        clock=fixed_clock,
        checkpointer=InMemorySaver(),
        require_human_review=True,
    )
    config: RunnableConfig = {"configurable": {"thread_id": "thread_pause_accept"}}

    await graph.ainvoke(
        create_initial_state(
            FixtureProvider.payment_service().fixture.incident,
            clock=fixed_clock,
        ),
        config,
    )

    paused = await graph.aget_state(config)
    assert paused.next == ("human_review",)
    assert paused.tasks[0].interrupts[0].value["report_id"].startswith("rpt_")
    assert paused.tasks[0].interrupts[0].value["high_risk_actions"]

    accept: Command[Any] = Command(resume={"action": ReviewAction.ACCEPT.value})
    completed = await graph.ainvoke(accept, config)

    assert completed["review_completed"] is True
    assert completed["human_feedback"].action is ReviewAction.ACCEPT
    assert (await graph.aget_state(config)).next == ()


@pytest.mark.asyncio
async def test_additional_research_resumes_then_pauses_for_final_confirmation() -> None:
    graph = build_offline_investigation_graph(
        clock=fixed_clock,
        checkpointer=InMemorySaver(),
        require_human_review=True,
    )
    config: RunnableConfig = {"configurable": {"thread_id": "thread_more_research"}}
    initial = create_initial_state(
        FixtureProvider.payment_service().fixture.incident,
        clock=fixed_clock,
    )
    await graph.ainvoke(initial, config)

    request_more: Command[Any] = Command(
        resume={
            "action": ReviewAction.REQUEST_MORE_RESEARCH.value,
            "comment": "Verify the database saturation signal.",
            "requested_queries": [
                {
                    "query": "database connection saturation",
                    "source_types": [SourceType.METRIC.value],
                    "service": "payment-service",
                }
            ],
        }
    )
    await graph.ainvoke(request_more, config)

    paused_again = await graph.aget_state(config)
    assert paused_again.next == ("human_review",)
    assert paused_again.values["research_round"] == 2
    assert paused_again.values["human_feedback"].action is ReviewAction.REQUEST_MORE_RESEARCH

    accept: Command[Any] = Command(resume={"action": ReviewAction.ACCEPT.value})
    completed = await graph.ainvoke(accept, config)
    assert completed["review_completed"] is True
