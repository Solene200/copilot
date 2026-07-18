"""Task metadata and safe streaming event contracts."""

from datetime import UTC, datetime
from enum import StrEnum

from pydantic import Field, JsonValue

from incident_copilot.domain.common import AwareDatetime, DomainModel
from incident_copilot.domain.incident import IncidentContext
from incident_copilot.domain.report import IncidentReport
from incident_copilot.domain.review import HumanReviewRequest
from incident_copilot.graph.schemas import InvestigationOptions


class InvestigationStatus(StrEnum):
    """Externally observable lifecycle of one investigation task."""

    PENDING = "pending"
    RUNNING = "running"
    WAITING_REVIEW = "waiting_review"
    COMPLETED = "completed"
    FAILED = "failed"


class EventType(StrEnum):
    """Versioned event names safe for public SSE consumers."""

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
    """Application metadata; full graph state remains in the checkpointer."""

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
    """Monotonic, replayable event that never contains raw checkpoint state."""

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
