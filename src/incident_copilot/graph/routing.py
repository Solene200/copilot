"""Pure, exhaustively tested investigation loop routing policy."""

from dataclasses import dataclass
from typing import Literal

from incident_copilot.graph.schemas import RouteTarget, StopReason
from incident_copilot.graph.state import InvestigationState


@dataclass(frozen=True, slots=True)
class RouteDecision:
    """A route target coupled to its auditable reason when research stops."""

    target: RouteTarget
    stop_reason: StopReason | None


def budget_stop_reason(state: InvestigationState) -> StopReason | None:
    """Return the highest-priority hard budget stop, independent of model judgement."""
    existing = state.get("stop_reason")
    if existing in {
        StopReason.DEADLINE_EXCEEDED,
        StopReason.TOOL_BUDGET_EXHAUSTED,
        StopReason.MODEL_BUDGET_EXHAUSTED,
        StopReason.TOKEN_BUDGET_EXHAUSTED,
    }:
        return existing
    if state.get("deadline_exceeded", False):
        return StopReason.DEADLINE_EXCEEDED
    if state.get("tool_call_count", 0) >= state["max_tool_calls"]:
        return StopReason.TOOL_BUDGET_EXHAUSTED
    if state.get("model_call_count", 0) >= state["max_model_calls"]:
        return StopReason.MODEL_BUDGET_EXHAUSTED
    usage = state.get("model_usage")
    if usage is not None and (
        usage.input_tokens + usage.output_tokens >= state["max_estimated_tokens"]
    ):
        return StopReason.TOKEN_BUDGET_EXHAUSTED
    return None


def decide_after_judge(state: InvestigationState) -> RouteDecision:
    """Apply non-model stop rules in fixed priority order."""
    budget_reason = budget_stop_reason(state)
    if budget_reason is not None:
        return RouteDecision(RouteTarget.REPORT, budget_reason)
    if state.get("evidence_sufficient", False):
        return RouteDecision(RouteTarget.REPORT, StopReason.EVIDENCE_SUFFICIENT)
    if state["research_round"] >= state["max_research_rounds"]:
        return RouteDecision(RouteTarget.REPORT, StopReason.MAX_RESEARCH_ROUNDS)
    return RouteDecision(RouteTarget.REFINE, None)


def route_after_parse(
    state: InvestigationState,
) -> Literal["build_investigation_plan", "generate_report"]:
    """Skip every external call when the invocation is already out of time."""
    if budget_stop_reason(state) is StopReason.DEADLINE_EXCEEDED:
        return "generate_report"
    return "build_investigation_plan"


def route_after_judge(
    state: InvestigationState,
) -> Literal["refine_investigation", "generate_report"]:
    """Return only a predeclared graph node name."""
    if decide_after_judge(state).target is RouteTarget.REFINE:
        return "refine_investigation"
    return "generate_report"
