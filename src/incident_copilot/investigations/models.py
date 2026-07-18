"""任务元数据和安全流式事件契约。"""

from datetime import UTC, datetime
from enum import StrEnum

from pydantic import Field, JsonValue

from incident_copilot.domain.common import AwareDatetime, DomainModel
from incident_copilot.domain.incident import IncidentContext
from incident_copilot.domain.report import IncidentReport
from incident_copilot.domain.review import HumanReviewRequest
from incident_copilot.graph.schemas import InvestigationOptions


class InvestigationStatus(StrEnum):
    """一个调查任务对外可观察的生命周期。"""

    PENDING = "pending"
    RUNNING = "running"
    WAITING_REVIEW = "waiting_review"
    COMPLETED = "completed"
    FAILED = "failed"


class EventType(StrEnum):
    """可安全提供给公开 SSE 消费者的版本化事件名称。"""

    INVESTIGATION_QUEUED = "investigation.queued"
    INVESTIGATION_STARTED = "investigation.started"
    NODE_COMPLETED = "node.completed"
    TOOL_COMPLETED = "tool.completed"
    TOOL_FAILED = "tool.failed"
    EVIDENCE_ADDED = "evidence.added"
    HYPOTHESIS_UPDATED = "hypothesis.updated"
    BUDGET_UPDATED = "budget.updated"
    REVIEW_REQUIRED = "review.required"
    REPORT_COMPLETED = "report.completed"
    INVESTIGATION_FAILED = "investigation.failed"


class InvestigationRecord(DomainModel):
    """应用元数据,完整 Graph State 仍保存在 Checkpointer 中。"""

    investigation_id: str = Field(pattern=r"^inv_[a-f0-9]{32}$")
    incident_id: str = Field(pattern=r"^inc_[A-Za-z0-9][A-Za-z0-9_-]{0,127}$")
    thread_id: str = Field(pattern=r"^thread_[a-f0-9]{32}$")
    run_id: str = Field(pattern=r"^run_[a-f0-9]{32}$")
    status: InvestigationStatus = InvestigationStatus.PENDING
    incident: IncidentContext
    options: InvestigationOptions
    request_fingerprint: str = Field(pattern=r"^[a-f0-9]{64}$")
    idempotency_key: str | None = Field(default=None, min_length=1, max_length=128)
    report: IncidentReport | None = None
    review_request: HumanReviewRequest | None = None
    error_message: str | None = Field(default=None, max_length=500)
    created_at: AwareDatetime = Field(default_factory=lambda: datetime.now(UTC))
    updated_at: AwareDatetime = Field(default_factory=lambda: datetime.now(UTC))
    version: int = Field(default=1, ge=1)


class InvestigationEvent(DomainModel):
    """单调递增且可重放、绝不包含 checkpoint 原始 State 的事件。"""

    schema_version: str = "1.0"
    event_id: str = Field(pattern=r"^evt_[a-f0-9]{32}_[0-9]+$")
    sequence: int = Field(ge=1)
    event_type: EventType
    investigation_id: str = Field(pattern=r"^inv_[a-f0-9]{32}$")
    incident_id: str = Field(pattern=r"^inc_[A-Za-z0-9][A-Za-z0-9_-]{0,127}$")
    thread_id: str = Field(pattern=r"^thread_[a-f0-9]{32}$")
    run_id: str = Field(pattern=r"^run_[a-f0-9]{32}$")
    occurred_at: AwareDatetime = Field(default_factory=lambda: datetime.now(UTC))
    data: dict[str, JsonValue] = Field(default_factory=dict)
