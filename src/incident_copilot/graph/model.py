"""Structured model port and deterministic offline implementation."""

import hashlib
from datetime import timedelta
from typing import Protocol

from pydantic import JsonValue

from incident_copilot.domain.common import HypothesisStatus, SourceType
from incident_copilot.domain.hypothesis import Hypothesis, VerificationQuery
from incident_copilot.graph.schemas import (
    HypothesesOutput,
    InvestigationStep,
    ModelContext,
    ModelResponse,
    ModelTask,
    ModelUsage,
    PlanOutput,
    ReportDraftOutput,
    SufficiencyOutput,
    stable_query_key,
)


class ModelProvider(Protocol):
    """Provider-neutral boundary returning untrusted JSON-like structured output."""

    async def complete(self, context: ModelContext) -> ModelResponse:
        """Complete exactly one allow-listed structured task."""
        ...


def _step(
    *,
    round_number: int,
    ordinal: int,
    tool_name: str,
    source_type: SourceType,
    purpose: str,
    arguments: dict[str, object],
    priority: int,
) -> InvestigationStep:
    query_key = stable_query_key(tool_name, arguments)
    return InvestigationStep(
        step_id=f"step_r{round_number}_{ordinal}_{query_key[:12]}",
        query_key=query_key,
        tool_name=tool_name,
        source_type=source_type,
        purpose=purpose,
        arguments=arguments,
        priority=priority,
        round_number=round_number,
    )


