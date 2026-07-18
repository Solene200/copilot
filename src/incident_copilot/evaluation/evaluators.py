"""分母行为明确且可以手工核对的纯评估器。"""

import math
import re
from collections.abc import Iterable, Mapping, Sequence
from statistics import fmean
from typing import TypeVar

from pydantic import JsonValue

from incident_copilot.domain.report import IncidentReport
from incident_copilot.evaluation.schemas import (
    ActualToolCall,
    AggregateMetrics,
    CitationMetrics,
    EvaluationSampleResult,
    ExpectedToolCall,
    RetrievalMetrics,
    SampleStatus,
    SetMetrics,
    ToolArgumentMetrics,
)

ItemT = TypeVar("ItemT", bound=str)


FAILURE_TYPE_PATTERNS: Mapping[str, tuple[str, ...]] = {
    "database_connection_pool_exhaustion": (
        "connection pool",
        "pool saturat",
        "connection acquisition",
        "max_connections",
    ),
    "dns_misconfiguration": ("dns", "resolver", "name lookup", "lookup timeout"),
    "cache_configuration_regression": (
        "cache ttl",
        "cache miss",
        "read amplification",
        "cache configuration",
    ),
}


def _normalized_text(value: str) -> str:
    return " ".join(re.findall(r"[a-z0-9_.-]+", value.casefold()))


def set_metrics(expected: Iterable[ItemT], actual: Iterable[ItemT]) -> SetMetrics:
    """比较去重集合,两个空集合视为完全匹配。"""
    expected_set = set(expected)
    actual_set = set(actual)
    true_positives = expected_set & actual_set
    precision = (
        len(true_positives) / len(actual_set) if actual_set else (1.0 if not expected_set else 0.0)
    )
    recall = len(true_positives) / len(expected_set) if expected_set else 1.0
    f1 = 2 * precision * recall / (precision + recall) if precision + recall else 0.0
    return SetMetrics(
        expected_count=len(expected_set),
        actual_count=len(actual_set),
        true_positive_count=len(true_positives),
        precision=precision,
        recall=recall,
        f1=f1,
        exact_match=expected_set == actual_set,
    )


def retrieval_metrics(
    expected_document_ids: Sequence[str],
    ranked_document_ids: Sequence[str],
    *,
    top_k: int,
) -> RetrievalMetrics:
    """稳定去除排名重复项后计算文档级 Recall@K 和 MRR。"""
    unique_ranked = tuple(dict.fromkeys(ranked_document_ids))
    expected = set(expected_document_ids)
    visible = unique_ranked[:top_k]
    recall = len(expected.intersection(visible)) / len(expected) if expected else 1.0
    reciprocal_rank = 0.0
    for rank, document_id in enumerate(visible, start=1):
        if document_id in expected:
            reciprocal_rank = 1.0 / rank
            break
    return RetrievalMetrics(
        top_k=top_k,
        expected_document_ids=tuple(expected_document_ids),
        ranked_document_ids=unique_ranked,
        recall_at_k=recall,
        reciprocal_rank=reciprocal_rank,
    )


def tool_argument_metrics(
    expected_calls: Sequence[ExpectedToolCall], actual_calls: Sequence[ActualToolCall]
) -> ToolArgumentMetrics:
    """把标签字段与任意轮次中匹配度最高的同名工具执行进行比较。"""
    actual_by_name: dict[str, list[dict[str, JsonValue]]] = {}
    for call in actual_calls:
        actual_by_name.setdefault(call.tool_name, []).append(call.arguments)
    expected_count = 0
    matched_count = 0
    for expected_call in expected_calls:
        expected_count += len(expected_call.arguments)
        candidates = actual_by_name.get(expected_call.tool_name, ())
        matched_count += max(
            (
                sum(
                    actual_arguments.get(field) == expected_value
                    for field, expected_value in expected_call.arguments.items()
                )
                for actual_arguments in candidates
            ),
            default=0,
        )
    score = matched_count / expected_count if expected_count else 1.0
    return ToolArgumentMetrics(
        expected_field_count=expected_count,
        matched_field_count=matched_count,
        score=score,
    )


def classify_failure_type(text: str | None) -> str | None:
    """对报告文本应用不依赖具体样例的透明分类器。"""
    if not text:
        return None
    normalized = _normalized_text(text)
    scores = {
        label: sum(pattern in normalized for pattern in patterns)
        for label, patterns in FAILURE_TYPE_PATTERNS.items()
    }
    best_score = max(scores.values(), default=0)
    if best_score == 0:
        return None
    return sorted(label for label, score in scores.items() if score == best_score)[0]


