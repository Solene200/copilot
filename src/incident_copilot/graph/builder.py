"""LangGraph 构建器和基于 Send 的动态证据分发。

本模块把节点、边、动态并行分发和可选 Checkpointer 组装成可执行 Graph。
它只描述控制流, 节点业务在 ``nodes.py``, 停止策略在 ``routing.py``, 合并语义在
``state.py``。当前并行模型是多个 ``Send`` 指向同一个通用 ``collect_evidence`` 节点。
"""

from collections.abc import Hashable
from datetime import timedelta
from typing import Literal, overload

from langgraph.checkpoint.base import BaseCheckpointSaver
from langgraph.graph import END, START, StateGraph
from langgraph.graph.state import CompiledStateGraph
from langgraph.types import Send

from incident_copilot.core.clock import Clock, utc_now
from incident_copilot.domain.incident import IncidentContext
from incident_copilot.graph.model import ModelProvider
from incident_copilot.graph.nodes import InvestigationNodes
from incident_copilot.graph.routing import route_after_judge, route_after_parse, route_after_report
from incident_copilot.graph.schemas import (
    InvestigationOptions,
    InvestigationStep,
    ModelUsage,
)
from incident_copilot.graph.state import InvestigationState
from incident_copilot.tools.registry import ToolRegistry

# 已编译调查 Graph 的统一类型: 输入、输出和运行期间都使用 InvestigationState。
InvestigationGraph = CompiledStateGraph[
    InvestigationState, None, InvestigationState, InvestigationState
]


def create_initial_state(
    incident: IncidentContext,
    *,
    options: InvestigationOptions | None = None,
    clock: Clock = utc_now,
) -> InvestigationState:
    """使用校验后的应用输入创建一个边界完整的初始 State。

    写入 incident、空集合、研究轮次、四类预算和总 deadline。后续节点只能通过
    最小增量更新 State, 不能从模型输出覆盖这些策略字段。
    """
    if not incident.services:
        raise ValueError("investigation requires at least one service")
    # options 来自 API 请求或调用方; 未传时使用经过 Pydantic 校验的安全默认预算。
    policy = options or InvestigationOptions()
    # started_at 和 deadline_at 必须使用同一个注入时钟, 否则固定时钟测试会误判超时。
    started_at = clock()
    # 初始 State 显式写全累计字段的单位元, 让后续 Reducer 只处理节点返回的增量。
    return InvestigationState(
        incident=incident,
        pending_steps=(),
        completed_steps=(),
        evidence=(),
        hypotheses=(),
        evidence_sufficient=False,
        sufficiency_reason="not yet evaluated",
        next_investigation_queries=(),
        research_round=1,
        max_research_rounds=policy.max_research_rounds,
        max_tool_calls=policy.max_tool_calls,
        max_tool_attempts=policy.max_tool_attempts,
        tool_attempt_limits={},
        max_parallel_tools=policy.max_parallel_tools,
        tool_call_count=0,
        tool_attempt_count=0,
        tool_success_count=0,
        tool_failure_count=0,
        max_model_calls=policy.max_model_calls,
        model_call_count=0,
        max_estimated_tokens=policy.max_estimated_tokens,
        model_usage=ModelUsage(),
        started_at=started_at,
        deadline_at=started_at + timedelta(seconds=policy.timeout_seconds),
        deadline_exceeded=False,
        errors=(),
    )


def dispatch_evidence_collection(
    state: InvestigationState,
) -> list[Send] | Literal["aggregate_evidence", "generate_report"]:
    """在 fan-out 前预留预算,并并行发送每个步骤所需的最小 State。

    读取 stop_reason、pending/completed steps 和工具预算。返回 ``Send`` 列表时不
    直接写 State; LangGraph 会在同一 superstep 并行调度这些分支。
    """
    if state.get("stop_reason") is not None:
        return "generate_report"
    return _dispatch_batch(state, empty_target="aggregate_evidence")


def dispatch_after_aggregate(
    state: InvestigationState,
) -> list[Send] | Literal["generate_hypotheses", "generate_report"]:
    """执行下一有界批次,只在计划耗尽后离开证据收集阶段。

    aggregate 是每批并行任务的汇合屏障。若并发上限导致仍有未执行步骤,再发送
    一批; 只有计划耗尽后才进入假设生成。
    """
    if state.get("stop_reason") is not None:
        return "generate_report"
    return _dispatch_batch(state, empty_target="generate_hypotheses")


@overload
def _dispatch_batch(
    state: InvestigationState,
    *,
    empty_target: Literal["aggregate_evidence"],
) -> list[Send] | Literal["aggregate_evidence"]: ...


@overload
def _dispatch_batch(
    state: InvestigationState,
    *,
    empty_target: Literal["generate_hypotheses"],
) -> list[Send] | Literal["generate_hypotheses"]: ...


