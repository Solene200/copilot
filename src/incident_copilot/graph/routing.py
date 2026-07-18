"""Pure, exhaustively tested investigation loop routing policy.

中文教学说明: 路由函数只读取 State 并返回预声明节点名, 不访问网络也不调用模型。
调查循环的终止权因此掌握在确定性代码中。优先级是 deadline 和硬预算、证据充分、
最大研究轮数, 最后才允许进入下一轮 refine。
"""

from dataclasses import dataclass
from typing import Literal

from incident_copilot.domain.common import RiskLevel
from incident_copilot.graph.schemas import RouteTarget, StopReason
from incident_copilot.graph.state import InvestigationState


@dataclass(frozen=True, slots=True)
class RouteDecision:
    """A route target coupled to its auditable reason when research stops."""

    target: RouteTarget
    stop_reason: StopReason | None


def budget_stop_reason(state: InvestigationState) -> StopReason | None:
    """Return the highest-priority hard budget stop, independent of model judgement.

    中文: 读取 stop_reason、deadline、工具/模型计数和 Token usage; 不写 State。返回首个
    命中的硬停止原因, 让所有入口复用相同预算政策。
    """
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
    """Apply non-model stop rules in fixed priority order.

    中文: 读取预算、evidence_sufficient 和研究轮次; 不写 State。只有全部边界允许且证据
    仍不足时才返回 REFINE, 从而保证调查循环有明确上限。
    """
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
    """Skip every external call when the invocation is already out of time.

    中文: 读取 deadline 相关字段; 不写 State。已超时请求直接生成受限报告。
    """
    if budget_stop_reason(state) is StopReason.DEADLINE_EXCEEDED:
        return "generate_report"
    return "build_investigation_plan"


def route_after_judge(
    state: InvestigationState,
) -> Literal["refine_investigation", "generate_report"]:
    """Return only a predeclared graph node name.

    中文: 读取 judge 后的充分性、轮次和预算; 不写 State。模型不能通过自由文本选择路由。
    """
    if decide_after_judge(state).target is RouteTarget.REFINE:
        return "refine_investigation"
    return "generate_report"


def route_after_report(state: InvestigationState) -> Literal["human_review", "__end__"]:
    """Require review only when the generated report contains high-risk actions.

    中文: 只读取 final_report.remediation_steps; 不写 State。high/critical 建议进入真实
    ``interrupt`` 节点, 低风险报告可直接结束。
    """
    report = state["final_report"]
    if any(
        step.risk_level in {RiskLevel.HIGH, RiskLevel.CRITICAL} for step in report.remediation_steps
    ):
        return "human_review"
    return "__end__"
