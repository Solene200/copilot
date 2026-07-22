"""LangGraph State 通道和确定性并行 Reducer。

State 是节点之间传递的有界数据契约。普通字段采用覆盖语义;
``Annotated`` 字段绑定 reducer, 用于合并同一 superstep 中多个 ``Send`` 分支的增量。
Reducer 必须尽量满足交换律、结合律和幂等性, 否则并行完成顺序或 checkpoint 重放会
改变最终结果。
"""

import json
from collections.abc import Callable, Sequence
from datetime import datetime
from typing import Annotated, TypeVar

from pydantic import BaseModel
from typing_extensions import TypedDict

from incident_copilot.domain.evidence import EvidenceRef
from incident_copilot.domain.hypothesis import Hypothesis, VerificationQuery
from incident_copilot.domain.incident import IncidentContext
from incident_copilot.domain.report import IncidentReport
from incident_copilot.domain.review import HumanFeedback
from incident_copilot.graph.schemas import (
    InvestigationError,
    InvestigationPlan,
    InvestigationStep,
    ModelUsage,
    StepResult,
    StopReason,
)

# Reducer 处理的 Pydantic 模型泛型类型。
ItemT = TypeVar("ItemT", bound=BaseModel)


def _canonical_model(item: BaseModel) -> str:
    # 规范 JSON 为内容相同的模型提供稳定比较键, 不受字典插入顺序影响。
    return json.dumps(
        item.model_dump(mode="json"),
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    )


def _merge_bounded_by_id(
    left: Sequence[ItemT],
    right: Sequence[ItemT],
    *,
    identity: Callable[[ItemT], str],
    rank: Callable[[ItemT], tuple[object, ...]],
    limit: int,
) -> tuple[ItemT, ...]:
    """按稳定 ID 合并模型, 解决冲突后执行确定性排序和上限裁剪。

    同一个 ID 若出现不同载荷, 使用 rank 和规范 JSON 选择固定胜者。这样 ``left/right``
    调换顺序时仍得到相同结果, 对并行 reducer 和 checkpoint 重放非常重要。
    """
    merged: dict[str, ItemT] = {}
    for item in (*left, *right):
        item_id = identity(item)
        current = merged.get(item_id)
        # 同一 ID 载荷冲突时不采用“后写覆盖”, 而是选固定排序键更小的版本。
        if current is None or (rank(item), _canonical_model(item)) < (
            rank(current),
            _canonical_model(current),
        ):
            merged[item_id] = item
    # 冲突解决后再次稳定排序并裁剪, 从而让并行完成顺序不影响最终 State。
    return tuple(
        sorted(
            merged.values(),
            key=lambda item: (rank(item), identity(item), _canonical_model(item)),
        )[:limit]
    )


def merge_evidence(
    left: Sequence[EvidenceRef], right: Sequence[EvidenceRef]
) -> tuple[EvidenceRef, ...]:
    """按 ID 合并证据,并确定性保留全局前 100 条。

    读取两个分支的 EvidenceRef 增量,按 evidence_id 去重并优先保留高相关、高可靠
    证据。State 只保存轻量引用, 不保存完整原始 payload。
    """
    # 相关性和可靠性越高 rank 越小, 超过上限时优先留下最有用的轻量引用。
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
    """让重放的步骤结果保持幂等,并且不受完成顺序影响。

    ``step_id`` 是幂等键。节点恢复或重复产生同一结果时不会重复累计执行记录。
    """
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
    """确定性保留有界且已经脱敏的错误集合。

    错误也是调查输出的一部分,但必须脱敏、去重并限制数量。
    """
    return _merge_bounded_by_id(
        left,
        right,
        identity=lambda item: item.error_id,
        rank=lambda item: (item.error_id,),
        limit=100,
    )


def add_count(left: int, right: int) -> int:
    """合并各分支的计数增量,避免“读取后修改再写入”的竞态。

    并行节点只返回本分支增量 ``1``,Reducer 负责求和。节点不能读取旧总数再写回,
    否则两个并行分支可能互相覆盖。
    """
    return left + right


