"""Investigation graph node implementations with explicit degradation paths."""

import hashlib
from collections import Counter
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Generic, TypeVar

from pydantic import BaseModel, ValidationError

from incident_copilot.domain.common import (
    HypothesisStatus,
    ReportDisposition,
    RiskLevel,
    SourceType,
)
from incident_copilot.domain.evidence import EvidenceRef
from incident_copilot.domain.hypothesis import Hypothesis
from incident_copilot.domain.report import (
    IncidentReport,
    InvestigationStats,
    RemediationStep,
    TimelineEvent,
)
from incident_copilot.graph.model import FakeModelProvider, ModelProvider
from incident_copilot.graph.routing import decide_after_judge
from incident_copilot.graph.schemas import (
    ErrorCategory,
    HypothesesOutput,
    InvestigationError,
    InvestigationPlan,
    ModelContext,
    ModelTask,
    ModelUsage,
    PlanOutput,
    ReportDraftOutput,
    StepResult,
    StepStatus,
    StopReason,
    SufficiencyOutput,
)
from incident_copilot.graph.state import InvestigationState, add_usage
from incident_copilot.tools.exceptions import ToolError, ToolExecutionError
from incident_copilot.tools.registry import ToolRegistry
from incident_copilot.tools.schemas import QueryContext

OutputT = TypeVar("OutputT", bound=BaseModel)
Clock = Callable[[], datetime]


def utc_now() -> datetime:
    """Return a timezone-aware UTC timestamp for production composition."""
    return datetime.now(UTC)


@dataclass(frozen=True, slots=True)
class StructuredCall(Generic[OutputT]):
    """Validated model value plus reducer-safe per-node deltas."""

    value: OutputT | None
    call_count: int
    usage: ModelUsage
    errors: tuple[InvestigationError, ...]