def _dispatch_batch(
    state: InvestigationState,
    *,
    empty_target: Literal["aggregate_evidence", "generate_hypotheses"],
) -> list[Send] | Literal["aggregate_evidence", "generate_hypotheses"]:
    """按剩余工具预算和并发上限选择下一批未执行查询。"""
    # 使用已完成查询键过滤重放,防止同一工具参数跨批次重复执行。
    remaining_steps = max(0, state["max_tool_calls"] - state.get("tool_call_count", 0))
    remaining_attempts = max(0, state["max_tool_attempts"] - state.get("tool_attempt_count", 0))
    # 在 fan-out 前计算批次大小,避免并行分支共同越过调查级工具预算。
    limit = min(remaining_steps, remaining_attempts, state["max_parallel_tools"])
    completed_queries = {item.query_key for item in state.get("completed_steps", ())}
    candidates = sorted(
        (
            step
            for step in state.get("pending_steps", ())
            if step.query_key not in completed_queries
        ),
        key=lambda step: (-step.priority, step.step_id),
    )
    selected: list[tuple[InvestigationStep, int]] = []
    for step in candidates[:limit]:
        # Registry 的重试上限按工具配置, Graph 还要用调查级剩余预算再次收紧。
        configured_limit = state.get("tool_attempt_limits", {}).get(step.tool_name, 1)
        attempt_allowance = min(configured_limit, remaining_attempts)
        if attempt_allowance <= 0:
            break
        # 在创建 Send 前预扣每个分支的最大尝试数, 避免并行分支各自看到同一份余额。
        selected.append((step, attempt_allowance))
        remaining_attempts -= attempt_allowance
    if not selected:
        return empty_target
    # 每个 Send 只携带单步执行所需字段,不复制完整证据和假设历史。
    return [
        Send(
            "collect_evidence",
            {
                "incident": state["incident"],
                "current_step": step,
                "current_step_attempt_limit": attempt_allowance,
                "deadline_at": state["deadline_at"],
            },
        )
        for step, attempt_allowance in selected
    ]


def build_investigation_graph(
    *,
    registry: ToolRegistry,
    model: ModelProvider,
    clock: Clock = utc_now,
    checkpointer: BaseCheckpointSaver[str] | None = None,
    require_human_review: bool = False,
) -> InvestigationGraph:
    """编译调查工作流,并按需加入可恢复的人工审核语义。

    注册节点和真实边,并把纯路由函数绑定为 conditional edges。启用人工审核时
    ``human_review`` 可以 interrupt; Graph 必须配合 Checkpointer 和稳定 thread_id 才能
    跨调用恢复。
    """
    nodes = InvestigationNodes(registry=registry, model=model, clock=clock)
    builder = StateGraph(InvestigationState)
    # 节点名称也是 streaming、Mermaid 和测试使用的稳定控制流标识。
    builder.add_node("parse_incident", nodes.parse_incident)
    builder.add_node("build_investigation_plan", nodes.build_investigation_plan)
    builder.add_node("collect_evidence", nodes.collect_evidence)
    builder.add_node("aggregate_evidence", nodes.aggregate_evidence)
    builder.add_node("generate_hypotheses", nodes.generate_hypotheses)
    builder.add_node("verify_hypotheses", nodes.verify_hypotheses)
    builder.add_node("judge_evidence", nodes.judge_evidence)
    builder.add_node("refine_investigation", nodes.refine_investigation)
    builder.add_node("generate_report", nodes.generate_report)
    if require_human_review:
        # destinations 只声明节点可能去向; 真正选择由 human_review 返回的 Command 决定。
        builder.add_node(
            "human_review",
            nodes.human_review,
            destinations=("refine_investigation", END),
        )

    builder.add_edge(START, "parse_incident")
    builder.add_conditional_edges(
        "parse_incident",
        route_after_parse,
        path_map={
            "build_investigation_plan": "build_investigation_plan",
            "generate_report": "generate_report",
        },
    )
    dispatch_targets: dict[Hashable, str] = {
        "collect_evidence": "collect_evidence",
        "aggregate_evidence": "aggregate_evidence",
        "generate_report": "generate_report",
    }
    builder.add_conditional_edges(
        "build_investigation_plan",
        dispatch_evidence_collection,
        path_map=dispatch_targets,
    )
    builder.add_edge("collect_evidence", "aggregate_evidence")
    # 所有并行 collect 分支在 aggregate 汇合,Reducer 先合并各分支 State 增量。
    builder.add_conditional_edges(
        "aggregate_evidence",
        dispatch_after_aggregate,
        path_map={
            "collect_evidence": "collect_evidence",
            "generate_hypotheses": "generate_hypotheses",
            "generate_report": "generate_report",
        },
    )
    builder.add_edge("generate_hypotheses", "verify_hypotheses")
    builder.add_edge("verify_hypotheses", "judge_evidence")
    route_targets: dict[Hashable, str] = {
        "refine_investigation": "refine_investigation",
        "generate_report": "generate_report",
    }
    builder.add_conditional_edges(
        "judge_evidence",
        route_after_judge,
        path_map=route_targets,
    )
    # refine 会生成下一轮增量计划, 随后复用与首轮完全相同的有界分发入口。
    builder.add_conditional_edges(
        "refine_investigation",
        dispatch_evidence_collection,
        path_map=dispatch_targets,
    )
    if require_human_review:
        # 是否需要审核由报告风险字段决定,不是让模型返回任意节点名称。
        builder.add_conditional_edges(
            "generate_report",
            route_after_report,
            path_map={"human_review": "human_review", "__end__": END},
        )
    else:
        builder.add_edge("generate_report", END)
    # Checkpointer 在 compile 时注入;具体 thread_id 在每次 invoke 的 config 中传入。
    return builder.compile(
        checkpointer=checkpointer,
        name=("incident-copilot-phase-5" if require_human_review else "incident-copilot-phase-4"),
    )
