"""带有明确降级路径的调查 Graph 节点实现。

本模块承载调查 Graph 的节点业务。每个节点读取 ``InvestigationState`` 的
有限字段并返回最小增量; reducer 再合并并行更新。工具和模型失败不会被吞掉, 而会转换为
``InvestigationError`` 并在可行时使用确定性降级路径继续生成诚实报告。
"""

import asyncio
import hashlib
from collections import Counter
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime
from typing import Generic, Literal, TypeVar

from langgraph.types import Command, interrupt
from pydantic import BaseModel, ValidationError

from incident_copilot.core.clock import Clock, utc_now
from incident_copilot.core.telemetry import trace_async
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
    RejectedHypothesis,
    RemediationStep,
    TimelineEvent,
)
from incident_copilot.domain.review import HumanFeedback, HumanReviewRequest, ReviewAction
from incident_copilot.graph.model import FakeModelProvider, ModelProvider
from incident_copilot.graph.routing import budget_stop_reason, decide_after_judge
from incident_copilot.graph.schemas import (
    ErrorCategory,
    HypothesesOutput,
    InvestigationError,
    InvestigationPlan,
    InvestigationStep,
    ModelContext,
    ModelTask,
    ModelUsage,
    PlanOutput,
    ReportDraftOutput,
    StepResult,
    StepStatus,
    StopReason,
    SufficiencyOutput,
    stable_query_key,
)
from incident_copilot.graph.state import InvestigationState, add_usage, merge_errors
from incident_copilot.tools.exceptions import ToolError, ToolExecutionError
from incident_copilot.tools.registry import ToolRegistry
from incident_copilot.tools.schemas import QueryContext

# 结构化模型调用返回的任务专属 Pydantic 类型。
OutputT = TypeVar("OutputT", bound=BaseModel)


@dataclass(frozen=True, slots=True)
class StructuredCall(Generic[OutputT]):
    """保存校验后的模型值和可由 Reducer 安全合并的节点增量。"""

    # 通过任务专属 Schema 校验后的模型值, 降级失败时为 None。
    value: OutputT | None
    # 本次 helper 实际消耗的模型调用次数。
    call_count: int
    # 本次 helper 累计的输入输出 Token 用量。
    usage: ModelUsage
    # 校验重试或 Provider 失败产生的安全错误。
    errors: tuple[InvestigationError, ...]
    # 因模型预算耗尽而必须停止时的原因。
    stop_reason: StopReason | None = None


