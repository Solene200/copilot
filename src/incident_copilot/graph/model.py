"""结构化模型端口和确定性离线实现。

Graph 只依赖 ``ModelProvider`` 协议,不依赖具体模型 SDK。模型返回的
payload 被视为不可信 JSON, 必须由 ``nodes.py`` 使用任务对应的 Pydantic Schema 校验。
``FakeModelProvider`` 是无网络、可复现的教学与测试实现, 不代表真实 LLM 诊断能力。
"""

import hashlib
from datetime import timedelta
from enum import StrEnum
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

PlanSpec = tuple[str, SourceType, str, dict[str, object], int]


class InvestigationScenario(StrEnum):
    """Fake Planner 可解释、互斥且不依赖样例身份的场景分类。"""

    DATABASE_POOL = "database_connection_pool"
    DNS = "dns_name_resolution"
    CACHE = "cache_regression"
    GENERAL = "general_service_degradation"


class ModelProvider(Protocol):
    """返回不可信 JSON-like 结构化输出的厂商无关边界。

    端口只允许一个 ``complete`` 操作。任务类型、裁剪后的证据和研究轮次通过
    ``ModelContext`` 传入, 厂商客户端和 API Key 不会泄漏到 Graph 节点。
    """

    async def complete(self, context: ModelContext) -> ModelResponse:
        """执行一个白名单内的结构化任务。"""
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
    # 工具名和规范参数共同决定查询身份, 使计划重放不会产生新的逻辑步骤。
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
    """无网络访问、由证据驱动的确定性模型替代实现。

    Fake 根据当前上下文生成结构化计划、假设、充分性和报告草稿。它不读取 Fixture
    ground truth, 也不会硬编码每个评估样例的最终答案。
    """

    def __init__(self, *, minimum_research_rounds: int = 1) -> None:
        if minimum_research_rounds < 1:
            raise ValueError("minimum_research_rounds must be positive")
        self._minimum_research_rounds = minimum_research_rounds

    async def complete(self, context: ModelContext) -> ModelResponse:
        """生成任务对应的 Pydantic 输出,并通过 JSON 模式序列化。

        按白名单内的 ModelTask 分派到确定性函数,再序列化为 JSON-like payload。
        Usage 是基于字符数的估算值, 所以必须设置 ``estimated=True``。
        """
        output: PlanOutput | HypothesesOutput | SufficiencyOutput | ReportDraftOutput
        # task 是枚举白名单, 这里不会根据模型文本或用户输入动态调用任意函数。
        if context.task is ModelTask.PLAN:
            output = self._plan(context)
        elif context.task is ModelTask.HYPOTHESES:
            output = self._hypotheses(context)
        elif context.task is ModelTask.JUDGE:
            output = self._judge(context)
        else:
            output = self._report(context)
        # 即使是 Fake Provider 也模拟真实 Provider 的 JSON 边界, 让节点统一执行二次校验。
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
        """根据研究轮次和上一轮缺口生成有界只读工具步骤。"""
        service = context.service
        start = context.start_time
        end = context.end_time
        # 首轮追求多源覆盖; 后续轮优先消费 judge 或人工审核提出的增量查询。
        if context.research_round == 1:
            specs = self._initial_specs(context)
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
        # 规格元组在这里统一转换为强类型步骤, 避免各场景重复构造 ID 和轮次字段。
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
            objective=(
                f"Explain {service} failures for the {self._scenario(context).value} scenario "
                "using independent, citable evidence."
            ),
            steps=steps,
            rationale=(
                "Collect symptoms, causal changes, dependency context, and operational knowledge."
            ),
        )

    @staticmethod
    def _scenario(context: ModelContext) -> InvestigationScenario:
        """只从公开上下文和当前证据文本分类,不读取 incident ID 或 fixture 元数据。"""
        evidence_text = " ".join(
            str(item.get("summary", "")) for item in context.evidence_summaries
        )
        # 只使用用户症状和已检索证据分类, 故意不读取 incident_id 或评估标签。
        text = " ".join((context.raw_query, *context.symptoms, evidence_text)).casefold()
        if any(term in text for term in ("dns", "name resolution", "name lookup", "resolver")):
            return InvestigationScenario.DNS
        if any(term in text for term in ("cache", "ttl", "read amplification")):
            return InvestigationScenario.CACHE
        if any(
            term in text
            for term in ("connection pool", "connection acquisition", "db pool", "pool saturation")
        ):
            return InvestigationScenario.DATABASE_POOL
        return InvestigationScenario.GENERAL

    def _initial_specs(self, context: ModelContext) -> tuple[PlanSpec, ...]:
        """把场景规则转换为六类通用证据查询; pool 额外查询相似事故。"""
        scenario = self._scenario(context)
        service = context.service
        start = context.start_time
        end = context.end_time
        route_name = service.removesuffix("-service")
        # 场景仅选择查询词和指标白名单, 不直接决定最终根因或评估答案。
        if scenario is InvestigationScenario.DATABASE_POOL:
            log_query = "connection acquisition"
            metric_name, aggregation = "db.pool.utilization", "max"
            operation = f"POST /{route_name}s"
            knowledge_query = "connection pool timeout"
        elif scenario is InvestigationScenario.DNS:
            log_query = "DNS lookup timeout"
            metric_name, aggregation = "http.server.error_rate", "rate"
            operation = f"GET /{route_name}"
            knowledge_query = "DNS resolver lookup timeout"
        elif scenario is InvestigationScenario.CACHE:
            log_query = "cache miss"
            metric_name, aggregation = "process.cpu.utilization", "max"
            operation = f"GET /{route_name}"
            knowledge_query = "cache TTL read amplification"
        else:
            log_query = "timeout"
            metric_name, aggregation = "http.server.error_rate", "rate"
            operation = f"GET /{route_name}"
            knowledge_query = "service timeout"
        # 所有时序工具共享同一个故障窗口, 防止不同来源因时间范围不一致而无法关联。
        common_range = {
            "service": service,
            "start_time": start.isoformat(),
            "end_time": end.isoformat(),
        }
        specs: tuple[PlanSpec, ...] = (
            (
                "search_logs",
                SourceType.LOG,
                f"Find {scenario.value} symptoms in service logs.",
                {**common_range, "query": log_query, "limit": 10},
                100,
            ),
            (
                "query_metrics",
                SourceType.METRIC,
                f"Measure the primary signal for {scenario.value}.",
                {
                    **common_range,
                    "metric_name": metric_name,
                    "aggregation": aggregation,
                    "limit": 10,
                },
                100,
            ),
            (
                "query_traces",
                SourceType.TRACE,
                "Locate the blocking or timed-out request span.",
                {
                    **common_range,
                    "operation": operation,
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
                    **common_range,
                    "start_time": (start - timedelta(minutes=30)).isoformat(),
                    "change_type": "configuration",
                    "limit": 10,
                },
                95,
            ),
            (
                "get_service_topology",
                SourceType.TOPOLOGY,
                "Identify critical dependencies for a competing hypothesis.",
                {"service": service, "at_time": start.isoformat(), "depth": 1, "limit": 10},
                80,
            ),
            (
                "search_runbooks",
                SourceType.KNOWLEDGE,
                f"Find vetted guidance for {scenario.value}.",
                {"service": service, "query": knowledge_query, "limit": 5},
                75,
            ),
        )
        if scenario is not InvestigationScenario.DATABASE_POOL:
            return specs
        # 相似事故查询只用于连接池场景的教学数据, 仍然通过 KnowledgeProvider 端口执行。
        return (
            *specs,
            (
                "search_similar_incidents",
                SourceType.KNOWLEDGE,
                "Compare prior incidents with the same failure signature.",
                {
                    "service": service,
                    "query": knowledge_query,
                    "before_time": start.isoformat(),
                    "lookback_days": 90,
                    "limit": 5,
                },
                70,
            ),
        )

    @staticmethod
    def _follow_up_specs(
        context: ModelContext,
    ) -> tuple[tuple[str, SourceType, str, dict[str, object], int], ...]:
        """把有界且厂商无关的后续调查意图转换为离线演示步骤。

        将 judge 或人工审核给出的 VerificationQuery 转为已有工具 Schema。这里仍受
        工具类型和最多 20 个步骤限制, 人工反馈不能引入任意执行能力。
        """
        feedback = context.human_feedback
        queries = (
            feedback.requested_queries
            if feedback is not None and feedback.requested_queries
            else context.next_investigation_queries
        )
        # 人工反馈优先于上一轮自动建议, 但两者都只能映射到已有只读工具 Schema。
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
                    # 自然语言不能直接成为 PromQL; 这里只能选择预先批准的领域指标名。
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
                # Pydantic 限制之外再设置转换上限, 防止一个反馈展开成过多并行步骤。
                if len(specs) == 20:
                    return tuple(specs)
        return tuple(specs)

    def _hypotheses(self, context: ModelContext) -> HypothesesOutput:
        """从证据语义构造一个领先假设和一个可证伪的竞争假设。"""
        scenario = self._scenario(context)
        # 先按确定性相关性阈值过滤, Fake Model 不会把所有证据都包装成“支持根因”。
        relevant = tuple(
            item
            for item in context.evidence_summaries
            if self._numeric_score(item.get("relevance_score")) >= 0.75
        )
        # 主假设要求症状、指标和变更链; 拓扑与 Trace 用来构造可证伪的竞争假设。
        lead_sources = {SourceType.LOG.value, SourceType.METRIC.value, SourceType.CHANGE.value}
        supporting = tuple(
            str(item["evidence_id"])
            for item in relevant
            if str(item.get("source_type")) in lead_sources
        )[:20]
        topology = tuple(
            str(item["evidence_id"])
            for item in relevant
            if str(item.get("source_type")) == SourceType.TOPOLOGY.value
        )[:5]
        trace = tuple(
            str(item["evidence_id"])
            for item in relevant
            if str(item.get("source_type")) == SourceType.TRACE.value
        )[:5]
        summaries = " ".join(
            str(item.get("summary", "")).strip()
            for item in relevant
            if str(item.get("evidence_id")) in supporting
        )[:1_400]
        lead_text, alternative_text = self._hypothesis_text(scenario, context.service)
        leading = self._make_hypothesis(
            context=context,
            role="leading",
            description=f"{lead_text} Evidence: {summaries}"[:2_000],
            supporting_ids=supporting,
            contradicting_ids=(),
            confidence=min(0.9, 0.5 + len(supporting) * 0.1),
            verification_query="Correlate the suspected configuration with symptoms and metrics.",
            reasoning=(
                "The rule selected mutually reinforcing log, metric, and change evidence from the "
                "current evidence packet; it did not read evaluator labels or fixture identity."
            ),
        )
        alternative = self._make_hypothesis(
            context=context,
            role="alternative",
            description=alternative_text,
            supporting_ids=topology,
            contradicting_ids=trace,
            confidence=0.35,
            verification_query="Check whether the dependency was slow before the local failure.",
            reasoning=(
                "Topology makes this dependency hypothesis plausible, but the cited request trace "
                "places the blocking work in the local scenario-specific path and therefore "
                "rejects it."
            ),
        )
        # 故意不按置信度返回,证明可信验证节点而非 Provider 返回顺序负责排序。
        return HypothesesOutput(hypotheses=(alternative, leading))

    @staticmethod
    def _numeric_score(value: JsonValue | None) -> float:
        if isinstance(value, (int, float)) and not isinstance(value, bool):
            return float(value)
        return 0.0

    @staticmethod
    def _hypothesis_text(scenario: InvestigationScenario, service: str) -> tuple[str, str]:
        # 每个场景同时给出主假设和竞争假设, 后者用于展示反证与拒绝流程。
        if scenario is InvestigationScenario.DATABASE_POOL:
            return (
                f"A database connection pool limit regression saturated {service} and caused "
                "connection acquisition timeouts.",
                "An external downstream dependency caused the request timeouts.",
            )
        if scenario is InvestigationScenario.DNS:
            return (
                f"A DNS resolver configuration regression made name resolution unreachable for "
                f"{service}, causing lookup timeouts.",
                "The downstream application dependency itself caused the request timeouts.",
            )
        if scenario is InvestigationScenario.CACHE:
            return (
                f"A cache TTL configuration regression disabled effective caching in {service}, "
                "causing cache misses and database read amplification.",
                "Database capacity alone caused the latency regression.",
            )
        return (
            f"A recent configuration regression caused the observed degradation in {service}.",
            "A downstream dependency caused the observed degradation.",
        )

    @staticmethod
    def _make_hypothesis(
        *,
        context: ModelContext,
        role: str,
        description: str,
        supporting_ids: tuple[str, ...],
        contradicting_ids: tuple[str, ...],
        confidence: float,
        verification_query: str,
        reasoning: str,
    ) -> Hypothesis:
        # 假设 ID 只由稳定业务内容生成, 与进程、当前时间和返回顺序无关。
        digest = hashlib.sha256(
            "|".join(
                (context.service, role, *supporting_ids, "against", *contradicting_ids)
            ).encode("utf-8")
        ).hexdigest()[:24]
        return Hypothesis(
            hypothesis_id=f"hyp_{digest}",
            description=description,
            affected_services=(),
            supporting_evidence_ids=supporting_ids,
            contradicting_evidence_ids=contradicting_ids,
            confidence=confidence,
            status=HypothesisStatus.PROPOSED,
            verification_queries=(
                VerificationQuery(
                    query=verification_query,
                    source_types=(SourceType.METRIC, SourceType.LOG, SourceType.TRACE),
                    service=context.service,
                ),
            ),
            reasoning_summary=reasoning,
            version=context.research_round,
        )

    def _judge(self, context: ModelContext) -> SufficiencyOutput:
        """根据来源覆盖、研究轮次和假设存在性产生结构化充分性建议。"""
        source_types = {str(item["source_type"]) for item in context.evidence_summaries}
        # Fake Judge 只给出结构化建议; 节点层还会验证 supported 状态和硬预算。
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
        """生成叙事草稿; 最终引用、风险和 disposition 仍由可信节点代码决定。"""
        # 草稿可以复述已验证主假设, 但没有假设时必须明确承认证据不足。
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