class InvestigationNodes:
    """Dependency-injected, bounded node set used by the compiled graph."""

    def __init__(
        self,
        *,
        registry: ToolRegistry,
        model: ModelProvider,
        clock: Clock = utc_now,
    ) -> None:
        self._registry = registry
        self._model = model
        self._clock = clock
        self._fallback_model = FakeModelProvider()

    async def parse_incident(self, state: InvestigationState) -> InvestigationState:
        """Accept the already-domain-validated incident at the graph boundary."""
        if not state["incident"].services:
            raise ValueError("investigation requires at least one normalized service")
        return {"deadline_exceeded": self._clock() >= state["deadline_at"]}

    async def build_investigation_plan(self, state: InvestigationState) -> InvestigationState:
        """Generate the first bounded plan through a structured output Schema."""
        return await self._plan_update(state, round_number=state["research_round"])

    async def refine_investigation(self, state: InvestigationState) -> InvestigationState:
        """Increment the single-writer round and create only incremental queries."""
        next_round = state["research_round"] + 1
        update = await self._plan_update(state, round_number=next_round)
        update["research_round"] = next_round
        return update

    async def collect_evidence(self, state: InvestigationState) -> InvestigationState:
        """Execute one Send-scoped tool step and convert failures into state data."""
        step = state["current_step"]
        started_at = self._clock()
        context = QueryContext(
            correlation_id=f"{state['incident'].incident_id}:{step.step_id}",
            deadline=state["deadline_at"],
            remaining_tool_calls=1,
        )
        try:
            result = await self._registry.execute(step.tool_name, step.arguments, context)
        except ToolError as exc:
            completed_at = self._clock()
            error = self._tool_error(step.step_id, step.tool_name, exc, completed_at)
            attempts = exc.attempts if isinstance(exc, ToolExecutionError) else 1
            step_result = StepResult(
                step_id=step.step_id,
                query_key=step.query_key,
                tool_name=step.tool_name,
                status=StepStatus.FAILED,
                error_id=error.error_id,
                attempts=attempts,
                started_at=started_at,
                completed_at=completed_at,
            )
            return {
                "completed_steps": (step_result,),
                "errors": (error,),
                "tool_call_count": 1,
                "tool_failure_count": 1,
            }

        completed_at = self._clock()
        refs = tuple(EvidenceRef.from_evidence(item) for item in result.evidence)
        step_result = StepResult(
            step_id=step.step_id,
            query_key=step.query_key,
            tool_name=step.tool_name,
            status=StepStatus.COMPLETED,
            evidence_ids=tuple(item.evidence_id for item in refs),
            attempts=result.attempts,
            started_at=started_at,
            completed_at=completed_at,
        )
        return {
            "completed_steps": (step_result,),
            "evidence": refs,
            "tool_call_count": 1,
            "tool_success_count": 1,
        }

    async def aggregate_evidence(self, state: InvestigationState) -> InvestigationState:
        """Mark deadline state after all map branches have reached the barrier."""
        return {"deadline_exceeded": self._clock() >= state["deadline_at"]}

    async def generate_hypotheses(self, state: InvestigationState) -> InvestigationState:
        """Generate Schema-validated hypotheses or use the deterministic fallback."""
        context = self._model_context(state, ModelTask.HYPOTHESES)
        call = await self._call_structured(state, context, HypothesesOutput)
        output = call.value or await self._fallback(context, HypothesesOutput)
        return {
            "hypotheses": output.hypotheses,
            "model_call_count": call.call_count,
            "model_usage": call.usage,
            "errors": call.errors,
        }

    async def verify_hypotheses(self, state: InvestigationState) -> InvestigationState:
        """Enforce evidence foreign keys and confidence policy outside the model."""
        evidence_by_id = {item.evidence_id: item for item in state.get("evidence", ())}
        verified: list[Hypothesis] = []
        for hypothesis in state.get("hypotheses", ()):
            supporting_ids = tuple(
                item for item in hypothesis.supporting_evidence_ids if item in evidence_by_id
            )
            contradicting_ids = tuple(
                item
                for item in hypothesis.contradicting_evidence_ids
                if item in evidence_by_id and item not in supporting_ids
            )
            supporting_sources = {evidence_by_id[item].source_type for item in supporting_ids}
            status = (
                HypothesisStatus.SUPPORTED
                if supporting_ids and len(supporting_sources) >= 2
                else HypothesisStatus.INCONCLUSIVE
            )
            confidence = hypothesis.confidence
            if not supporting_ids:
                confidence = min(confidence, 0.2)
            elif len(supporting_sources) < 2:
                confidence = min(confidence, 0.55)
            verified.append(
                Hypothesis.model_validate(
                    {
                        **hypothesis.model_dump(mode="python"),
                        "supporting_evidence_ids": supporting_ids,
                        "contradicting_evidence_ids": contradicting_ids,
                        "confidence": confidence,
                        "status": status,
                    }
                )
            )
        return {"hypotheses": tuple(verified)}

    async def judge_evidence(self, state: InvestigationState) -> InvestigationState:
        """Combine structured judgement with deterministic sufficiency and stop rules."""
        context = self._model_context(state, ModelTask.JUDGE)
        call = await self._call_structured(state, context, SufficiencyOutput)
        if call.value is None:
            output = await self._fallback(context, SufficiencyOutput)
        else:
            output = call.value
        supported = any(
            item.status is HypothesisStatus.SUPPORTED for item in state.get("hypotheses", ())
        )
        sources = {item.source_type for item in state.get("evidence", ())}
        sufficient = output.sufficient and supported and len(sources) >= 2
        deadline_exceeded = self._clock() >= state["deadline_at"]
        projected: InvestigationState = state.copy()
        projected["evidence_sufficient"] = sufficient
        projected["deadline_exceeded"] = deadline_exceeded
        projected["model_call_count"] = state.get("model_call_count", 0) + call.call_count
        decision = decide_after_judge(projected)
        update: InvestigationState = {
            "evidence_sufficient": sufficient,
            "sufficiency_reason": output.reason,
            "next_investigation_queries": output.next_queries,
            "deadline_exceeded": deadline_exceeded,
            "model_call_count": call.call_count,
            "model_usage": call.usage,
            "errors": call.errors,
        }
        if decision.stop_reason is not None:
            update["stop_reason"] = decision.stop_reason
        return update

    async def generate_report(self, state: InvestigationState) -> InvestigationState:
        """Build an honest domain report and attach only verified Evidence IDs."""
        context = self._model_context(state, ModelTask.REPORT)
        call = await self._call_structured(state, context, ReportDraftOutput)
        draft = call.value or await self._fallback(context, ReportDraftOutput)
        completed_at = self._clock()
        evidence = state.get("evidence", ())
        evidence_by_id = {item.evidence_id: item for item in evidence}
        leading = state.get("hypotheses", (None,))[0]
        supporting_ids = leading.supporting_evidence_ids if leading is not None else ()
        contradicting_ids = leading.contradicting_evidence_ids if leading is not None else ()
        supporting = tuple(
            evidence_by_id[item] for item in supporting_ids if item in evidence_by_id
        )
        contradicting = tuple(
            evidence_by_id[item] for item in contradicting_ids if item in evidence_by_id
        )
        if not supporting:
            supporting = tuple(item for item in evidence if item.relevance_score >= 0.75)[:20]
        citations = tuple(
            {
                item.citation.citation_id: item.citation for item in (*supporting, *contradicting)
            }.values()
        )
        timeline = self._timeline((*supporting, *contradicting))
        stop_reason = state["stop_reason"]
        disposition = (
            ReportDisposition.PROBABLE
            if stop_reason is StopReason.EVIDENCE_SUFFICIENT and bool(supporting)
            else ReportDisposition.INCONCLUSIVE
        )
        source_counts = Counter(item.source_type for item in evidence)
        missing_sources = [source.value for source in SourceType if source_counts[source] == 0]
        limitations = (
            [f"missing evidence sources: {', '.join(missing_sources)}"] if missing_sources else []
        )
        if state.get("errors"):
            limitations.append(f"{len(state['errors'])} tool/model error(s) were degraded")
        if disposition is ReportDisposition.INCONCLUSIVE:
            limitations.append(f"research stopped because {stop_reason.value}")
        usage = add_usage(state.get("model_usage", ModelUsage()), call.usage)
        total_model_calls = state.get("model_call_count", 0) + call.call_count
        duration_ms = max(0, int((completed_at - state["started_at"]).total_seconds() * 1_000))
        report = IncidentReport(
            report_id=f"rpt_{state['incident'].incident_id.removeprefix('inc_')}",
            incident_id=state["incident"].incident_id,
            summary=draft.summary,
            root_cause=draft.root_cause,
            disposition=disposition,
            confidence=leading.confidence if leading is not None else 0.0,
            confidence_rationale=draft.confidence_rationale,
            affected_services=state["incident"].services,
            timeline=timeline,
            supporting_evidence=supporting,
            contradicting_evidence=contradicting,
            remediation_steps=tuple(
                RemediationStep(
                    action=action,
                    priority=index,
                    risk_level=RiskLevel.MEDIUM,
                    validation="Verify the cited signals return to their expected range.",
                    rollback="Restore the prior reviewed configuration if validation fails.",
                    requires_human_approval=True,
                )
                for index, action in enumerate(draft.remediation_actions, start=1)
            ),
            risks=draft.risks,
            citations=citations,
            investigation_summary=(
                f"Completed {state['research_round']} bounded research round(s) with "
                f"{state.get('tool_call_count', 0)} tool call(s)."
            ),
            investigation_stats=InvestigationStats(
                research_rounds=state["research_round"],
                tool_call_count=state.get("tool_call_count", 0),
                tool_success_count=state.get("tool_success_count", 0),
                tool_failure_count=state.get("tool_failure_count", 0),
                model_call_count=total_model_calls,
                input_tokens=usage.input_tokens,
                output_tokens=usage.output_tokens,
                total_tokens=usage.input_tokens + usage.output_tokens,
                token_usage_estimated=usage.estimated,
                started_at=state["started_at"],
                completed_at=completed_at,
                duration_ms=duration_ms,
                evidence_count_by_source=source_counts,
                stop_reason=stop_reason.value,
            ),
            limitations=tuple(limitations),
            generated_at=completed_at,
        )
        return {
            "final_report": report,
            "model_call_count": call.call_count,
            "model_usage": call.usage,
            "errors": call.errors,
        }

    async def _plan_update(
        self, state: InvestigationState, *, round_number: int
    ) -> InvestigationState:
        context = self._model_context(state, ModelTask.PLAN, round_number=round_number)
        call = await self._call_structured(state, context, PlanOutput)
        output = call.value or await self._fallback(context, PlanOutput)
        allowed = set(self._registry.tool_names)
        completed_queries = {item.query_key for item in state.get("completed_steps", ())}
        steps = tuple(
            step
            for step in output.steps
            if step.tool_name in allowed and step.query_key not in completed_queries
        )
        plan_hash = hashlib.sha256(
            "|".join(step.step_id for step in steps).encode("utf-8")
        ).hexdigest()[:16]
        plan = InvestigationPlan(
            plan_id=f"plan_r{round_number}_{plan_hash}",
            round_number=round_number,
            objective=output.objective,
            steps=steps,
            coverage_targets=tuple(dict.fromkeys(step.source_type for step in steps)),
            rationale=output.rationale,
        )
        return {
            "investigation_plan": plan,
            "pending_steps": steps,
            "model_call_count": call.call_count,
            "model_usage": call.usage,
            "errors": call.errors,
        }

    async def _call_structured(
        self,
        state: InvestigationState,
        context: ModelContext,
        schema: type[OutputT],
    ) -> StructuredCall[OutputT]:
        remaining = max(0, state["max_model_calls"] - state.get("model_call_count", 0))
        attempts = min(2, remaining)
        errors: list[InvestigationError] = []
        usage = ModelUsage()
        for attempt in range(1, attempts + 1):
            try:
                response = await self._model.complete(context)
                usage = add_usage(usage, response.usage)
                value = schema.model_validate(response.payload)
            except (ValidationError, ValueError, TypeError) as exc:
                errors.append(self._model_error(context.task, context.research_round, attempt, exc))
            else:
                return StructuredCall(value, attempt, usage, tuple(errors))
        if attempts == 0:
            errors.append(
                self._model_error(
                    context.task,
                    context.research_round,
                    1,
                    RuntimeError("model call budget exhausted"),
                    category=ErrorCategory.BUDGET,
                )
            )
        return StructuredCall(None, attempts, usage, tuple(errors))

    async def _fallback(self, context: ModelContext, schema: type[OutputT]) -> OutputT:
        response = await self._fallback_model.complete(context)
        return schema.model_validate(response.payload)

    def _model_context(
        self,
        state: InvestigationState,
        task: ModelTask,
        *,
        round_number: int | None = None,
    ) -> ModelContext:
        incident = state["incident"]
        evidence_summaries = tuple(
            {
                "evidence_id": item.evidence_id,
                "source_type": item.source_type.value,
                "service": item.service,
                "summary": item.summary,
                "relevance_score": item.relevance_score,
                "reliability_score": item.reliability_score,
            }
            for item in state.get("evidence", ())
        )
        return ModelContext(
            task=task,
            incident_id=incident.incident_id,
            service=incident.services[0],
            raw_query=incident.raw_query,
            start_time=incident.start_time,
            end_time=incident.end_time,
            research_round=round_number or state["research_round"],
            evidence_summaries=evidence_summaries,
            hypotheses=state.get("hypotheses", ()),
            error_count=len(state.get("errors", ())),
        )

    def _tool_error(
        self, step_id: str, tool_name: str, exc: ToolError, occurred_at: datetime
    ) -> InvestigationError:
        if isinstance(exc, ToolExecutionError):
            category = {
                "timeout": ErrorCategory.TIMEOUT,
                "unavailable": ErrorCategory.UNAVAILABLE,
                "malformed_response": ErrorCategory.MALFORMED_RESPONSE,
            }.get(exc.category.value, ErrorCategory.INTERNAL)
            retryable = exc.retryable
            attempt = exc.attempts
        else:
            category = ErrorCategory.VALIDATION
            retryable = False
            attempt = 1
        error_id = self._error_id("tool", tool_name, step_id, str(attempt))
        return InvestigationError(
            error_id=error_id,
            category=category,
            component="tool-registry",
            operation=tool_name,
            message=f"tool step failed: {tool_name}",
            retryable=retryable,
            occurred_at=occurred_at,
            step_id=step_id,
            attempt=attempt,
        )

    def _model_error(
        self,
        task: ModelTask,
        round_number: int,
        attempt: int,
        exc: Exception,
        *,
        category: ErrorCategory = ErrorCategory.MALFORMED_RESPONSE,
    ) -> InvestigationError:
        del exc
        return InvestigationError(
            error_id=self._error_id("model", task.value, str(round_number), str(attempt)),
            category=category,
            component="model-provider",
            operation=task.value,
            message=f"structured model output failed validation: {task.value}",
            retryable=category is not ErrorCategory.BUDGET,
            occurred_at=self._clock(),
            attempt=attempt,
        )

    @staticmethod
    def _error_id(*parts: str) -> str:
        digest = hashlib.sha256("|".join(parts).encode("utf-8")).hexdigest()[:24]
        return f"err_{digest}"

    @staticmethod
    def _timeline(evidence: Sequence[EvidenceRef]) -> tuple[TimelineEvent, ...]:
        events = [
            TimelineEvent(
                timestamp=item.timestamp or item.start_time,
                description=item.summary,
                evidence_ids=(item.evidence_id,),
            )
            for item in evidence
            if item.timestamp is not None or item.start_time is not None
        ]
        return tuple(sorted(events, key=lambda item: (item.timestamp, item.evidence_ids)))[:20]