class FakeModelProvider:
    """Deterministic, evidence-driven model substitute with no network access."""

    def __init__(self, *, minimum_research_rounds: int = 1) -> None:
        if minimum_research_rounds < 1:
            raise ValueError("minimum_research_rounds must be positive")
        self._minimum_research_rounds = minimum_research_rounds

    async def complete(self, context: ModelContext) -> ModelResponse:
        """Produce task-specific Pydantic output serialized through JSON mode."""
        output: PlanOutput | HypothesesOutput | SufficiencyOutput | ReportDraftOutput
        if context.task is ModelTask.PLAN:
            output = self._plan(context)
        elif context.task is ModelTask.HYPOTHESES:
            output = self._hypotheses(context)
        elif context.task is ModelTask.JUDGE:
            output = self._judge(context)
        else:
            output = self._report(context)
        payload = output.model_dump(mode="json")
        serialized_context = context.model_dump_json()
        serialized_output = output.model_dump_json()
        return ModelResponse(
            payload=payload,
            usage=ModelUsage(
                input_tokens=max(1, len(serialized_context) // 4),
                output_tokens=max(1, len(serialized_output) // 4),
                estimated=True,
            ),
        )

    def _plan(self, context: ModelContext) -> PlanOutput:
        service = context.service
        start = context.start_time
        end = context.end_time
        if context.research_round == 1:
            specs: tuple[tuple[str, SourceType, str, dict[str, object], int], ...] = (
                (
                    "search_logs",
                    SourceType.LOG,
                    "Find database acquisition failures in the incident window.",
                    {
                        "service": service,
                        "start_time": start.isoformat(),
                        "end_time": end.isoformat(),
                        "query": "connection acquisition",
                        "limit": 10,
                    },
                    100,
                ),
                (
                    "query_metrics",
                    SourceType.METRIC,
                    "Measure database pool saturation.",
                    {
                        "service": service,
                        "start_time": start.isoformat(),
                        "end_time": end.isoformat(),
                        "metric_name": "db.pool.utilization",
                        "aggregation": "max",
                        "limit": 10,
                    },
                    100,
                ),
                (
                    "query_traces",
                    SourceType.TRACE,
                    "Locate the blocking span on timed-out payment requests.",
                    {
                        "service": service,
                        "start_time": start.isoformat(),
                        "end_time": end.isoformat(),
                        "operation": "POST /payments",
                        "status": "timeout",
                        "limit": 10,
                    },
                    95,
                ),
                (
                    "get_recent_changes",
                    SourceType.CHANGE,
                    "Check configuration changes immediately before impact.",
                    {
                        "service": service,
                        "start_time": (start - timedelta(minutes=30)).isoformat(),
                        "end_time": end.isoformat(),
                        "change_type": "configuration",
                        "limit": 10,
                    },
                    95,
                ),
                (
                    "get_service_topology",
                    SourceType.TOPOLOGY,
                    "Identify critical dependencies for alternative hypotheses.",
                    {"service": service, "at_time": start.isoformat(), "depth": 1, "limit": 10},
                    80,
                ),
                (
                    "search_runbooks",
                    SourceType.KNOWLEDGE,
                    "Find a vetted connection pool timeout runbook.",
                    {"service": service, "query": "connection pool timeout", "limit": 5},
                    75,
                ),
                (
                    "search_similar_incidents",
                    SourceType.KNOWLEDGE,
                    "Compare prior incidents with the same failure signature.",
                    {
                        "service": service,
                        "query": "connection pool timeout",
                        "before_time": start.isoformat(),
                        "lookback_days": 90,
                        "limit": 5,
                    },
                    70,
                ),
            )
        elif follow_up_specs := self._follow_up_specs(context):
            specs = follow_up_specs
        else:
            specs = (
                (
                    "search_logs",
                    SourceType.LOG,
                    "Broaden the log query to capture request-level timeout symptoms.",
                    {
                        "service": service,
                        "start_time": start.isoformat(),
                        "end_time": end.isoformat(),
                        "query": "timed out",
                        "limit": 10,
                    },
                    100,
                ),
                (
                    "query_metrics",
                    SourceType.METRIC,
                    "Correlate pool saturation with the service error rate.",
                    {
                        "service": service,
                        "start_time": start.isoformat(),
                        "end_time": end.isoformat(),
                        "metric_name": "http.server.error_rate",
                        "aggregation": "rate",
                        "limit": 10,
                    },
                    90,
                ),
                (
                    "query_traces",
                    SourceType.TRACE,
                    "Recheck timeout traces without restricting the operation name.",
                    {
                        "service": service,
                        "start_time": start.isoformat(),
                        "end_time": end.isoformat(),
                        "status": "timeout",
                        "limit": 10,
                    },
                    85,
                ),
            )
        steps = tuple(
            _step(
                round_number=context.research_round,
                ordinal=ordinal,
                tool_name=tool_name,
                source_type=source_type,
                purpose=purpose,
                arguments=arguments,
                priority=priority,
            )
            for ordinal, (tool_name, source_type, purpose, arguments, priority) in enumerate(
                specs, start=1
            )
        )
        return PlanOutput(
            objective=f"Explain {service} failures using independent, citable evidence.",
            steps=steps,
            rationale=(
                "Collect symptoms, causal changes, dependency context, and operational knowledge."
            ),
        )

    @staticmethod
    def _follow_up_specs(
        context: ModelContext,
    ) -> tuple[tuple[str, SourceType, str, dict[str, object], int], ...]:
        """Turn bounded provider-neutral follow-up intent into offline demo steps."""
        feedback = context.human_feedback
        queries = (
            feedback.requested_queries
            if feedback is not None and feedback.requested_queries
            else context.next_investigation_queries
        )
        specs: list[tuple[str, SourceType, str, dict[str, object], int]] = []
        for query in queries:
            service = query.service or context.service
            for source_type in query.source_types:
                spec: tuple[str, SourceType, str, dict[str, object], int]
                common_range = {
                    "service": service,
                    "start_time": context.start_time.isoformat(),
                    "end_time": context.end_time.isoformat(),
                }
                if source_type is SourceType.LOG:
                    spec = (
                        "search_logs",
                        source_type,
                        query.query,
                        {**common_range, "query": query.query, "limit": 10},
                        100,
                    )
                elif source_type is SourceType.METRIC:
                    normalized = query.query.casefold()
                    pool_metric = any(
                        term in normalized for term in ("database", "connection", "pool")
                    )
                    spec = (
                        "query_metrics",
                        source_type,
                        query.query,
                        {
                            **common_range,
                            "metric_name": (
                                "db.pool.utilization" if pool_metric else "http.server.error_rate"
                            ),
                            "aggregation": "max" if pool_metric else "rate",
                            "limit": 10,
                        },
                        100,
                    )
                elif source_type is SourceType.TRACE:
                    spec = (
                        "query_traces",
                        source_type,
                        query.query,
                        {**common_range, "status": "timeout", "limit": 10},
                        95,
                    )
                elif source_type is SourceType.CHANGE:
                    spec = (
                        "get_recent_changes",
                        source_type,
                        query.query,
                        {
                            **common_range,
                            "start_time": (context.start_time - timedelta(minutes=30)).isoformat(),
                            "change_type": "configuration",
                            "limit": 10,
                        },
                        95,
                    )
                elif source_type is SourceType.TOPOLOGY:
                    spec = (
                        "get_service_topology",
                        source_type,
                        query.query,
                        {
                            "service": service,
                            "at_time": context.start_time.isoformat(),
                            "depth": 1,
                            "limit": 10,
                        },
                        80,
                    )
                else:
                    spec = (
                        "search_runbooks",
                        source_type,
                        query.query,
                        {"service": service, "query": query.query, "limit": 5},
                        75,
                    )
                specs.append(spec)
                if len(specs) == 20:
                    return tuple(specs)
        return tuple(specs)

    def _hypotheses(self, context: ModelContext) -> HypothesesOutput:
        relevant: list[dict[str, JsonValue]] = []
        for item in context.evidence_summaries:
            score = item.get("relevance_score", 0.0)
            if isinstance(score, (int, float)) and not isinstance(score, bool) and score >= 0.75:
                relevant.append(item)
        supporting_ids = tuple(str(item["evidence_id"]) for item in relevant[:20])
        evidence_summary = " ".join(
            str(item.get("summary", "")).strip() for item in relevant[:3]
        ).strip()
        description = (
            f"The leading evidence pattern for {context.service} is: {evidence_summary}"
            if evidence_summary
            else f"The available evidence does not yet establish a cause for {context.service}."
        )[:2_000]
        hypothesis_digest = hashlib.sha256(
            f"{context.incident_id}|{'|'.join(supporting_ids)}".encode()
        ).hexdigest()[:24]
        hypothesis = Hypothesis(
            hypothesis_id=f"hyp_{hypothesis_digest}",
            description=description,
            affected_services=(context.service,),
            supporting_evidence_ids=supporting_ids,
            confidence=min(0.9, 0.35 + len(supporting_ids) * 0.08),
            status=HypothesisStatus.PROPOSED,
            verification_queries=(
                VerificationQuery(
                    query="Compare the leading symptoms with changes and independent signals.",
                    source_types=(SourceType.METRIC, SourceType.LOG, SourceType.CHANGE),
                    service=context.service,
                ),
            ),
            reasoning_summary=(
                "This deterministic fake groups only high-relevance cited summaries; it does not "
                "use fixture ground truth or claim general diagnostic ability."
            ),
            version=context.research_round,
        )
        return HypothesesOutput(hypotheses=(hypothesis,))

    def _judge(self, context: ModelContext) -> SufficiencyOutput:
        source_types = {str(item["source_type"]) for item in context.evidence_summaries}
        enough_sources = len(source_types) >= 2
        enough_rounds = context.research_round >= self._minimum_research_rounds
        sufficient = enough_sources and enough_rounds and bool(context.hypotheses)
        reason = (
            "A supported hypothesis is backed by multiple independent evidence sources."
            if sufficient
            else "The current evidence packet requires another bounded investigation round."
        )
        return SufficiencyOutput(
            sufficient=sufficient,
            reason=reason,
            next_queries=(
                VerificationQuery(
                    query="Collect another independent signal for the leading hypothesis.",
                    source_types=(SourceType.LOG, SourceType.METRIC, SourceType.TRACE),
                    service=context.service,
                ),
            )
            if not sufficient
            else (),
        )

    @staticmethod
    def _report(context: ModelContext) -> ReportDraftOutput:
        root_cause = (
            context.hypotheses[0].description
            if context.hypotheses
            else "The available evidence does not establish a root cause."
        )
        return ReportDraftOutput(
            summary=(
                f"Investigated {context.service} using bounded multi-source evidence collection."
            ),
            root_cause=root_cause,
            confidence_rationale=(
                "Confidence is limited to the cited evidence collected by read-only tools."
            ),
            remediation_actions=(
                "Review the highest-relevance cited change and reverse it only after approval.",
                "Validate the affected service and all cited signals after mitigation.",
            ),
            risks=(
                "Acting on an offline rule-based hypothesis can worsen impact; "
                "require human approval.",
            ),
        )
