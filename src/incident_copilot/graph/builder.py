"""LangGraph builder and Send-based dynamic evidence dispatch."""

from collections.abc import Callable
from datetime import datetime, timedelta

from langgraph.graph import END, START, StateGraph
from langgraph.graph.state import CompiledStateGraph
from langgraph.types import Send

from incident_copilot.domain.incident import IncidentContext
from incident_copilot.graph.model import ModelProvider
from incident_copilot.graph.nodes import InvestigationNodes, utc_now
from incident_copilot.graph.routing import route_after_judge
from incident_copilot.graph.schemas import InvestigationOptions, ModelUsage
from incident_copilot.graph.state import InvestigationState
from incident_copilot.tools.registry import ToolRegistry

InvestigationGraph = CompiledStateGraph[
    InvestigationState, None, InvestigationState, InvestigationState
]


def create_initial_state(
    incident: IncidentContext,
    *,
    options: InvestigationOptions | None = None,
    clock: Callable[[], datetime] = utc_now,
) -> InvestigationState:
    """Create one fully bounded invocation state from validated application input."""
    if not incident.services:
        raise ValueError("investigation requires at least one service")
    policy = options or InvestigationOptions()
    started_at = clock()
    return InvestigationState(
        incident=incident,
        pending_steps=(),
        completed_steps=(),
        evidence=(),
        hypotheses=(),
        evidence_sufficient=False,
        sufficiency_reason="not yet evaluated",
        next_investigation_queries=(),
        research_round=1,
        max_research_rounds=policy.max_research_rounds,
        max_tool_calls=policy.max_tool_calls,
        max_parallel_tools=policy.max_parallel_tools,
        tool_call_count=0,
        tool_success_count=0,
        tool_failure_count=0,
        max_model_calls=policy.max_model_calls,
        model_call_count=0,
        model_usage=ModelUsage(),
        started_at=started_at,
        deadline_at=started_at + timedelta(seconds=policy.timeout_seconds),
        deadline_exceeded=False,
        errors=(),
    )


def dispatch_evidence_collection(
    state: InvestigationState,
) -> list[Send] | str:
    """Reserve budget before fan-out and send minimal per-step state in parallel."""
    remaining = max(0, state["max_tool_calls"] - state.get("tool_call_count", 0))
    limit = min(remaining, state["max_parallel_tools"])
    completed_queries = {item.query_key for item in state.get("completed_steps", ())}
    candidates = sorted(
        (
            step
            for step in state.get("pending_steps", ())
            if step.query_key not in completed_queries
        ),
        key=lambda step: (-step.priority, step.step_id),
    )
    selected = candidates[:limit]
    if not selected:
        return "aggregate_evidence"
    return [
        Send(
            "collect_evidence",
            {
                "incident": state["incident"],
                "current_step": step,
                "deadline_at": state["deadline_at"],
            },
        )
        for step in selected
    ]


def build_investigation_graph(
    *,
    registry: ToolRegistry,
    model: ModelProvider,
    clock: Callable[[], datetime] = utc_now,
) -> InvestigationGraph:
    """Compile the exact Phase 4 graph without checkpoint or HITL concerns."""
    nodes = InvestigationNodes(registry=registry, model=model, clock=clock)
    builder = StateGraph(InvestigationState)
    builder.add_node("parse_incident", nodes.parse_incident)
    builder.add_node("build_investigation_plan", nodes.build_investigation_plan)
    builder.add_node("collect_evidence", nodes.collect_evidence)
    builder.add_node("aggregate_evidence", nodes.aggregate_evidence)
    builder.add_node("generate_hypotheses", nodes.generate_hypotheses)
    builder.add_node("verify_hypotheses", nodes.verify_hypotheses)
    builder.add_node("judge_evidence", nodes.judge_evidence)
    builder.add_node("refine_investigation", nodes.refine_investigation)
    builder.add_node("generate_report", nodes.generate_report)

    builder.add_edge(START, "parse_incident")
    builder.add_edge("parse_incident", "build_investigation_plan")
    builder.add_conditional_edges("build_investigation_plan", dispatch_evidence_collection)
    builder.add_edge("collect_evidence", "aggregate_evidence")
    builder.add_edge("aggregate_evidence", "generate_hypotheses")
    builder.add_edge("generate_hypotheses", "verify_hypotheses")
    builder.add_edge("verify_hypotheses", "judge_evidence")
    builder.add_conditional_edges("judge_evidence", route_after_judge)
    builder.add_conditional_edges("refine_investigation", dispatch_evidence_collection)
    builder.add_edge("generate_report", END)
    return builder.compile(name="incident-copilot-phase-4")