def root_cause_term_recall(root_cause: str | None, terms: Sequence[str]) -> float:
    """不使用在线模型裁判,测量带标签因果指标的覆盖率。"""
    if not terms:
        return 1.0
    if not root_cause:
        return 0.0
    normalized = _normalized_text(root_cause)
    matches = sum(_normalized_text(term) in normalized for term in terms)
    return matches / len(terms)


def citation_metrics(report: IncidentReport) -> CitationMetrics:
    """验证每个 EvidenceRef 都能解析到完全一致的报告引用。"""
    citations = {citation.citation_id: citation for citation in report.citations}
    evidence = (*report.supporting_evidence, *report.contradicting_evidence)
    correct = 0
    for item in evidence:
        expected = item.citation
        actual = citations.get(expected.citation_id)
        if actual is not None and (
            actual.uri,
            actual.locator,
            actual.content_hash.casefold(),
        ) == (
            expected.uri,
            expected.locator,
            expected.content_hash.casefold(),
        ):
            correct += 1
    score = correct / len(evidence) if evidence else None
    return CitationMetrics(
        checked_evidence_count=len(evidence),
        correct_citation_count=correct,
        score=score,
    )


def _defined_mean(values: Iterable[float | None]) -> float | None:
    defined = [value for value in values if value is not None]
    return fmean(defined) if defined else None


def _percentile(values: Sequence[float], percentile: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    if len(ordered) == 1:
        return ordered[0]
    position = (len(ordered) - 1) * percentile
    lower = math.floor(position)
    upper = math.ceil(position)
    if lower == upper:
        return ordered[lower]
    fraction = position - lower
    return ordered[lower] + (ordered[upper] - ordered[lower]) * fraction


def aggregate_metrics(results: Sequence[EvaluationSampleResult]) -> AggregateMetrics:
    """聚合完成样例,同时让失败样例继续出现在汇总计数中。"""
    completed = [result for result in results if result.status is SampleStatus.COMPLETED]
    usages = [result.usage for result in completed if result.usage is not None]
    total_tokens = sum(usage.total_tokens for usage in usages)
    return AggregateMetrics(
        service_localization_accuracy=_defined_mean(
            float(metric.exact_match) if metric is not None else None
            for metric in (result.service_localization for result in completed)
        ),
        failure_type_accuracy=_defined_mean(
            float(value) if value is not None else None
            for value in (result.failure_type_correct for result in completed)
        ),
        retrieval_recall_at_k=_defined_mean(
            metric.recall_at_k if metric is not None else None
            for metric in (result.retrieval for result in completed)
        ),
        retrieval_mrr=_defined_mean(
            metric.reciprocal_rank if metric is not None else None
            for metric in (result.retrieval for result in completed)
        ),
        tool_selection_f1=_defined_mean(
            metric.f1 if metric is not None else None
            for metric in (result.tool_selection for result in completed)
        ),
        tool_argument_accuracy=_defined_mean(
            metric.score if metric is not None else None
            for metric in (result.tool_arguments for result in completed)
        ),
        evidence_relevance_f1=_defined_mean(
            metric.f1 if metric is not None else None
            for metric in (result.evidence_relevance for result in completed)
        ),
        citation_correctness=_defined_mean(
            metric.score if metric is not None else None
            for metric in (result.citations for result in completed)
        ),
        root_cause_accuracy=_defined_mean(
            float(value) if value is not None else None
            for value in (result.root_cause_accurate for result in completed)
        ),
        mean_research_rounds=_defined_mean(float(usage.research_rounds) for usage in usages),
        mean_tool_calls=_defined_mean(float(usage.tool_calls) for usage in usages),
        mean_latency_ms=_defined_mean(usage.latency_ms for usage in usages),
        p95_latency_ms=_percentile([usage.latency_ms for usage in usages], 0.95),
        total_tokens=total_tokens,
        mean_tokens=(total_tokens / len(usages) if usages else None),
        token_usage_estimated=(
            all(usage.token_usage_estimated for usage in usages) if usages else None
        ),
    )


def json_argument_value(value: object) -> JsonValue:
    """在 Graph 参数通过 Pydantic JSON 校验后收窄其类型。"""
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, list):
        return [json_argument_value(item) for item in value]
    if isinstance(value, dict):
        return {str(key): json_argument_value(item) for key, item in value.items()}
    raise TypeError(f"tool argument is not JSON-compatible: {type(value).__name__}")
