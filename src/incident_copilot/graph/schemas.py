"""调查 Graph 节点之间交换的已校验值。"""

import hashlib
import json
from collections.abc import Mapping
from datetime import UTC, datetime
from enum import StrEnum
from typing import Literal, Self

from pydantic import Field, JsonValue, field_validator, model_validator

from incident_copilot.domain.common import (
    AwareDatetime,
    DomainModel,
    SourceType,
    unique_evidence_ids,
)
from incident_copilot.domain.hypothesis import Hypothesis, VerificationQuery
from incident_copilot.domain.review import HumanFeedback


def stable_query_key(tool_name: str, arguments: Mapping[str, object]) -> str:
    """在可信代码中计算查询标识,而不接受模型提供的标识。"""
    canonical = json.dumps(
        {"tool_name": tool_name, "arguments": arguments},
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


class StepStatus(StrEnum):
    """一个只读调查工具步骤的终止状态。"""

    COMPLETED = "completed"
    FAILED = "failed"
    SKIPPED = "skipped"


class ErrorCategory(StrEnum):
    """可以安全暴露在报告中的稳定 Graph 级错误类别。"""

    VALIDATION = "validation"
    TIMEOUT = "timeout"
    UNAVAILABLE = "unavailable"
    MALFORMED_RESPONSE = "malformed_response"
    BUDGET = "budget"
    INTERNAL = "internal"


class StopReason(StrEnum):
    """明确且可审计的调查循环结束原因。"""

    EVIDENCE_SUFFICIENT = "evidence_sufficient"
    MAX_RESEARCH_ROUNDS = "max_research_rounds"
    TOOL_BUDGET_EXHAUSTED = "tool_budget_exhausted"
    MODEL_BUDGET_EXHAUSTED = "model_budget_exhausted"
    TOKEN_BUDGET_EXHAUSTED = "token_budget_exhausted"
    DEADLINE_EXCEEDED = "deadline_exceeded"


class InvestigationOptions(DomainModel):
    """由应用代码控制且模型永远不能修改的不可变调用预算。"""

    max_research_rounds: int = Field(default=2, ge=1, le=5)
    max_tool_calls: int = Field(default=14, ge=1, le=100)
    max_parallel_tools: int = Field(default=7, ge=1, le=20)
    max_model_calls: int = Field(default=20, ge=1, le=50)
    max_estimated_tokens: int = Field(default=20_000, ge=1, le=1_000_000)
    timeout_seconds: float = Field(default=30.0, gt=0, le=300)


class ModelTask(StrEnum):
    """Phase 4 使用的白名单结构化模型操作。"""

    PLAN = "plan"
    HYPOTHESES = "hypotheses"
    JUDGE = "judge"
    REPORT = "report"


class InvestigationStep(DomainModel):
    """为一轮调查生成的已校验白名单工具请求。"""

    step_id: str = Field(pattern=r"^step_[A-Za-z0-9][A-Za-z0-9_-]{0,127}$")
    query_key: str = Field(pattern=r"^[a-f0-9]{64}$")
    tool_name: str = Field(pattern=r"^[a-z][a-z0-9_]{1,63}$")
    source_type: SourceType
    purpose: str = Field(min_length=1, max_length=1_000)
    arguments: dict[str, JsonValue]
    priority: int = Field(default=50, ge=1, le=100)
    round_number: int = Field(ge=1)


class InvestigationPlan(DomainModel):
    """步骤会由 Tool Registry 再次校验的有界计划。"""

    plan_id: str = Field(pattern=r"^plan_[A-Za-z0-9][A-Za-z0-9_-]{0,127}$")
    round_number: int = Field(ge=1)
    objective: str = Field(min_length=1, max_length=1_000)
    steps: tuple[InvestigationStep, ...] = Field(default_factory=tuple, max_length=20)
    coverage_targets: tuple[SourceType, ...] = Field(default_factory=tuple, max_length=6)
    rationale: str = Field(min_length=1, max_length=2_000)

    @field_validator("coverage_targets")
    @classmethod
    def unique_sources(cls, values: tuple[SourceType, ...]) -> tuple[SourceType, ...]:
        return tuple(dict.fromkeys(values))

    @model_validator(mode="after")
    def validate_steps(self) -> Self:
        if any(step.round_number != self.round_number for step in self.steps):
            raise ValueError("plan steps must belong to the plan round")
        step_ids = [step.step_id for step in self.steps]
        if len(step_ids) != len(set(step_ids)):
            raise ValueError("plan step ids must be unique")
        query_keys = [step.query_key for step in self.steps]
        if len(query_keys) != len(set(query_keys)):
            raise ValueError("plan queries must be unique")
        return self


class StepResult(DomainModel):
    """不包含原始证据载荷的工具步骤紧凑终止记录。"""

    step_id: str = Field(pattern=r"^step_[A-Za-z0-9][A-Za-z0-9_-]{0,127}$")
    query_key: str = Field(pattern=r"^[a-f0-9]{64}$")
    tool_name: str = Field(pattern=r"^[a-z][a-z0-9_]{1,63}$")
    arguments: dict[str, JsonValue] = Field(default_factory=dict, max_length=20)
    status: StepStatus
    evidence_ids: tuple[str, ...] = Field(default_factory=tuple, max_length=50)
    error_id: str | None = Field(default=None, pattern=r"^err_[A-Za-z0-9][A-Za-z0-9_-]{0,127}$")
    attempts: int = Field(ge=0, le=10)
    started_at: AwareDatetime
    completed_at: AwareDatetime

    @field_validator("evidence_ids")
    @classmethod
    def validate_evidence_ids(cls, values: tuple[str, ...]) -> tuple[str, ...]:
        return unique_evidence_ids(values, field_name="step evidence ids")

    @model_validator(mode="after")
    def validate_outcome(self) -> Self:
        if self.completed_at < self.started_at:
            raise ValueError("step completion must not precede start")
        if self.status is StepStatus.FAILED and self.error_id is None:
            raise ValueError("failed step requires an error id")
        if self.status is StepStatus.COMPLETED and self.error_id is not None:
            raise ValueError("completed step must not reference an error")
        return self


class InvestigationError(DomainModel):
    """保存在有界 Graph State 中、经过脱敏的一等失败对象。"""

    error_id: str = Field(pattern=r"^err_[A-Za-z0-9][A-Za-z0-9_-]{0,127}$")
    category: ErrorCategory
    component: str = Field(min_length=1, max_length=128)
    operation: str = Field(min_length=1, max_length=128)
    message: str = Field(min_length=1, max_length=1_000)
    retryable: bool = False
    occurred_at: AwareDatetime = Field(default_factory=lambda: datetime.now(UTC))
    step_id: str | None = Field(default=None, pattern=r"^step_[A-Za-z0-9][A-Za-z0-9_-]{0,127}$")
    attempt: int = Field(default=1, ge=1, le=10)


class ModelUsage(DomainModel):
    """单次调用用量,Fake Model 数值会明确标记为估算值。"""

    input_tokens: int = Field(default=0, ge=0)
    output_tokens: int = Field(default=0, ge=0)
    estimated: bool = False


class ModelResponse(DomainModel):
    """需要使用任务专属 Schema 再次校验的不可信 Provider 响应。"""

    payload: dict[str, JsonValue]
    usage: ModelUsage = Field(default_factory=ModelUsage)


class PlanOutput(DomainModel):
    """初始或细化调查计划使用的结构化模型输出。"""

    objective: str = Field(min_length=1, max_length=1_000)
    steps: tuple[InvestigationStep, ...] = Field(default_factory=tuple, max_length=20)
    rationale: str = Field(min_length=1, max_length=2_000)


class HypothesesOutput(DomainModel):
    """包含有界可证伪假设的结构化模型输出。"""

    hypotheses: tuple[Hypothesis, ...] = Field(min_length=1, max_length=10)


class SufficiencyOutput(DomainModel):
    """结构化模型判断,最终路由仍由代码策略控制。"""

    sufficient: bool
    reason: str = Field(min_length=1, max_length=2_000)
    next_queries: tuple[VerificationQuery, ...] = Field(default_factory=tuple, max_length=10)


class ReportDraftOutput(DomainModel):
    """仅包含叙事的报告输出,由代码附加已验证 Evidence 引用。"""

    summary: str = Field(min_length=1, max_length=4_000)
    root_cause: str | None = Field(default=None, max_length=4_000)
    confidence_rationale: str = Field(min_length=1, max_length=2_000)
    remediation_actions: tuple[str, ...] = Field(min_length=1, max_length=10)
    risks: tuple[str, ...] = Field(default_factory=tuple, max_length=10)


class ModelContext(DomainModel):
    """传给模型 Provider 的有界证据包。"""

    task: ModelTask
    incident_id: str = Field(pattern=r"^inc_[A-Za-z0-9][A-Za-z0-9_-]{0,127}$")
    service: str = Field(min_length=1, max_length=128)
    raw_query: str = Field(min_length=1, max_length=10_000)
    start_time: AwareDatetime
    end_time: AwareDatetime
    research_round: int = Field(ge=1)
    evidence_summaries: tuple[dict[str, JsonValue], ...] = Field(
        default_factory=tuple, max_length=100
    )
    hypotheses: tuple[Hypothesis, ...] = Field(default_factory=tuple, max_length=10)
    next_investigation_queries: tuple[VerificationQuery, ...] = Field(
        default_factory=tuple, max_length=10
    )
    human_feedback: HumanFeedback | None = None
    error_count: int = Field(default=0, ge=0)


class RouteTarget(StrEnum):
    """判断后路由唯一允许选择的目标。"""

    REFINE = "refine_investigation"
    REPORT = "generate_report"


ReportStatus = Literal["complete", "limited"]
