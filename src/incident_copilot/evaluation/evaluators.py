"""分母行为明确且可以手工核对的纯评估器。"""

import math
import re
from collections.abc import Iterable, Mapping, Sequence
from statistics import fmean
from typing import TypeVar

from pydantic import JsonValue

from incident_copilot.domain.evidence import (
    Citation,
    EvidenceResolutionError,
    EvidenceResolver,
    content_sha256,
)
from incident_copilot.domain.report import IncidentReport
from incident_copilot.evaluation.schemas import (
    ActualToolCall,
    AggregateMetrics,
    CitationCheckMetrics,
    CitationMetrics,
    EvaluationSampleResult,
    ExpectedToolCall,
    RetrievalMetrics,
    SampleStatus,
    SetMetrics,
    ToolArgumentMetrics,
)

# 集合指标函数接收的字符串子类型, 用于保留输入元素的具体类型。
ItemT = TypeVar("ItemT", bound=str)


# 故障类型机器标签及其可在根因文本中识别的英文关键词。
FAILURE_TYPE_PATTERNS: Mapping[str, tuple[str, ...]] = {
    # 数据库连接池耗尽: 可用连接不足或获取连接超时。
    "database_connection_pool_exhaustion": (
        "connection pool",
        "pool saturat",
        "connection acquisition",
        "max_connections",
    ),
    # DNS 配置错误: 域名解析器、名称查询或查询超时异常。
    "dns_misconfiguration": ("dns", "resolver", "name lookup", "lookup timeout"),
    # 缓存配置回退: TTL 等配置变化导致命中率下降和后端读取放大。
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
    # 集合指标忽略顺序和重复项, 适合服务、工具、Evidence ID 这类多标签结果。
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
    # 同一文档多个 Chunk 命中只保留首次排名, 避免重复文档虚增 Recall。
    unique_ranked = tuple(dict.fromkeys(ranked_document_ids))
    expected = set(expected_document_ids)
    visible = unique_ranked[:top_k]
    recall = len(expected.intersection(visible)) / len(expected) if expected else 1.0
    reciprocal_rank = 0.0
    for rank, document_id in enumerate(visible, start=1):
        # MRR 只关心第一条相关文档出现的位置, 找到后即可停止。
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
        # 同名工具可能跨轮调用多次, 为每个期望调用选参数匹配最多的真实执行。
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
    # 每个标签按透明关键词命中数打分, 同分时用标签字典序保证结果稳定。
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
    # 空标签集合定义为完全覆盖; 有标签但报告无根因则定义为零覆盖。
    if not terms:
        return 1.0
    if not root_cause:
        return 0.0
    normalized = _normalized_text(root_cause)
    matches = sum(_normalized_text(term) in normalized for term in terms)
    return matches / len(terms)


def _citation_check_metrics(checked: int, passed: int) -> CitationCheckMetrics:
    return CitationCheckMetrics(
        checked_citation_count=checked,
        passed_citation_count=passed,
        score=passed / checked if checked else None,
    )


def citation_metrics(
    report: IncidentReport,
    resolver: EvidenceResolver,
) -> CitationMetrics:
    """分别验证引用对象一致性、locator 可解析性与内容完整性。"""
    citations = {citation.citation_id: citation for citation in report.citations}
    evidence = (*report.supporting_evidence, *report.contradicting_evidence)
    consistent = 0
    resolved_contents: list[tuple[Citation, JsonValue]] = []
    for item in evidence:
        # 第一层只比较报告 Citation 与 EvidenceRef 内嵌 Citation 是否指向同一内容。
        expected = item.citation
        actual = citations.get(expected.citation_id)
        if actual is not None and (
            actual.uri,
            actual.locator,
            actual.content_hash_algorithm,
            actual.content_hash.casefold(),
        ) == (
            expected.uri,
            expected.locator,
            expected.content_hash_algorithm,
            expected.content_hash.casefold(),
        ):
            consistent += 1
        if actual is None:
            continue
        try:
            # 第二层由 Resolver 重新读取 locator; 无法解析不会被计为内容完整性样本。
            content = resolver.resolve(actual)
        except EvidenceResolutionError:
            continue
        resolved_contents.append((actual, content))

    # 第三层对成功解析的原文重算哈希, 与“引用对象字段看起来一致”分开统计。
    intact = sum(
        content_sha256(content, algorithm=citation.content_hash_algorithm).casefold()
        == citation.content_hash.casefold()
        for citation, content in resolved_contents
    )
    return CitationMetrics(
        reference_consistency=_citation_check_metrics(len(evidence), consistent),
        locator_resolvability=_citation_check_metrics(len(evidence), len(resolved_contents)),
        content_integrity=_citation_check_metrics(len(resolved_contents), intact),
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
    # 使用线性插值, 小样本也能得到确定且可手工复核的百分位数。
    position = (len(ordered) - 1) * percentile
    lower = math.floor(position)
    upper = math.ceil(position)
    if lower == upper:
        return ordered[lower]
    fraction = position - lower
    return ordered[lower] + (ordered[upper] - ordered[lower]) * fraction


def aggregate_metrics(results: Sequence[EvaluationSampleResult]) -> AggregateMetrics:
    """聚合完成样例,同时让失败样例继续出现在汇总计数中。"""
    # 质量均值只对有定义的完成结果计算; 失败数量仍由外层 Summary 单独报告。
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
        citation_reference_consistency=_defined_mean(
            metric.reference_consistency.score if metric is not None else None
            for metric in (result.citations for result in completed)
        ),
        citation_locator_resolvability=_defined_mean(
            metric.locator_resolvability.score if metric is not None else None
            for metric in (result.citations for result in completed)
        ),
        citation_content_integrity=_defined_mean(
            metric.content_integrity.score if metric is not None else None
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
    # 递归只接受 JSON 基本类型, 未知 Python 对象不会被字符串化后掩盖数据问题。
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, list):
        return [json_argument_value(item) for item in value]
    if isinstance(value, dict):
        return {str(key): json_argument_value(item) for key, item in value.items()}
    raise TypeError(f"tool argument is not JSON-compatible: {type(value).__name__}")
