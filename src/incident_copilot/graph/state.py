"""LangGraph state channels and deterministic parallel reducers."""

from collections.abc import Callable, Sequence
from datetime import datetime
from typing import Annotated, TypeVar

from typing_extensions import TypedDict

from incident_copilot.domain.evidence import EvidenceRef
from incident_copilot.domain.hypothesis import Hypothesis, VerificationQuery
from incident_copilot.domain.incident import IncidentContext
from incident_copilot.domain.report import IncidentReport
from incident_copilot.graph.schemas import (
    InvestigationError,
    InvestigationPlan,
    InvestigationStep,
    ModelUsage,
    StepResult,
    StopReason,
)

ItemT = TypeVar("ItemT")


def _merge_bounded_by_id(
    left: Sequence[ItemT],
    right: Sequence[ItemT],
    *,
    identity: Callable[[ItemT], str],
    rank: Callable[[ItemT], tuple[object, ...]],
    limit: int,
) -> tuple[ItemT, ...]:
    merged = {identity(item): item for item in (*left, *right)}
    return tuple(sorted(merged.values(), key=rank)[:limit])


def merge_evidence(
    left: Sequence[EvidenceRef], right: Sequence[EvidenceRef]
) -> tuple[EvidenceRef, ...]:
    """Union evidence by ID and retain a deterministic global top 100."""
    return _merge_bounded_by_id(
        left,
        right,
        identity=lambda item: item.evidence_id,
        rank=lambda item: (-item.relevance_score, -item.reliability_score, item.evidence_id),
        limit=100,
    )


def merge_step_results(
    left: Sequence[StepResult], right: Sequence[StepResult]
) -> tuple[StepResult, ...]:
    """Make replayed step completion idempotent and ordering independent."""
    return _merge_bounded_by_id(
        left,
        right,
        identity=lambda item: item.step_id,
        rank=lambda item: (item.step_id,),
        limit=200,
    )


def merge_errors(
    left: Sequence[InvestigationError], right: Sequence[InvestigationError]
) -> tuple[InvestigationError, ...]:
    """Retain a deterministic bounded set of sanitized failures."""
    return _merge_bounded_by_id(
        left,
        right,
        identity=lambda item: item.error_id,
        rank=lambda item: (item.error_id,),
        limit=100,
    )


def add_count(left: int, right: int) -> int:
    """Combine per-branch counter deltas without read-modify-write races."""
    return left + right


def add_usage(left: ModelUsage, right: ModelUsage) -> ModelUsage:
    """Combine model usage deltas while preserving estimated provenance."""
    return ModelUsage(
        input_tokens=left.input_tokens + right.input_tokens,
        output_tokens=left.output_tokens + right.output_tokens,
        estimated=left.estimated or right.estimated,
    )


class InvestigationState(TypedDict, total=False):
    """Bounded graph channels; nodes emit only their minimal updates."""

    incident: IncidentContext
    investigation_plan: InvestigationPlan
    pending_steps: tuple[InvestigationStep, ...]
    current_step: InvestigationStep
    completed_steps: Annotated[tuple[StepResult, ...], merge_step_results]
    evidence: Annotated[tuple[EvidenceRef, ...], merge_evidence]
    hypotheses: tuple[Hypothesis, ...]
    evidence_sufficient: bool
    sufficiency_reason: str
    next_investigation_queries: tuple[VerificationQuery, ...]
    research_round: int
    max_research_rounds: int
    max_tool_calls: int
    max_parallel_tools: int
    tool_call_count: Annotated[int, add_count]
    tool_success_count: Annotated[int, add_count]
    tool_failure_count: Annotated[int, add_count]
    max_model_calls: int
    model_call_count: Annotated[int, add_count]
    model_usage: Annotated[ModelUsage, add_usage]
    started_at: datetime
    deadline_at: datetime
    deadline_exceeded: bool
    errors: Annotated[tuple[InvestigationError, ...], merge_errors]
    stop_reason: StopReason
    final_report: IncidentReport
