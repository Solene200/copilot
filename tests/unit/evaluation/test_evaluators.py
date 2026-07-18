"""Hand-calculated boundaries for every pure Phase 6 evaluator."""

from datetime import UTC, datetime

import pytest

from incident_copilot.domain.common import ReportDisposition, SourceType
from incident_copilot.domain.evidence import Citation, EvidenceRef
from incident_copilot.domain.report import IncidentReport, InvestigationStats
from incident_copilot.evaluation.evaluators import (
    aggregate_metrics,
    citation_metrics,
    classify_failure_type,
    retrieval_metrics,
    root_cause_term_recall,
    set_metrics,
    tool_argument_metrics,
)
from incident_copilot.evaluation.schemas import (
    ActualToolCall,
    EvaluationSampleResult,
    ExpectedToolCall,
    SampleStatus,
    SampleUsage,
)

NOW = datetime(2026, 7, 18, 8, 0, tzinfo=UTC)
HASH = "a" * 64


def _report(*, include_citation: bool = True) -> IncidentReport:
    citation = Citation(
        citation_id="cit_eval_source",
        uri="fixture://evaluation/source.json",
        locator="evidence[0]",
        display_name="evaluation source",
        retrieved_at=NOW,
        content_hash=HASH,
    )
    evidence = EvidenceRef(
        evidence_id="ev_eval_source",
        source_type=SourceType.LOG,
        title="Evaluation evidence",
        summary="A labeled causal signal.",
        timestamp=NOW,
        service="payment-service",
        relevance_score=1.0,
        reliability_score=1.0,
        citation=citation,
    )
    stats = InvestigationStats(
        research_rounds=1,
        tool_call_count=2,
        tool_success_count=2,
        tool_failure_count=0,
        model_call_count=4,
        input_tokens=30,
        output_tokens=10,
        total_tokens=40,
        token_usage_estimated=True,
        started_at=NOW,
        completed_at=NOW,
        duration_ms=0,
        evidence_count_by_source={SourceType.LOG: 1},
        stop_reason="evidence_sufficient",
    )
    return IncidentReport(
        report_id="rpt_eval_001",
        incident_id="inc_eval_001",
        summary="Evaluation report",
        root_cause="connection pool saturation",
        disposition=ReportDisposition.PROBABLE,
        confidence=0.8,
        confidence_rationale="Supported by one labeled signal.",
        affected_services=("payment-service",),
        supporting_evidence=(evidence,),
        citations=(citation,) if include_citation else (),
        investigation_summary="One bounded round.",
        investigation_stats=stats,
        generated_at=NOW,
    )


@pytest.mark.parametrize(
    ("expected", "actual", "precision", "recall", "f1", "exact"),
    [
        ((), (), 1.0, 1.0, 1.0, True),
        (("a", "b"), ("a",), 1.0, 0.5, 2 / 3, False),
        ((), ("unexpected",), 0.0, 1.0, 0.0, False),
        (("a",), (), 0.0, 0.0, 0.0, False),
    ],
)
def test_set_metrics_has_explicit_empty_set_rules(
    expected: tuple[str, ...],
    actual: tuple[str, ...],
    precision: float,
    recall: float,
    f1: float,
    exact: bool,
) -> None:
    result = set_metrics(expected, actual)

    assert result.precision == pytest.approx(precision)
    assert result.recall == pytest.approx(recall)
    assert result.f1 == pytest.approx(f1)
    assert result.exact_match is exact


def test_retrieval_metrics_deduplicate_before_recall_and_mrr() -> None:
    result = retrieval_metrics(("doc_b", "doc_c"), ("doc_a", "doc_b", "doc_b", "doc_c"), top_k=3)

    assert result.ranked_document_ids == ("doc_a", "doc_b", "doc_c")
    assert result.recall_at_k == 1.0
    assert result.reciprocal_rank == 0.5


def test_retrieval_metrics_keep_zero_when_no_relevant_document_is_found() -> None:
    result = retrieval_metrics(("doc_missing",), ("doc_a",), top_k=1)

    assert result.recall_at_k == 0.0
    assert result.reciprocal_rank == 0.0


def test_tool_argument_metrics_compare_only_labeled_fields() -> None:
    expected = (
        ExpectedToolCall(tool_name="search_logs", arguments={"service": "payment-service"}),
        ExpectedToolCall(tool_name="query_metrics", arguments={"metric_name": "cpu"}),
    )
    actual = (
        ActualToolCall(
            tool_name="search_logs",
            arguments={"service": "payment-service", "limit": 10},
            status="completed",
            evidence_ids=(),
        ),
        ActualToolCall(
            tool_name="query_metrics",
            arguments={"metric_name": "memory"},
            status="completed",
            evidence_ids=(),
        ),
    )

    result = tool_argument_metrics(expected, actual)

    assert result.expected_field_count == 2
    assert result.matched_field_count == 1
    assert result.score == 0.5


def test_tool_argument_metrics_are_stable_across_multiple_research_rounds() -> None:
    expected = (
        ExpectedToolCall(
            tool_name="search_logs",
            arguments={"service": "payment-service", "query": "connection acquisition"},
        ),
    )
    actual = (
        ActualToolCall(
            tool_name="search_logs",
            arguments={"service": "payment-service", "query": "timed out"},
            status="completed",
            evidence_ids=(),
        ),
        ActualToolCall(
            tool_name="search_logs",
            arguments={"service": "payment-service", "query": "connection acquisition"},
            status="completed",
            evidence_ids=(),
        ),
    )

    result = tool_argument_metrics(expected, actual)

    assert result.matched_field_count == 2
    assert result.score == 1.0


def test_failure_type_and_root_cause_terms_are_transparent_lexical_checks() -> None:
    text = "A DNS resolver change caused a name lookup timeout."

    assert classify_failure_type(text) == "dns_misconfiguration"
    assert root_cause_term_recall(
        text, ("DNS resolver", "lookup timeout", "gateway")
    ) == pytest.approx(2 / 3)
    assert classify_failure_type("unclassified symptom") is None


def test_citation_correctness_requires_exact_report_citation() -> None:
    correct = citation_metrics(_report(include_citation=True))
    missing = citation_metrics(_report(include_citation=False))

    assert correct.checked_evidence_count == 1
    assert correct.correct_citation_count == 1
    assert correct.score == 1.0
    assert missing.correct_citation_count == 0
    assert missing.score == 0.0


def test_aggregate_excludes_failed_sample_but_keeps_measured_token_provenance() -> None:
    report = _report()
    completed = EvaluationSampleResult(
        sample_id="eval_completed",
        status=SampleStatus.COMPLETED,
        root_cause_accurate=True,
        usage=SampleUsage(
            research_rounds=1,
            tool_calls=2,
            model_calls=4,
            latency_ms=12.5,
            input_tokens=30,
            output_tokens=10,
            total_tokens=40,
            token_usage_estimated=True,
        ),
        report=report,
    )
    failed = EvaluationSampleResult(
        sample_id="eval_failed",
        status=SampleStatus.FAILED,
        error="fixture unavailable",
    )

    aggregate = aggregate_metrics((completed, failed))

    assert aggregate.root_cause_accuracy == 1.0
    assert aggregate.mean_latency_ms == 12.5
    assert aggregate.p95_latency_ms == 12.5
    assert aggregate.total_tokens == 40
    assert aggregate.token_usage_estimated is True
    assert aggregate.estimated_cost_usd is None
    assert aggregate.cost_status == "unavailable_no_pricing"