def add_usage(left: ModelUsage, right: ModelUsage) -> ModelUsage:
    """合并模型用量增量,并保留用量是否为估算值的信息。

    Token 数逐维相加;任一来源为估算值时,合并结果也必须保留 estimated 标记。
    """
    return ModelUsage(
        input_tokens=left.input_tokens + right.input_tokens,
        output_tokens=left.output_tokens + right.output_tokens,
        estimated=left.estimated or right.estimated,
    )


class InvestigationState(TypedDict, total=False):
    """定义有界 Graph 通道,各节点只返回最小更新。

    ``total=False`` 允许每个节点只返回自己负责的字段。没有 Reducer 的字段会覆盖;
    ``completed_steps/evidence/errors`` 和计数、usage 字段则按上方 reducer 合并。
    """

    # 规范化后的故障输入, 是所有调查节点的共同起点。
    incident: IncidentContext
    # 当前研究轮次正在执行的调查计划。
    investigation_plan: InvestigationPlan
    # 本轮尚未调度完成的工具步骤。
    pending_steps: tuple[InvestigationStep, ...]
    # Send 分支当前负责执行的单个工具步骤。
    current_step: InvestigationStep
    # 当前 Send 分支获准消耗的物理工具尝试数。
    current_step_attempt_limit: int
    # 已终止步骤的有界集合, 并行分支按 step_id 合并。
    completed_steps: Annotated[tuple[StepResult, ...], merge_step_results]
    # 已收集的轻量证据引用, 并行分支按 evidence_id 合并。
    evidence: Annotated[tuple[EvidenceRef, ...], merge_evidence]
    # 当前版本的根因假设集合, 验证节点会整体替换。
    hypotheses: tuple[Hypothesis, ...]
    # 当前证据是否足够支撑报告。
    evidence_sufficient: bool
    # 证据充分性判断的解释。
    sufficiency_reason: str
    # 下一轮需要验证的问题集合。
    next_investigation_queries: tuple[VerificationQuery, ...]
    # 当前研究轮次, 从 1 开始。
    research_round: int
    # 允许执行的最大研究轮数。
    max_research_rounds: int
    # 整次调查允许的逻辑工具步骤总数。
    max_tool_calls: int
    # 整次调查允许的物理工具尝试总数,包含重试。
    max_tool_attempts: int
    # 可信 Registry 为每个工具声明的单逻辑步骤最大物理尝试数。
    tool_attempt_limits: dict[str, int]
    # 一个批次允许并行执行的最大工具数。
    max_parallel_tools: int
    # 逻辑工具步骤累计增量, 并行分支通过 add_count 求和。
    tool_call_count: Annotated[int, add_count]
    # 物理工具尝试累计增量, 并行分支通过 add_count 求和。
    tool_attempt_count: Annotated[int, add_count]
    # 成功工具调用累计增量。
    tool_success_count: Annotated[int, add_count]
    # 失败工具调用累计增量。
    tool_failure_count: Annotated[int, add_count]
    # 整次调查允许的模型调用总数。
    max_model_calls: int
    # 模型调用累计增量。
    model_call_count: Annotated[int, add_count]
    # 整次调查允许的输入输出 Token 估算总数。
    max_estimated_tokens: int
    # 模型 Token 用量累计值, 并行更新通过 add_usage 合并。
    model_usage: Annotated[ModelUsage, add_usage]
    # Graph 调查开始时间。
    started_at: datetime
    # 整次调查必须停止的绝对截止时间。
    deadline_at: datetime
    # 是否已经检测到超过截止时间。
    deadline_exceeded: bool
    # 已脱敏错误的有界集合, 并行分支按 error_id 合并。
    errors: Annotated[tuple[InvestigationError, ...], merge_errors]
    # 调查循环最终停止的明确原因。
    stop_reason: StopReason | None
    # 报告节点生成的最终结构化故障报告。
    final_report: IncidentReport
    # 人工恢复时提交的严格反馈载荷。
    human_feedback: HumanFeedback
    # 人工审核是否已经接受并完成。
    review_completed: bool
