"""LangGraph state channels and deterministic parallel reducers.

中文教学说明: State 是节点之间传递的有界数据契约。普通字段采用覆盖语义;
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

ItemT = TypeVar("ItemT", bound=BaseModel)


def _canonical_model(item: BaseModel) -> str:
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
        if current is None or (rank(item), _canonical_model(item)) < (
            rank(current),
            _canonical_model(current),
        ):
            merged[item_id] = item
    return tuple(
        sorted(
            merged.values(),
            key=lambda item: (rank(item), identity(item), _canonical_model(item)),
        )[:limit]
    )


def merge_evidence(
    left: Sequence[EvidenceRef], right: Sequence[EvidenceRef]
) -> tuple[EvidenceRef, ...]:
    """Union evidence by ID and retain a deterministic global top 100.

    中文: 读取两个分支的 EvidenceRef 增量, 按 evidence_id 去重并优先保留高相关、高可靠
    证据。State 只保存轻量引用, 不保存完整原始 payload。
    """
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
    """Make replayed step completion idempotent and ordering independent.

    中文: ``step_id`` 是幂等键。节点恢复或重复产生同一结果时不会重复累计执行记录。
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
    """Retain a deterministic bounded set of sanitized failures.

    中文: 错误也是调查输出的一部分, 但必须脱敏、去重并限制数量。
    """
    return _merge_bounded_by_id(
        left,
        right,
        identity=lambda item: item.error_id,
        rank=lambda item: (item.error_id,),
        limit=100,
    )


def add_count(left: int, right: int) -> int:
    """Combine per-branch counter deltas without read-modify-write races.

    中文: 并行节点只返回本分支增量 ``1``, reducer 负责求和。节点不能读取旧总数再写回,
    否则两个并行分支可能互相覆盖。
    """
    return left + right


def add_usage(left: ModelUsage, right: ModelUsage) -> ModelUsage:
    """Combine model usage deltas while preserving estimated provenance.

    中文: Token 数逐维相加; 任一来源为估算值时, 合并结果也必须保留 estimated 标记。
    """
    return ModelUsage(
        input_tokens=left.input_tokens + right.input_tokens,
        output_tokens=left.output_tokens + right.output_tokens,
        estimated=left.estimated or right.estimated,
    )


class InvestigationState(TypedDict, total=False):
    """Bounded graph channels; nodes emit only their minimal updates.

    中文: ``total=False`` 允许每个节点只返回自己负责的字段。没有 reducer 的字段会覆盖;
    ``completed_steps/evidence/errors`` 和计数、usage 字段则按上方 reducer 合并。
    """

    incident: IncidentContext
    investigation_plan: InvestigationPlan
    pending_steps: tuple[InvestigationStep, ...]
    current_step: InvestigationStep
    completed_steps: Annotated[tuple[StepResult, ...], merge_step_results]
    evidence: Annotated[tuple[EvidenceRef, ...], merge_evidence]
    hypotheses: tuple[Hypothesis, ...]
    evidence_sufficient: bool
    sufficiency_reason: str
    next_investigation_queries: tuple[VerificationQuery, ...]
    research_round: int
    max_research_rounds: int
    max_tool_calls: int
    max_parallel_tools: int
    tool_call_count: Annotated[int, add_count]
    tool_success_count: Annotated[int, add_count]
    tool_failure_count: Annotated[int, add_count]
    max_model_calls: int
    model_call_count: Annotated[int, add_count]
    max_estimated_tokens: int
    model_usage: Annotated[ModelUsage, add_usage]
    started_at: datetime
    deadline_at: datetime
    deadline_exceeded: bool
    errors: Annotated[tuple[InvestigationError, ...], merge_errors]
    stop_reason: StopReason | None
    final_report: IncidentReport
    human_feedback: HumanFeedback
    review_completed: bool