class InvestigationNodes:
    """供编译后 Graph 使用的依赖注入式有界节点集合。

    ToolRegistry、ModelProvider 和 Clock 均从外部注入。节点不自行读取配置文件、
    Fixture 或网络, 因此同一套控制流可以切换离线和真实 Adapter。
    """

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

    @trace_async("incident_copilot.node.parse_incident", component="node")
    async def parse_incident(self, state: InvestigationState) -> InvestigationState:
        """在 Graph 边界接收已经过领域校验的事故信息。

        State 读取: ``incident.services``、``deadline_at``。
        State 写入: ``deadline_exceeded``、可信工具 attempt 上限; 已超时时额外写
        停止原因字段 ``stop_reason``。
        """
        if not state["incident"].services:
            raise ValueError("investigation requires at least one normalized service")
        # Registry 是工具重试策略的可信来源; 模型不能自行提高某个工具的尝试次数。
        deadline_exceeded = self._clock() >= state["deadline_at"]
        update: InvestigationState = {
            "deadline_exceeded": deadline_exceeded,
            "tool_attempt_limits": self._registry.attempt_limits,
        }
        if deadline_exceeded:
            update["stop_reason"] = StopReason.DEADLINE_EXCEEDED
        return update

    @trace_async("incident_copilot.node.build_investigation_plan", component="node")
    async def build_investigation_plan(self, state: InvestigationState) -> InvestigationState:
        """通过结构化输出 Schema 生成第一轮有界调查计划。

        State 读取: incident、research_round、已完成查询和模型/deadline 预算。
        State 写入: investigation_plan、pending_steps、模型计数/usage/errors 及停止字段。
        """
        return await self._plan_update(state, round_number=state["research_round"])

    @trace_async("incident_copilot.node.refine_investigation", component="node")
    async def refine_investigation(self, state: InvestigationState) -> InvestigationState:
        """由单一写入者递增研究轮次,并且只创建增量查询。

        State 读取: 上轮证据缺口、human_feedback、completed_steps、research_round 和预算。
        State 写入: 新 plan/pending_steps、``research_round + 1`` 及模型调用增量。
        """
        next_round = state["research_round"] + 1
        update = await self._plan_update(state, round_number=next_round)
        update["research_round"] = next_round
        return update

    def human_review(
        self, state: InvestigationState
    ) -> Command[Literal["refine_investigation", "__end__"]]:
        """在高风险建议进入确认报告前暂停调查。

        State 读取: ``final_report.remediation_steps``。
        State 写入: 接受时写 human_feedback/review_completed 并结束; 追加研究时还会重置
        evidence_sufficient 和 stop_reason, 然后跳转 refine。
        """
        report = state["final_report"]
        # 只把真正需要审批的动作暴露给审核者, 避免低风险建议制造无意义中断。
        high_risk_actions = tuple(
            step.action
            for step in report.remediation_steps
            if step.risk_level in {RiskLevel.HIGH, RiskLevel.CRITICAL}
        )
        request = HumanReviewRequest(
            report_id=report.report_id,
            reason="High-risk remediation requires explicit human confirmation.",
            high_risk_actions=high_risk_actions,
        )
        # interrupt 会把可序列化请求写入 checkpoint 并暂停当前 thread。
        # 恢复时节点从头重放,因此 interrupt 前不能执行非幂等外部副作用。
        raw_feedback = interrupt(request.model_dump(mode="json"))
        # 人工输入同样是不可信边界,必须重新通过 Pydantic 校验。
        feedback = HumanFeedback.model_validate(raw_feedback)
        if feedback.action is ReviewAction.ACCEPT:
            # 接受操作直接结束当前 thread, 不再重新执行报告或工具节点。
            return Command(
                update={"human_feedback": feedback, "review_completed": True},
                goto="__end__",
            )
        # “追加研究”清除上一轮停止结论, 但预算计数不会清零, 因而无法绕过硬上限。
        return Command(
            update={
                "human_feedback": feedback,
                "review_completed": False,
                "evidence_sufficient": False,
                "stop_reason": None,
            },
            goto="refine_investigation",
        )

    @trace_async("incident_copilot.node.collect_evidence", component="node")
    async def collect_evidence(self, state: InvestigationState) -> InvestigationState:
        """执行一个由 Send 限定范围的工具步骤,并把失败转换成 State 数据。

        State 读取: incident、current_step、deadline_at。
        State 写入: completed_steps; 成功时写 evidence/tool success 增量, 失败时写 errors/
        tool failure 增量。每个 ``Send`` 分支只执行一个步骤。
        """
        step = state["current_step"]
        started_at = self._clock()
        # Send 分支只拿到本步骤允许的物理尝试数, Registry 无法消耗其他分支的额度。
        context = QueryContext(
            correlation_id=f"{state['incident'].incident_id}:{step.step_id}",
            deadline=state["deadline_at"],
            remaining_tool_attempts=state["current_step_attempt_limit"],
        )
        try:
            # Registry 统一负责参数 Schema、白名单、超时、重试和输出边界。
            result = await self._registry.execute(step.tool_name, step.arguments, context)
        except ToolError as exc:
            # 工具失败被转换为可合并的 State 数据; 单个 Provider 失败不会使整个 Graph 崩溃。
            completed_at = self._clock()
            error = self._tool_error(step.step_id, step.tool_name, exc, completed_at)
            attempts = exc.attempts if isinstance(exc, ToolExecutionError) else 1
            step_result = StepResult(
                step_id=step.step_id,
                query_key=step.query_key,
                tool_name=step.tool_name,
                arguments=step.arguments,
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
                "tool_attempt_count": attempts,
                "tool_failure_count": 1,
            }

        completed_at = self._clock()
        # 完整 Evidence 在 Provider 边界校验后投影为轻量引用, 再进入有界 Graph State。
        refs = tuple(EvidenceRef.from_evidence(item) for item in result.evidence)
        step_result = StepResult(
            step_id=step.step_id,
            query_key=step.query_key,
            tool_name=step.tool_name,
            arguments=step.arguments,
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
            "tool_attempt_count": result.attempts,
            "tool_success_count": 1,
        }

    @trace_async("incident_copilot.node.aggregate_evidence", component="node")
    async def aggregate_evidence(self, state: InvestigationState) -> InvestigationState:
        """当前批次的所有分支到达屏障后,标记确定性的硬停止原因。

        State 读取: deadline、工具/模型/Token 预算和 reducer 已汇合的计数。
        State 写入: ``deadline_exceeded``; 命中硬边界时写 ``stop_reason``。
        """
        deadline_exceeded = self._clock() >= state["deadline_at"]
        # 先构造包含本节点判断的临时 State, 再复用统一预算函数, 避免各节点规则漂移。
        projected = state.copy()
        projected["deadline_exceeded"] = deadline_exceeded
        update: InvestigationState = {"deadline_exceeded": deadline_exceeded}
        reason = budget_stop_reason(projected)
        if reason is not None:
            update["stop_reason"] = reason
        return update

    @trace_async("incident_copilot.node.generate_hypotheses", component="node")
    async def generate_hypotheses(self, state: InvestigationState) -> InvestigationState:
        """生成通过 Schema 校验的假设,失败时使用确定性降级结果。

        State 读取: incident、evidence 摘要、research_round、历史错误和模型预算。
        State 写入: hypotheses、model_call_count、model_usage、errors 和可选 stop_reason。
        """
        context = self._model_context(state, ModelTask.HYPOTHESES)
        call = await self._call_structured(state, context, HypothesesOutput)
        # 真实模型不可用或输出校验失败时, Fake Model 只负责生成结构合法的诚实降级结果。
        output = call.value or await self._fallback(context, HypothesesOutput)
        return {
            "hypotheses": output.hypotheses,
            "model_call_count": call.call_count,
            "model_usage": call.usage,
            "errors": call.errors,
            **({"stop_reason": call.stop_reason} if call.stop_reason is not None else {}),
        }

    @trace_async("incident_copilot.node.verify_hypotheses", component="node")
    async def verify_hypotheses(self, state: InvestigationState) -> InvestigationState:
        """在模型之外强制校验证据外键和置信度策略。

        State 读取: evidence、hypotheses。
        State 写入: 过滤无效 Evidence ID、从证据推导服务,并按显式规则排序后的 hypotheses。
        """
        evidence_by_id = {item.evidence_id: item for item in state.get("evidence", ())}
        verified: list[Hypothesis] = []
        for hypothesis in state.get("hypotheses", ()):
            # LLM 返回的 Evidence ID 是不可信外键, 只能保留当前 State 中真实存在的记录。
            supporting_ids = tuple(
                item for item in hypothesis.supporting_evidence_ids if item in evidence_by_id
            )
            contradicting_ids = tuple(
                item
                for item in hypothesis.contradicting_evidence_ids
                if item in evidence_by_id and item not in supporting_ids
            )
            supporting_sources = {evidence_by_id[item].source_type for item in supporting_ids}
            contradicting_sources = {evidence_by_id[item].source_type for item in contradicting_ids}
            # 状态由多源证据覆盖决定, 而不是照抄模型声明的 supported/rejected。
            if contradicting_ids and len(contradicting_sources) >= len(supporting_sources):
                status = HypothesisStatus.REJECTED
            elif supporting_ids and len(supporting_sources) >= 2:
                status = HypothesisStatus.SUPPORTED
            else:
                status = HypothesisStatus.INCONCLUSIVE
            confidence = hypothesis.confidence
            # 单源或无证据假设强制压低置信度, 防止语言模型给出没有依据的高分。
            if status is HypothesisStatus.REJECTED or not supporting_ids:
                confidence = min(confidence, 0.2)
            elif len(supporting_sources) < 2:
                confidence = min(confidence, 0.55)
            # 受影响服务同样从被验证证据反推, 不信任模型自由填写的服务名称。
            affected_services = tuple(
                dict.fromkeys(
                    evidence_by_id[item].service
                    for item in (*supporting_ids, *contradicting_ids)
                    if evidence_by_id[item].service is not None
                )
            )
            verified.append(
                Hypothesis.model_validate(
                    {
                        **hypothesis.model_dump(mode="python"),
                        "supporting_evidence_ids": supporting_ids,
                        "contradicting_evidence_ids": contradicting_ids,
                        "affected_services": affected_services,
                        "confidence": confidence,
                        "status": status,
                    }
                )
            )
        status_rank = {
            HypothesisStatus.SUPPORTED: 0,
            HypothesisStatus.INVESTIGATING: 1,
            HypothesisStatus.PROPOSED: 2,
            HypothesisStatus.INCONCLUSIVE: 3,
            HypothesisStatus.REJECTED: 4,
        }
        # 报告默认取第一条作为 leading hypothesis, 所以此处排序是业务优先级的一部分。
        verified.sort(
            key=lambda item: (
                status_rank[item.status],
                -item.confidence,
                -len(item.supporting_evidence_ids),
                item.hypothesis_id,
            )
        )
        return {"hypotheses": tuple(verified)}

    @trace_async("incident_copilot.node.judge_evidence", component="node")
    async def judge_evidence(self, state: InvestigationState) -> InvestigationState:
        """结合结构化模型判断、确定性充分性条件和停止规则。

        State 读取: evidence 来源覆盖、verified hypotheses、轮次和全部预算。
        State 写入: evidence_sufficient、sufficiency_reason、next queries、deadline、模型
        调用增量、errors 和 stop_reason。真正的下一节点仍由纯路由函数决定。
        """
        context = self._model_context(state, ModelTask.JUDGE)
        call = await self._call_structured(state, context, SufficiencyOutput)
        if call.value is None:
            output = await self._fallback(context, SufficiencyOutput)
        else:
            output = call.value
        # 模型的 sufficient 只是候选意见; 至少一个受支持假设和两个来源才满足硬条件。
        supported = any(
            item.status is HypothesisStatus.SUPPORTED for item in state.get("hypotheses", ())
        )
        sources = {item.source_type for item in state.get("evidence", ())}
        sufficient = output.sufficient and supported and len(sources) >= 2
        deadline_exceeded = self._clock() >= state["deadline_at"]
        # 路由函数需要看到“本次模型调用之后”的累计预算, 因此先在副本中投影增量。
        projected: InvestigationState = state.copy()
        projected["evidence_sufficient"] = sufficient
        projected["deadline_exceeded"] = deadline_exceeded
        projected["model_call_count"] = state.get("model_call_count", 0) + call.call_count
        projected["model_usage"] = add_usage(state.get("model_usage", ModelUsage()), call.usage)
        if call.stop_reason is not None:
            projected["stop_reason"] = call.stop_reason
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
        elif call.stop_reason is not None:
            update["stop_reason"] = call.stop_reason
        return update

    @trace_async("incident_copilot.node.generate_report", component="node")
    async def generate_report(self, state: InvestigationState) -> InvestigationState:
        """构造诚实的领域报告,并且只附加已验证的 Evidence ID。

        State 读取: incident、evidence、hypotheses、errors、预算计数、stop_reason 和时间。
        State 写入: final_report 及本次模型调用增量。报告引用只能从 State 中已经存在的
        EvidenceRef 生成, 模型草稿不能凭空创建 citation。
        """
        context = self._model_context(state, ModelTask.REPORT)
        call = await self._call_structured(state, context, ReportDraftOutput)
        # 模型只提供文字草稿; 证据、引用、置信度和统计数据全部由可信代码重建。
        draft = call.value or await self._fallback(context, ReportDraftOutput)
        completed_at = self._clock()
        evidence = state.get("evidence", ())
        evidence_by_id = {item.evidence_id: item for item in evidence}
        hypotheses = state.get("hypotheses", ())
        # verify_hypotheses 已按状态、置信度和证据数排序, 第一项才可作为主假设。
        leading = hypotheses[0] if hypotheses else None
        supporting_ids = leading.supporting_evidence_ids if leading is not None else ()
        rejected = tuple(item for item in hypotheses if item.status is HypothesisStatus.REJECTED)
        contradicting_ids = tuple(
            dict.fromkeys(
                item
                for hypothesis in rejected
                for item in hypothesis.contradicting_evidence_ids
                if item not in supporting_ids
            )
        )
        supporting = tuple(
            evidence_by_id[item] for item in supporting_ids if item in evidence_by_id
        )
        contradicting = tuple(
            evidence_by_id[item] for item in contradicting_ids if item in evidence_by_id
        )
        if not supporting:
            # 没有主假设证据时只选高相关证据作调查摘要, 仍不会把根因标记为 probable。
            supporting = tuple(item for item in evidence if item.relevance_score >= 0.75)[:20]
        affected_services = (
            leading.affected_services
            if leading is not None and leading.affected_services
            else tuple(
                dict.fromkeys(item.service for item in supporting if item.service is not None)
            )
        )
        # Citation 必须从真实 EvidenceRef 提取并按 citation_id 去重, 模型草稿无权新增。
        citations = tuple(
            {
                item.citation.citation_id: item.citation for item in (*supporting, *contradicting)
            }.values()
        )
        # disposition 由可信停止原因和真实 supporting evidence 共同决定。
        timeline = self._timeline((*supporting, *contradicting))
        stop_reason = state.get("stop_reason") or StopReason.MAX_RESEARCH_ROUNDS
        disposition = (
            ReportDisposition.PROBABLE
            if stop_reason is StopReason.EVIDENCE_SUFFICIENT and bool(supporting)
            else ReportDisposition.INCONCLUSIVE
        )
        source_counts = Counter(item.source_type for item in evidence)
        # 缺失来源、降级错误和停止原因都会进入 limitations, 避免报告隐藏调查缺口。
        missing_sources = [source.value for source in SourceType if source_counts[source] == 0]
        limitations = (
            [f"missing evidence sources: {', '.join(missing_sources)}"] if missing_sources else []
        )
        all_errors = merge_errors(state.get("errors", ()), call.errors)
        if all_errors:
            limitations.append(f"{len(all_errors)} tool/model error(s) were degraded")
        if disposition is ReportDisposition.INCONCLUSIVE:
            limitations.append(f"research stopped because {stop_reason.value}")
        usage = add_usage(state.get("model_usage", ModelUsage()), call.usage)
        total_model_calls = state.get("model_call_count", 0) + call.call_count
        duration_ms = max(0, int((completed_at - state["started_at"]).total_seconds() * 1_000))
        # 到这里才一次性构造领域报告, Pydantic 会再次校验引用和字段之间的契约。
        report = IncidentReport(
            report_id=f"rpt_{state['incident'].incident_id.removeprefix('inc_')}",
            incident_id=state["incident"].incident_id,
            summary=draft.summary,
            root_cause=(draft.root_cause if disposition is ReportDisposition.PROBABLE else None),
            disposition=disposition,
            confidence=(
                leading.confidence
                if disposition is ReportDisposition.PROBABLE and leading is not None
                else min(leading.confidence, 0.55)
                if leading is not None
                else 0.0
            ),
            confidence_rationale=draft.confidence_rationale,
            affected_services=affected_services,
            timeline=timeline,
            supporting_evidence=supporting,
            contradicting_evidence=contradicting,
            rejected_hypotheses=tuple(
                RejectedHypothesis(
                    hypothesis_id=item.hypothesis_id,
                    description=item.description,
                    rejection_reason=item.reasoning_summary,
                    evidence_ids=item.contradicting_evidence_ids,
                )
                for item in rejected
            ),
            remediation_steps=tuple(
                RemediationStep(
                    action=action,
                    priority=index,
                    risk_level=(RiskLevel.HIGH if index == 1 else RiskLevel.MEDIUM),
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
                f"{state.get('tool_call_count', 0)} logical tool step(s) and "
                f"{state.get('tool_attempt_count', 0)} physical attempt(s)."
            ),
            investigation_stats=InvestigationStats(
                research_rounds=state["research_round"],
                tool_call_count=state.get("tool_call_count", 0),
                tool_attempt_count=state.get("tool_attempt_count", 0),
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
        """校验模型计划并由可信代码重算 step/query identity 和轮次。"""
        context = self._model_context(state, ModelTask.PLAN, round_number=round_number)
        call = await self._call_structured(state, context, PlanOutput)
        output = call.value or await self._fallback(context, PlanOutput)
        # 工具白名单来自 Registry; 模型输出未知工具时直接丢弃, 不进入后续 Send。
        allowed = set(self._registry.tool_names)
        completed_queries = {item.query_key for item in state.get("completed_steps", ())}
        seen_queries: set[str] = set()
        normalized_steps: list[InvestigationStep] = []
        for ordinal, step in enumerate(output.steps, start=1):
            if step.tool_name not in allowed:
                continue
            query_key = stable_query_key(step.tool_name, step.arguments)
            # query_key 同时过滤历史已执行查询和本计划内部重复, 保证恢复与重放幂等。
            if query_key in completed_queries or query_key in seen_queries:
                continue
            seen_queries.add(query_key)
            # 不信任模型提供的 step_id/query_key/round,统一根据规范参数重建。
            normalized_steps.append(
                InvestigationStep.model_validate(
                    {
                        **step.model_dump(mode="python"),
                        "step_id": f"step_r{round_number}_{ordinal}_{query_key[:12]}",
                        "query_key": query_key,
                        "round_number": round_number,
                    }
                )
            )
        steps = tuple(normalized_steps)
        # plan_id 由规范化步骤派生, 相同输入在重放时会产生相同标识。
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
        deadline_exceeded = self._clock() >= state["deadline_at"]
        # 将本次模型用量投影到累计值后再判断预算, 防止最后一次调用结束后仍继续分发工具。
        projected = state.copy()
        projected["deadline_exceeded"] = deadline_exceeded
        projected["model_call_count"] = state.get("model_call_count", 0) + call.call_count
        projected["model_usage"] = add_usage(state.get("model_usage", ModelUsage()), call.usage)
        update: InvestigationState = {
            "investigation_plan": plan,
            "pending_steps": steps,
            "model_call_count": call.call_count,
            "model_usage": call.usage,
            "errors": call.errors,
            "deadline_exceeded": deadline_exceeded,
        }
        reason = budget_stop_reason(projected)
        if call.stop_reason is not None:
            update["stop_reason"] = call.stop_reason
        elif reason is not None:
            update["stop_reason"] = reason
        return update

    @trace_async("incident_copilot.model.structured_complete", component="model")
    async def _call_structured(
        self,
        state: InvestigationState,
        context: ModelContext,
        schema: type[OutputT],
    ) -> StructuredCall[OutputT]:
        """在调用/Token/deadline 预算内执行最多两次结构化模型尝试。

        返回值同时携带本节点增量计数、usage、错误和可能的停止原因, 供 State reducer
        安全合并。任何异常都不会把未经校验的 payload 传给下游节点。
        """
        remaining = max(0, state["max_model_calls"] - state.get("model_call_count", 0))
        prior_usage = state.get("model_usage", ModelUsage())
        # 离线模式没有厂商 tokenizer, 统一采用可复现的字符数估算并明确标记 estimated。
        estimated_input_tokens = max(1, len(context.model_dump_json()) // 4)
        tokens_exhausted = (
            state.get("stop_reason") is StopReason.TOKEN_BUDGET_EXHAUSTED
            or prior_usage.input_tokens + prior_usage.output_tokens + estimated_input_tokens
            >= state["max_estimated_tokens"]
        )
        deadline_exceeded = (
            state.get("stop_reason") is StopReason.DEADLINE_EXCEEDED
            or self._clock() >= state["deadline_at"]
        )
        # 结构化输出最多尝试两次; 硬预算不足时一次也不调用 Provider。
        max_attempts = 0 if tokens_exhausted or deadline_exceeded else min(2, remaining)
        call_count = 0
        errors: list[InvestigationError] = []
        usage = ModelUsage()
        for attempt in range(1, max_attempts + 1):
            # 第二次结构修复尝试前再次检查 Token 预算,避免重试越界。
            projected_retry_tokens = (
                prior_usage.input_tokens
                + prior_usage.output_tokens
                + usage.input_tokens
                + usage.output_tokens
                + estimated_input_tokens
            )
            if attempt > 1 and projected_retry_tokens >= state["max_estimated_tokens"]:
                tokens_exhausted = True
                errors.append(
                    self._model_error(
                        context.task,
                        context.research_round,
                        attempt,
                        RuntimeError("estimated token budget exhausted before retry"),
                        category=ErrorCategory.BUDGET,
                    )
                )
                break
            remaining_seconds = (state["deadline_at"] - self._clock()).total_seconds()
            if remaining_seconds <= 0:
                deadline_exceeded = True
                errors.append(
                    self._model_error(
                        context.task,
                        context.research_round,
                        max(1, call_count + 1),
                        TimeoutError("investigation deadline exceeded"),
                        category=ErrorCategory.TIMEOUT,
                    )
                )
                break
            call_count += 1
            try:
                # 总调查 deadline 同时充当本次模型调用的最大剩余 timeout。
                response = await asyncio.wait_for(
                    self._model.complete(context), timeout=remaining_seconds
                )
                usage = add_usage(usage, response.usage)
                value = schema.model_validate(response.payload)
            except TimeoutError as exc:
                deadline_exceeded = True
                errors.append(
                    self._model_error(
                        context.task,
                        context.research_round,
                        attempt,
                        exc,
                        category=ErrorCategory.TIMEOUT,
                    )
                )
                break
            except (ValidationError, ValueError, TypeError) as exc:
                # Schema 错误允许有限修复重试, 但原始未校验 payload 永远不会传给下游。
                errors.append(self._model_error(context.task, context.research_round, attempt, exc))
                if (
                    prior_usage.input_tokens
                    + prior_usage.output_tokens
                    + usage.input_tokens
                    + usage.output_tokens
                    >= state["max_estimated_tokens"]
                ):
                    tokens_exhausted = True
                    break
            except Exception as exc:
                # 连接类异常与内部异常分类保存, 最终报告只展示脱敏后的结构化错误。
                errors.append(
                    self._model_error(
                        context.task,
                        context.research_round,
                        attempt,
                        exc,
                        category=(
                            ErrorCategory.UNAVAILABLE
                            if isinstance(exc, (ConnectionError, OSError))
                            else ErrorCategory.INTERNAL
                        ),
                    )
                )
            else:
                # 只有 Pydantic 校验成功的结构值才作为本节点结果返回。
                return StructuredCall(value, call_count, usage, tuple(errors))
        if max_attempts == 0:
            if deadline_exceeded:
                message = "investigation deadline exceeded"
                category = ErrorCategory.TIMEOUT
            elif tokens_exhausted:
                message = "estimated token budget exhausted"
                category = ErrorCategory.BUDGET
            else:
                message = "model call budget exhausted"
                category = ErrorCategory.BUDGET
            errors.append(
                self._model_error(
                    context.task,
                    context.research_round,
                    1,
                    RuntimeError(message),
                    category=category,
                )
            )
        if (
            prior_usage.input_tokens
            + prior_usage.output_tokens
            + usage.input_tokens
            + usage.output_tokens
            >= state["max_estimated_tokens"]
        ):
            tokens_exhausted = True
        # 失败后只传播最强的硬停止原因; 普通格式错误会由调用节点进入 Fake 降级。
        stop_reason: StopReason | None = None
        if deadline_exceeded:
            stop_reason = StopReason.DEADLINE_EXCEEDED
        elif tokens_exhausted:
            stop_reason = StopReason.TOKEN_BUDGET_EXHAUSTED
        elif remaining == 0:
            stop_reason = StopReason.MODEL_BUDGET_EXHAUSTED
        return StructuredCall(None, call_count, usage, tuple(errors), stop_reason)

    async def _fallback(self, context: ModelContext, schema: type[OutputT]) -> OutputT:
        # Fake Provider 仍经过同一输出 Schema, 降级路径不会绕开结构化校验。
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
        # 只给模型有界摘要和 Evidence ID, 不把大块原始日志或秘密载荷复制进上下文。
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
            symptoms=incident.symptoms,
            start_time=incident.start_time,
            end_time=incident.end_time,
            research_round=round_number or state["research_round"],
            evidence_summaries=evidence_summaries,
            hypotheses=state.get("hypotheses", ()),
            next_investigation_queries=state.get("next_investigation_queries", ()),
            human_feedback=state.get("human_feedback"),
            error_count=len(state.get("errors", ())),
        )

    def _tool_error(
        self, step_id: str, tool_name: str, exc: ToolError, occurred_at: datetime
    ) -> InvestigationError:
        # Registry 执行错误保留类别/重试信息; 参数等前置错误统一归为验证失败。
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
        # 原始异常不进入 Graph State, 只保留稳定任务、类别和尝试次数。
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
